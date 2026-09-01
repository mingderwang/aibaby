"""GRPO (Group Relative Policy Optimization) training for the AI Baby.

Implements the group-relative policy optimization scheme from DeepSeekMath
(GRPO), adapted to a sequential control MDP where a single trained policy
samples a whole trajectory rather than one answer per prompt.

Key idea (no critic): for the same task/start state, sample a *group* of
`group_size` complete trajectories from the policy, compute each trajectory's
total return, then normalise the returns *within the group* to get relative
advantages:

    adv_j = (R_j - mean(R)) / (std(R) + eps)

Every step of trajectory j is weighted by adv_j in the policy-gradient update.
This removes the learned value function (and its bias) entirely, replacing it
with group-relative comparison -- exactly RLOO-style / GRPO relative reward.

The world layout is reset with the same seed for every group member so all
trajectories start from an identical layout and only the policy's actions
differ.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
import torch.optim as optim

from aibaby.env.world import NUM_ACTIONS, VirtualWorld
from aibaby.models.transformer import BabyTransformer


class GRPOHyperparams:
    """Hyperparameters for the GRPO trainer (no critic needed)."""

    def __init__(
        self,
        entropy_coef: float = 0.01,
        lr: float = 1e-4,
        max_grad_norm: float = 0.5,
        num_epochs: int = 4,
        batch_size: int = 256,
        group_size: int = 8,
        max_steps_per_episode: int = 200,
        exploration_noise: float = 0.0,
    ):
        self.entropy_coef = entropy_coef
        self.lr = lr
        self.max_grad_norm = max_grad_norm
        self.num_epochs = num_epochs
        self.batch_size = batch_size
        self.group_size = group_size
        self.max_steps_per_episode = max_steps_per_episode
        self.exploration_noise = exploration_noise


class GRPOTrajectory:
    """One sampled trajectory: a list of (obs, action, logprob) plus its return."""

    __slots__ = ("obs", "actions", "logprobs", "return_")

    def __init__(self):
        self.obs: list[np.ndarray] = []
        self.actions: list[int] = []
        self.logprobs: list[float] = []
        self.return_: float = 0.0


def _logits_to_dist(logits: torch.Tensor, noise: float):
    """Build a categorical distribution, optionally mixing with uniform noise."""
    if noise > 0:
        probs = F.softmax(logits, dim=-1)
        probs = (1 - noise) * probs + (noise / NUM_ACTIONS) * torch.ones_like(probs)
        return torch.distributions.Categorical(probs=probs)
    return torch.distributions.Categorical(logits=logits)


class GRPOAgent:
    """GRPO trainer wrapping a BabyTransformer (uses only the policy head)."""

    def __init__(self, model: BabyTransformer, hp: GRPOHyperparams):
        self.model = model
        self.hp = hp
        self.optimizer = optim.Adam(model.parameters(), lr=hp.lr)
        self.device = next(model.parameters()).device

    def act(self, obs: np.ndarray):
        """Sample one action for one obs; returns (action, logprob)."""
        x = (
            torch.from_numpy(obs.astype(np.float32))
            .unsqueeze(0)
            .to(self.device)
        )
        logits, _ = self.model(x)
        dist = _logits_to_dist(logits, self.hp.exploration_noise)
        action = dist.sample()
        return int(action.item()), dist.log_prob(action).item()

    def _rollout_one(self, world: VirtualWorld) -> GRPOTrajectory:
        """Roll out one full episode with the current policy."""
        traj = GRPOTrajectory()
        obs = world.reset()
        for _ in range(self.hp.max_steps_per_episode):
            action, logprob = self.act(obs)
            next_obs, reward, done, _ = world.step(action)
            traj.obs.append(obs)
            traj.actions.append(action)
            traj.logprobs.append(logprob)
            traj.return_ += reward
            obs = next_obs
            if done:
                break
        return traj

    def collect_groups(self, world_config) -> dict:
        """Sample a group of trajectories from an identical world layout.

        Returns a dict of flat arrays for the policy update.
        """
        hp = self.hp
        world = VirtualWorld(world_config)
        seed = world_config.seed
        start_obs = world.observation()

        trajectories = []
        for _ in range(hp.group_size):
            w = VirtualWorld(world_config, seed=seed)
            trajectories.append(self._rollout_one(w))

        # Group-relative advantage (normalise returns within the group).
        returns = np.array([t.return_ for t in trajectories], dtype=np.float32)
        std = returns.std()
        if std < 1e-8:
            adv = np.zeros_like(returns, dtype=np.float32)
        else:
            adv = (returns - returns.mean()) / (std + 1e-8)

        obs_list = []
        act_list = []
        logp_list = []
        adv_list = []
        for j, t in enumerate(trajectories):
            n = len(t.obs)
            obs_list.extend(t.obs)
            act_list.extend(t.actions)
            logp_list.extend(t.logprobs)
            adv_list.extend([adv[j]] * n)

        return {
            "obs": np.stack(obs_list).astype(np.float32),
            "actions": np.array(act_list, dtype=np.int64),
            "logprobs": np.array(logp_list, dtype=np.float32),
            "advantages": np.array(adv_list, dtype=np.float32),
            "returns": returns,
            "mean_return": float(returns.mean()),
            "std_return": float(std),
            "start_obs": start_obs,
            "num_traj": len(trajectories),
            "total_steps": len(obs_list),
        }

    def update(self, data: dict) -> dict:
        """One GRPO optimization over the collected group data."""
        hp = self.hp
        obs = torch.from_numpy(data["obs"]).to(self.device)
        actions = torch.from_numpy(data["actions"]).to(self.device)
        old_logprobs = torch.from_numpy(data["logprobs"]).to(self.device)
        advantages = torch.from_numpy(data["advantages"]).to(self.device)

        old_logprobs = old_logprobs.detach()
        n = obs.shape[0]
        indices = np.arange(n)

        total_policy = 0.0
        total_entropy = 0.0
        num_batches = 0

        for _ in range(hp.num_epochs):
            np.random.shuffle(indices)
            for start in range(0, n, hp.batch_size):
                idx = indices[start : start + hp.batch_size]
                idx_t = torch.from_numpy(idx).to(self.device)

                logits, _ = self.model(obs[idx_t])
                dist = torch.distributions.Categorical(logits=logits)
                logprobs = dist.log_prob(actions[idx_t])
                # Importance ratio for GRPO (no clipping of advantage used here;
                # policy clipping is what makes PPO-on-top-of-GRPO stable).
                ratio = torch.exp(logprobs - old_logprobs[idx_t])
                batch_adv = advantages[idx_t]

                # Policy gradient with a PPO-style clip for stability.
                surr1 = ratio * batch_adv
                surr2 = (
                    torch.clamp(ratio, 1 - 0.2, 1 + 0.2) * batch_adv
                )
                policy_loss = -torch.min(surr1, surr2).mean()
                entropy = dist.entropy().mean()

                loss = policy_loss - hp.entropy_coef * entropy

                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    self.model.parameters(), hp.max_grad_norm
                )
                self.optimizer.step()

                total_policy += policy_loss.item()
                total_entropy += entropy.item()
                num_batches += 1

        return {
            "policy_loss": total_policy / max(num_batches, 1),
            "entropy": total_entropy / max(num_batches, 1),
            "mean_return": data["mean_return"],
            "std_return": data["std_return"],
            "group_size": data["num_traj"],
            "total_steps": data["total_steps"],
        }
