"""TensorBoard + checkpointing utilities.

`Logger` writes scalar curves and optionally the latest model checkpoint via
torch.utils.tensorboard (nested under torch._tensorboard in some versions).
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import torch

try:  # torch >= 2.x location
    from torch.utils.tensorboard import SummaryWriter  # type: ignore
except Exception:  # pragma: no cover
    try:
        from torch._tensorboard import SummaryWriter  # type: ignore
    except Exception:  # pragma: no cover
        SummaryWriter = None  # type: ignore


class Logger:
    """Small wrapper around TensorBoard and checkpoint saving."""

    def __init__(self, run_dir: str, enabled: bool = True):
        self.run_dir = run_dir
        self.enabled = enabled
        os.makedirs(run_dir, exist_ok=True)
        self.writer = SummaryWriter(run_dir) if enabled and SummaryWriter else None
        self.global_step = 0

    @property
    def ckpt_dir(self) -> str:
        d = os.path.join(self.run_dir, "checkpoints")
        os.makedirs(d, exist_ok=True)
        return d

    def log_scalars(self, tag: str, values: Dict[str, float], step: Optional[int] = None) -> None:
        if not self.enabled or self.writer is None:
            return
        if step is None:
            step = self.global_step
        for k, v in values.items():
            self.writer.add_scalar(f"{tag}/{k}", v, step)

    def save_checkpoint(
        self,
        model: torch.nn.Module,
        optimizer: torch.optim.Optimizer,
        curriculum_state: Optional[Dict] = None,
        step: Optional[int] = None,
    ) -> str:
        """Write a checkpoint; returns the path."""
        step = step if step is not None else self.global_step
        path = os.path.join(self.ckpt_dir, f"ckpt_{step}.pt")
        torch.save(
            {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "curriculum": curriculum_state,
                "global_step": step,
            },
            path,
        )
        return path

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
