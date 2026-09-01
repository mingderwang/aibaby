#!/usr/bin/env python3
"""Entry point: train the AI Baby with PPO + curriculum learning.

Usage:
    python -m aibaby.scripts.train --config aibaby/configs/default.yaml
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import torch
import yaml

from aibaby.algos.ppo import PPOAgent, PPOHyperparams, RolloutBuffer, collect_rollout
from aibaby.curriculum.curriculum import Curriculum, CurriculumConfig
from aibaby.dl.logging import Logger
from aibaby.env.world import VirtualWorld, WorldConfig
from aibaby.evaluation.evaluate import compare_with_random, evaluate_policy
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


def build(cfg: dict, world: VirtualWorld):
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
    pcfg = cfg["ppo"]
    hp = PPOHyperparams(
        gamma=pcfg["gamma"],
        lam=pcfg["lam"],
        clip_epsilon=pcfg["clip_epsilon"],
        value_coef=pcfg["value_coef"],
        value_loss_clip=pcfg.get("value_loss_clip", 0.0),
        entropy_coef=pcfg["entropy_coef"],
        lr=pcfg["lr"],
        max_grad_norm=pcfg["max_grad_norm"],
        num_epochs=pcfg["num_epochs"],
        batch_size=pcfg["batch_size"],
        max_steps_per_iter=pcfg["max_steps_per_iter"],
        exploration_noise=pcfg["exploration_noise"],
    )
    agent = PPOAgent(model, hp)
    return model, hp, agent


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

    # Curriculum-driven world.
    cur_cfg = CurriculumConfig(**cfg["curriculum"])
    base = WorldConfig(**cfg["world"])
    base.seed = cfg["seed"]
    curriculum = Curriculum(cur_cfg, base)

    # Probe world for obs dim.
    probe = VirtualWorld(base)
    obs_dim = probe.observation_size
    del probe

    model, hp, agent = build(cfg, VirtualWorld(base))

    logger = Logger(cfg["run_dir"], enabled=True)
    logger.global_step = 0

    print(f"[aibaby] Transformer params: {count_parameters(model):,}")
    print(f"[aibaby] observation size  : {obs_dim}")
    print(f"[aibaby] device            : {agent.device}")

    # Resume support.
    start_iter = 0
    if cfg.get("resume"):
        ckpt = torch.load(cfg["resume"], map_location=agent.device)
        model.load_state_dict(ckpt["model"])
        agent.optimizer.load_state_dict(ckpt["optimizer"])
        start_iter = ckpt.get("global_step", 0)
        if ckpt.get("curriculum"):
            curriculum.difficulty = ckpt["curriculum"]["difficulty"]
            curriculum.stage = ckpt["curriculum"]["stage"]
        print(f"[aibaby] resumed from {cfg['resume']} at iter {start_iter}")

    # Random baseline evaluated once for reference.
    random_result = evaluate_policy(
        act_fn=lambda o: int(np.random.randint(5)),
        num_episodes=cfg["train"]["eval_episodes"],
        world_config=base,
        name="random",
    )
    print(f"[baseline] random avg_reward={random_result.avg_reward:.2f}")

    total_iters = cfg["train"]["total_iters"]
    buffer = RolloutBuffer(hp.max_steps_per_iter, obs_dim)

    for it in range(start_iter, total_iters):
        t0 = time.time()
        # Difficulty for this iteration (curriculum updates from previous eval).
        wcfg = curriculum.world_config(seed=it)
        world = VirtualWorld(wcfg, seed=it)

        rollout_stats = collect_rollout(world, agent, buffer)
        hp.exploration_noise = max(0.0, hp.exploration_noise * 0.995)  # anneal
        ppo_stats = agent.update(buffer, bootstrap_value=rollout_stats["bootstrap_value"])

        logger.global_step += 1
        logger.log_scalars("rollout", rollout_stats)
        logger.log_scalars("ppo", ppo_stats)
        logger.log_scalars("curriculum", {"difficulty": curriculum.difficulty})

        dt = time.time() - t0
        if it % 1 == 0 or it == total_iters - 1:
            print(
                f"[iter {it}] reward={rollout_stats['episode_reward']:.2f} "
                f"steps={rollout_stats['episodes']} diff={curriculum.difficulty:.2f} "
                f"p_loss={ppo_stats['policy_loss']:.3f} v_loss={ppo_stats['value_loss']:.3f} "
                f"ent={ppo_stats['entropy']:.3f} ({dt:.1f}s)"
            )

        # Periodic evaluation.
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

        # Checkpoint.
        if (it + 1) % cfg["checkpoint_every"] == 0 or it == total_iters - 1:
            path = logger.save_checkpoint(
                model,
                agent.optimizer,
                curriculum_state={"difficulty": curriculum.difficulty, "stage": curriculum.stage},
                step=logger.global_step,
            )
            print(f"  [save] {path}")

    logger.close()
    print("[aibaby] training complete.")


if __name__ == "__main__":
    main()
