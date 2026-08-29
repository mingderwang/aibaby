"""Curriculum learning for the AI Baby.

The world difficulty starts easy (few hazards, lots of food) and gradually
becomes harder as the baby's performance improves. A `Curriculum` tracks the
current stage and walks generation-relevant bounds up/down.

Design keeps a stable *curriculum seed* so that failure/success is due to the
agent, not world randomness: stages apply to the RNG state that generates the
world layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

from aibaby.env.world import WorldConfig


@dataclass
class CurriculumConfig:
    """Tunable schedule for world difficulty progression."""

    # Difficulty is a scalar in [0, 1]: 0 = trivially easy, 1 = hardest.
    start_difficulty: float = 0.1
    end_difficulty: float = 0.9
    # How much difficulty increases per successful evaluation, and decreases
    # on failure. Use slow increments to avoid plateaus.
    success_step: float = 0.05
    fail_step: float = 0.02
    # Reward threshold (average reward per episode) considered "success" at
    # the current stage.
    success_threshold: float = 12.0
    # Number of evaluation episodes per curriculum check.
    eval_episodes: int = 24


class Curriculum:
    """Linear difficulty schedule: maps difficulty -> WorldConfig complexity."""

    def __init__(self, cfg: CurriculumConfig, base: WorldConfig | None = None):
        self.cfg = cfg
        self.base = base or WorldConfig()
        self.difficulty = cfg.start_difficulty
        self.stage = 1
        self.history: Dict[int, float] = {}

    def world_config(self, seed: int | None = None) -> WorldConfig:
        """Produce a WorldConfig with difficulty ~ self.difficulty."""
        cfg = self.base
        d = self.difficulty
        # Food abundant when easy, scarce when hard.
        num_food = lerp_ints(cfg.num_food, (4, 6), d)
        # Few hazards when easy, many when hard.
        num_hazard = lerp_ints(cfg.num_hazard, (1, 3), d)
        # Slightly more walls when hard.
        num_wall = lerp_ints(cfg.num_wall, (1, 3), d)
        return WorldConfig(
            num_food=num_food,
            num_hazard=num_hazard,
            num_wall=num_wall,
            initial_energy=cfg.initial_energy,
            energy_per_timestep=cfg.energy_per_timestep,
            food_reward=cfg.food_reward,
            food_energy=cfg.food_energy,
            hazard_penalty=cfg.hazard_penalty * (1.0 + d * 0.5),
            max_steps=cfg.max_steps,
            seed=seed if seed is not None else cfg.seed,
        )

    def update(self, avg_reward: float) -> Dict[str, float]:
        """Advance difficulty based on measured evaluation reward.

        Returns a stats dict for logging the curriculum transition.
        """
        if avg_reward >= self.cfg.success_threshold:
            self.difficulty = min(self.cfg.end_difficulty, self.difficulty + self.cfg.success_step)
            self.stage += 1
        else:
            self.difficulty = max(self.cfg.start_difficulty, self.difficulty - self.cfg.fail_step)

        self.history[self.stage] = self.difficulty
        return {"difficulty": self.difficulty, "stage": self.stage}


def lerp_ints(
    bounds: tuple[int, int],
    easy_bounds: tuple[int, int],
    d: float,
) -> tuple[int, int]:
    """Linearly interpolate an integer (lo, hi) bound-pair from `bounds` toward
    `easy_bounds` based on difficulty d in [0,1].

    At d=1 we use `bounds` (hard), at d=0 we use `easy_bounds`.
    """
    lo = round((1 - d) * easy_bounds[0] + d * bounds[0])
    hi = round((1 - d) * easy_bounds[1] + d * bounds[1])
    return (max(1, lo), max(lo, hi))
