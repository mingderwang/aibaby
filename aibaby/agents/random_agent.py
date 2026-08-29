"""Random baseline agent - uniform random action selection.

Used as a control in evaluation to show how much the trained policy improves
over a purely random policy. No learning, no model.
"""

from __future__ import annotations

import numpy as np

from aibaby.env.world import NUM_ACTIONS


class RandomAgent:
    """Acts uniformly at random."""

    name = "random"

    def __init__(self, seed: int = 0):
        self.rng = np.random.RandomState(seed)

    def act(self, obs: np.ndarray) -> int:
        return int(self.rng.randint(NUM_ACTIONS))

    def reset(self) -> None:
        pass
