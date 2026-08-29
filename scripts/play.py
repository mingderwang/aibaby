#!/usr/bin/env python3
"""Play one episode with a trained checkpoint (or random agent) and print grid.

Usage:
    python -m aibaby.scripts.play --checkpoint runs/baby/checkpoints/ckpt_N.pt
    python -m aibaby.scripts.play --random
"""

from __future__ import annotations

import argparse

import numpy as np
import torch
import yaml

from aibaby.env.world import VirtualWorld, WorldConfig


def load_model(checkpoint: str, world: VirtualWorld, device: torch.device):
    from aibaby.models.transformer import BabyTransformer

    model = BabyTransformer(obs_dim=world.observation_size, num_actions=5).to(device)
    ckpt = torch.load(checkpoint, map_location=device)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model, ckpt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default=None, help="path to a .pt checkpoint")
    ap.add_argument("--random", action="store_true", help="use a random agent instead")
    ap.add_argument("--config", default="aibaby/configs/default.yaml")
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=40)
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    world_cfg = WorldConfig(**cfg["world"])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = None
    if not args.random:
        if args.checkpoint is None:
            raise SystemExit("provide --checkpoint or --random")
        world = VirtualWorld(world_cfg)
        model, ckpt = load_model(args.checkpoint, world, device)

    for ep in range(args.episodes):
        world = VirtualWorld(world_cfg, seed=ep)
        obs = world.reset()
        total_reward = 0.0
        state = world.config.max_steps if args.random else args.max_steps
        limit = state
        print(f"--- episode {ep} ---")
        for t in range(limit):
            if model is not None:
                with torch.no_grad():
                    x = torch.from_numpy(obs.astype(np.float32)).unsqueeze(0).to(device)
                    logits, _ = model(x)
                action = int(logits.argmax(-1).item())
            else:
                action = int(np.random.randint(5))
            obs, reward, done, info = world.step(action)
            total_reward += reward
            if t % 10 == 0:
                print(world.render_text())
                print(f"step {t} energy={world.energy:.1f} reward_so_far={total_reward:.2f}")
            if done:
                print(f"[done] after {t+1} steps, reward={total_reward:.2f}")
                break
        print(f"total reward: {total_reward:.3f}")


if __name__ == "__main__":
    main()
