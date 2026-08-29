"""Evaluation utilities.

Runs a fixed number of episodes for a given policy (trained or random) and
reports aggregate metrics, optionally comparing against the random baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from aibaby.env.world import VirtualWorld, WorldConfig
from aibaby.models.transformer import BabyTransformer


@dataclass
class EvalResult:
    name: str
    avg_reward: float
    avg_energy: float
    success_rate: float  # fraction of episodes that reached max_steps
    avg_steps: float
    food_eaten_total: int
    hazard_hit_total: int
    rewards: List[float] = field(default_factory=list)


def evaluate_policy(
    act_fn: Callable[[np.ndarray], int],
    num_episodes: int = 50,
    world_config: Optional[WorldConfig] = None,
    name: str = "policy",
    seed: int = 0,
) -> EvalResult:
    """Evaluate a policy `act_fn: obs -> action` over `num_episodes` episodes.

    A fresh world layout is generated per episode (stable RNG per deterministic
    result), but the agent is never trained here.
    """
    rewards = []
    energies = []
    successes = 0
    steps = []
    food_total = 0
    hazard_total = 0

    rng = np.random.RandomState(seed)

    for ep in range(num_episodes):
        world = VirtualWorld(world_config, seed=rng.randint(0, 2**31))
        obs = world.reset()
        ep_reward = 0.0
        ep_steps = 0
        succeeded = False
        while True:
            action = act_fn(obs)
            obs, reward, done, info = world.step(action)
            ep_reward += reward
            ep_steps += 1
            food_total += info["food_eaten"]
            hazard_total += info["hazard_hit"]
            if done:
                if info.get("timeout", False):
                    succeeded = True
                break
        rewards.append(ep_reward)
        energies.append(world.energy)
        steps.append(ep_steps)
        successes += int(succeeded)

    return EvalResult(
        name=name,
        avg_reward=float(np.mean(rewards)),
        avg_energy=float(np.mean(energies)),
        success_rate=successes / max(num_episodes, 1),
        avg_steps=float(np.mean(steps)),
        food_eaten_total=food_total,
        hazard_hit_total=hazard_total,
        rewards=rewards,
    )


def compare_with_random(
    policy_act: Callable[[np.ndarray], int],
    model_obs_dim: int,
    num_episodes: int = 50,
    world_config: Optional[WorldConfig] = None,
    baseline_seed: int = 0,
) -> Dict[str, EvalResult]:
    """Evaluate trained vs random baseline on identical world configs."""
    from aibaby.agents.random_agent import RandomAgent

    random = RandomAgent(seed=baseline_seed)

    result = {}
    result["trained"] = evaluate_policy(policy_act, num_episodes, world_config, "trained")
    result["random"] = evaluate_policy(random.act, num_episodes, world_config, "random", baseline_seed)
    return result
