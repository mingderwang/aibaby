"""PPO (Proximal Policy Optimization) training for the AI Baby.

Implements a standard clipped PPO with:
  - Generalized Advantage Estimation (GAE)
  - An advantage normalization-based return and value loss
  - Policy entropy bonus to encourage exploration
  - Optional high-noise exploration at the start of training
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Tuple

import numpy as np
import torch
import torch.nn.functional as F
import torch.optim as optim

from aibaby.env.world import NUM_ACTIONS, VirtualWorld
from aibaby.models.transformer import BabyTransformer


@dataclass
class PPOHyperparams:
    gamma: float = 0.99
    lam: float = 0.95
    clip_epsilon: float = 0.2
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    lr: float = 3e-4
    max_grad_norm: float = 0.5
    num_epochs: int = 4
    batch_size: int = 256
    max_steps_per_iter: int = 2048
    exploration_noise: float = 0.1  # extra uniform prob during early training


class RolloutBuffer:
    """Sliding buffer of collected transitions for one PPO update."""

    def __init__(self, max_steps: int, obs_dim: int):
        self.max_steps = max_steps
        self.obs = np.zeros((max_steps, obs_dim), dtype=np.float32)
        self.actions = np.zeros(max_steps, dtype=np.int64)
        self.logprobs = np.zeros(max_steps, dtype=np.float32)
        self.rewards = np.zeros(max_steps, dtype=np.float32)
        self.dones = np.zeros(max_steps, dtype=np.float32)
        self.values = np.zeros(max_steps, dtype=np.float32)
        self.ptr = 0
        self.full = False

    def store(
        self,
        obs: np.ndarray,
        action: int,
        logprob: float,
        reward: float,
        done: bool,
        value: float,
    ) -> None:
        i = self.ptr
        self.obs[i] = obs
        self.actions[i] = action
        self.logprobs[i] = logprob
        self.rewards[i] = reward
        self.dones[i] = 1.0 if done else 0.0
        self.values[i] = value
        self.ptr = (self.ptr + 1) % self.max_steps
        if self.ptr == 0:
            self.full = True

    def __len__(self) -> int:
        return self.max_steps if self.full else self.ptr

    def to_torch(self) -> Tuple[torch.Tensor, ...]:
        n = len(self)
        return (
            torch.from_numpy(self.obs[:n]),
            torch.from_numpy(self.actions[:n]),
            torch.from_numpy(self.logprobs[:n]),
            torch.from_numpy(self.rewards[:n]),
            torch.from_numpy(self.dones[:n]),
            torch.from_numpy(self.values[:n]),
        )


class PPOAgent:
    """PPO trainer wrapping a BabyTransformer actor-critic."""

    def __init__(
        self,
        model: BabyTransformer,
        hp: PPOHyperparams,
    ):
        self.model = model
        self.hp = hp
        self.optimizer = optim.Adam(model.parameters(), lr=hp.lr)
        self.device = next(model.parameters()).device

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #
    @torch.no_grad()
    def act(self, obs: np.ndarray) -> Tuple[int, float, float]:
        """Return (action, log_prob, value) from a single observation."""
        x = torch.from_numpy(obs.astype(np.float32)).unsqueeze(0).to(self.device)
        logits, value = self.model(x)
        noise = self.hp.exploration_noise
        if noise > 0:
            # Mix with uniform distribution to encourage broad exploration.
            probs = F.softmax(logits, dim=-1)
            probs = (1 - noise) * probs + (noise / NUM_ACTIONS) * torch.ones_like(probs)
            dist = torch.distributions.Categorical(probs=probs)
        else:
            dist = torch.distributions.Categorical(logits=logits)
        action = dist.sample()
        return (
            int(action.item()),
            dist.log_prob(action).item(),
            float(value.item()),
        )

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #
    def update(self, buffer: RolloutBuffer) -> dict:
        """Run one PPO update over the collected rollout and return stats."""
        hp = self.hp
        obs, actions, old_logprobs, rewards, dones, values = buffer.to_torch()
        obs = obs.to(self.device)
        actions = actions.to(self.device)
        old_logprobs = old_logprobs.to(self.device)
        values = values.to(self.device)

        # Compute GAE advantages.
        rewards_c = rewards.to(self.device)
        dones_c = dones.to(self.device)

        advantages = self._compute_gae(rewards_c, dones_c, values, hp.gamma, hp.lam)
        returns = advantages + values.detach()
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        old_logprobs = old_logprobs.detach()
        n = obs.shape[0]
        indices = np.arange(n)

        total_policy = 0.0
        total_value = 0.0
        total_entropy = 0.0
        num_batches = 0

        for _ in range(hp.num_epochs):
            np.random.shuffle(indices)
            for start in range(0, n, hp.batch_size):
                idx = indices[start : start + hp.batch_size]
                idx_t = torch.from_numpy(idx).to(self.device)

                batch_obs = obs[idx_t]
                batch_actions = actions[idx_t]
                batch_old_logprobs = old_logprobs[idx_t]
                batch_advantages = advantages[idx_t]
                batch_returns = returns[idx_t]

                logits, value = self.model(batch_obs)
                dist = torch.distributions.Categorical(logits=logits)
                logprobs = dist.log_prob(batch_actions)
                entropy = dist.entropy().mean()

                ratio = torch.exp(logprobs - batch_old_logprobs)
                surr1 = ratio * batch_advantages
                surr2 = (
                    torch.clamp(ratio, 1 - hp.clip_epsilon, 1 + hp.clip_epsilon)
                    * batch_advantages
                )
                policy_loss = -torch.min(surr1, surr2).mean()

                value_loss = F.mse_loss(value.squeeze(-1), batch_returns)
                loss = (
                    policy_loss
                    + hp.value_coef * value_loss
                    - hp.entropy_coef * entropy
                )

                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), hp.max_grad_norm)
                self.optimizer.step()

                total_policy += policy_loss.item()
                total_value += value_loss.item()
                total_entropy += entropy.item()
                num_batches += 1

        return {
            "rollout_length": n,
            "policy_loss": total_policy / max(num_batches, 1),
            "value_loss": total_value / max(num_batches, 1),
            "entropy": total_entropy / max(num_batches, 1),
        }

    @staticmethod
    def _compute_gae(
        rewards: torch.Tensor,
        dones: torch.Tensor,
        values: torch.Tensor,
        gamma: float,
        lam: float,
    ) -> torch.Tensor:
        """GAE over a fixed-length rollout (bootstrap not needed here)."""
        n = rewards.shape[0]
        advantages = torch.zeros_like(rewards)
        gae = 0.0
        next_value = 0.0
        for t in reversed(range(n)):
            if t == n - 1:
                # No value bootstrap at the end of a buffer; treat terminal naturally.
                delta = rewards[t] + gamma * next_value * (1 - dones[t]) - values[t]
            else:
                delta = rewards[t] + gamma * values[t + 1] * (1 - dones[t]) - values[t]
            gae = delta + gamma * lam * (1 - dones[t]) * gae
            advantages[t] = gae
        return advantages


def collect_rollout(
    world: VirtualWorld,
    agent: PPOAgent,
    buffer: RolloutBuffer,
) -> dict:
    """Collect transitions into the buffer; return aggregate info for logging."""
    obs = world.observation()
    total_reward = 0.0
    steps = 0
    episodes = 0
    done = False

    # Reset the world so each rollout starts from scratch.
    obs = world.reset()

    for _ in range(buffer.max_steps):
        action, logprob, value = agent.act(obs)
        next_obs, reward, done, info = world.step(action)
        buffer.store(obs, action, logprob, reward, done, value)
        total_reward += reward
        steps += 1
        obs = next_obs
        if done:
            episodes += 1
            obs = world.reset()

    return {
        "episode_reward": total_reward / max(episodes, 1),
        "avg_energy": world.energy,
        "episodes": episodes,
    }
