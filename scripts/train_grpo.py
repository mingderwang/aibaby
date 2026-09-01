#!/usr/bin/env python3
"""Entry point: train the AI Baby with GRPO + curriculum learning.

A GRPO (Group Relative Policy Optimization) alternative to the PPO trainer in
scripts/train.py. It uses no critic/value head: for each world layout it
samples a group of trajectories, normalises the returns within the group to
get relative advantages, and does a policy-only clipped update.

Usage:
    python -m aibaby.scripts.train_grpo --config aibaby/configs/default.yaml
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import torch
import yaml

from aibaby.algos.grpo import GRPOAgent, GRPOHyperparams
from aibaby.curriculum.curriculum import Curriculum, CurriculumConfig
from aibaby.dl.logging import Logger
from aibaby.env.world import VirtualWorld, WorldConfig
from aibaby.evaluation.evaluate import compare_with_random
from aibaby.models.transformer import BabyTransformer, count_parameters


def _device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build(cfg: dict, world: VirtualWorld) -> GRPOAgent:
    device = _device(cfg["device"])
    mcfg = cfg["model"]
    model = BabyTransformer(
        obs_dim=world.observation_size,
        num_actions=5,
        d_model=mcfg["d_model"],
        n_heads=mcfg["n_heads"],
        n_layers=mcfg["n_layers"],
        max_seq=mcfg["max_seq"],
        drop_emb=mcfg["drop_emb"],
    ).to(device)
    pcfg = cfg.get("grpo", {})
    hp = GRPOHyperparams(
        entropy_coef=pcfg.get("entropy_coef", 0.01),
        lr=pcfg.get("lr", 1e-4),
        max_grad_norm=pcfg.get("max_grad_norm", 0.5),
        num_epochs=pcfg.get("num_epochs", 4),
        batch_size=pcfg.get("batch_size", 256),
        group_size=pcfg.get("group_size", 8),
        max_steps_per_episode=cfg["world"]["max_steps"],
        exploration_noise=pcfg.get("exploration_noise", 0.0),
    )
    agent = GRPOAgent(model, hp)
    return model, agent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="aibaby/configs/default.yaml")
    ap.add_argument("--total-iters", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.total_iters:
        cfg["train"]["total_iters"] = args.total_iters

    torch.manual_seed(cfg["seed"])
    np.random.seed(cfg["seed"])

    cur_cfg = CurriculumConfig(**cfg["curriculum"])
    base = WorldConfig(**cfg["world"])
    base.seed = cfg["seed"]
    curriculum = Curriculum(cur_cfg, base)

    probe = VirtualWorld(base)
    obs_dim = probe.observation_size
    del probe

    model, agent = build(cfg, VirtualWorld(base))
    hp = agent.hp

    logger = Logger(cfg["run_dir"] + "_grpo", enabled=True)
    logger.global_step = 0

    print(f"[grpo] Transformer params: {count_parameters(model):,}")
    print(f"[grpo] observation size  : {obs_dim}")
    print(f"[grpo] device            : {agent.device}")
    print(f"[grpo] group_size        : {hp.group_size}")

    total_iters = cfg["train"]["total_iters"]

    for it in range(total_iters):
        t0 = time.time()
        wcfg = curriculum.world_config(seed=it)
        # Ensure every group member sees the identical layout seed.
        group_wcfg = WorldConfig(
            num_food=wcfg.num_food,
            num_hazard=wcfg.num_hazard,
            num_wall=wcfg.num_wall,
            initial_energy=wcfg.initial_energy,
            energy_per_timestep=wcfg.energy_per_timestep,
            food_reward=wcfg.food_reward,
            food_energy=wcfg.food_energy,
            hazard_penalty=wcfg.hazard_penalty,
            max_steps=wcfg.max_steps,
            seed=it,
        )

        data = agent.collect_groups(group_wcfg)
        grpo_stats = agent.update(data)
        agent.hp.exploration_noise = max(0.0, agent.hp.exploration_noise * 0.995)

        logger.global_step += 1
        logger.log_scalars("rollout", {
            "episode_reward": data["mean_return"],
            "episodes": data["num_traj"],
        })
        logger.log_scalars("grpo", grpo_stats)
        logger.log_scalars("curriculum", {"difficulty": curriculum.difficulty})

        dt = time.time() - t0
        print(
            f"[iter {it}] group_mean={data['mean_return']:.2f} "
            f"std={data['std_return']:.2f} steps={data['total_steps']} "
            f"diff={curriculum.difficulty:.2f} p_loss={grpo_stats['policy_loss']:.3f} "
            f"ent={grpo_stats['entropy']:.3f} ({dt:.1f}s)"
        )

        if (it + 1) % cfg["train"]["eval_every"] == 0 or it == total_iters - 1:
            def _act(o, m=model):
                m.eval()
                with torch.no_grad():
                    x = torch.from_numpy(o.astype(np.float32)).unsqueeze(0).to(agent.device)
                    logits, _ = m(x)
                return int(logits.argmax(-1).item())

            res = compare_with_random(
                policy_act=_act,
                model_obs_dim=obs_dim,
                num_episodes=cfg["train"]["eval_episodes"],
                world_config=base,
            )
            logger.log_scalars("eval", {
                "trained_reward": res["trained"].avg_reward,
                "random_reward": res["random"].avg_reward,
                "trained_success": res["trained"].success_rate,
                "random_success": res["random"].success_rate,
            })
            curriculum.update(res["trained"].avg_reward)
            logger.log_scalars("curriculum", {"difficulty": curriculum.difficulty, "stage": curriculum.stage})
            print(
                f"  [eval] trained={res['trained'].avg_reward:.2f} "
                f"(succ {res['trained'].success_rate:.2f}) "
                f"random={res['random'].avg_reward:.2f} "
                f"(succ {res['random'].success_rate:.2f})"
            )

        if (it + 1) % cfg["checkpoint_every"] == 0 or it == total_iters - 1:
            path = logger.save_checkpoint(
                model,
                agent.optimizer,
                curriculum_state={"difficulty": curriculum.difficulty, "stage": curriculum.stage},
                step=logger.global_step,
            )
            print(f"  [save] {path}")

    logger.close()
    print("[grpo] training complete.")


if __name__ == "__main__":
    main()
