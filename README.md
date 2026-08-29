# AiBaby

A **small Transformer baby** learning to survive in a **10×10 virtual world**
via **PPO (actor-critic)**, initialized entirely from scratch — **no LLM, no
pretrained weights, no LoRA**.

![stack](https://img.shields.io/badge/stack-PyTorch-ee4c2c)

## Highlights (checklist)

| Feature | Status |
|---|---|
| Random initialization (no LLM / pretrained / LoRA) | ✅ |
| 10×10 virtual world | ✅ `aibaby/env/world.py` |
| Small Transformer (actor-critic) | ✅ `aibaby/models/transformer.py` |
| PPO / Actor-Critic | ✅ `aibaby/algos/ppo.py` |
| Curriculum learning | ✅ `aibaby/curriculum/curriculum.py` |
| Random baseline | ✅ `aibaby/agents/random_agent.py`, eval compares vs random |
| Docker | ✅ `Dockerfile` |
| Evaluation | ✅ `aibaby/evaluation/evaluate.py` |
| Checkpoint | ✅ save/resume in `aibaby/scripts/train.py` |
| TensorBoard | ✅ `aibaby/dl/logging.py` |
| Language emergence / AGI interface | ✅ `aibaby/interface/language.py` |

## The world

* Discrete **10×10 grid** with tiles: `FOOD`, `HAZARD`, `WALL`, `EMPTY`.
* The baby chooses one of 5 actions (stay / up / down / left / right).
* It starts with `initial_energy`, loses a little energy each step, gains
  **+reward** from food, and gets a **penalty** for entering hazards.
* An episode ends when energy runs out (death) or `max_steps` is reached
  (survival / timeout).

## The model

`BabyTransformer` is a small **decoder-only causal transformer** with policy
and value heads — a shared **actor-critic**. All weights are initialized
randomly from scratch. It takes a flattened observation and outputs action
logits + a scalar value.

## PPO

Standard clipped PPO with **GAE**, entropy bonus, gradient clipping, and an
annealed exploration-noise term for early exploration.

## Curriculum

Difficulty is a scalar in `[0, 1]`. Easy worlds start with lots of food and
few hazards; as the baby outperforms the threshold, difficulty increases
(fewer food, more hazards, harsher penalties). Difficulty also backs off if
performance drops — a classic adaptive curriculum.

## Evaluation & baselines

`compare_with_random` runs the trained policy and a **uniform-random
baseline** over the same world config, reporting avg reward, success rate,
food eaten, and hazards hit. All logged to TensorBoard.

## Quick start

```bash
# CPU, short run
pip install -r requirements.txt
python -m aibaby.scripts.train --config aibaby/configs/default.yaml --total-iters 200

# TensorBoard
tensorboard --logdir runs

# Watch a trained policy (after saving a checkpoint)
python -m aibaby.scripts.play --checkpoint runs/baby/checkpoints/ckpt_XX.pt

# Random baseline play
python -m aibaby.scripts.play --random
```

## Docker

```bash
bash docker-build.sh          # build + short sanity run
docker run --rm -p 6006:6006 -v "$(pwd)/runs:/app/runs" aibaby \
  tensorboard --logdir /app/runs --host 0.0.0.0
```

## Configuration

Everything is driven by `aibaby/configs/default.yaml`:
`world`, `model`, `ppo`, `train`, and `curriculum` sections. Checkpoints are
saved under `<run_dir>/checkpoints/` and can be resumed via `resume: <path>`.

## Extension seams: language emergence / AGI

`aibaby/interface/language.py` provides forward-looking stubs (deliberately
inactive in the default loop) so the project can grow into language/AGI
experiments without rearchitecting:

* **`Tokenizer`** – deterministic grounding of world events to discrete token
  streams (the mapping would become *learned/emerged* later).
* **`SequenceBuilder`** – packs per-step observations into a variable-length
  sequence for the transformer's causal/sequence interface (memory /
  utterances over time).
* **`CommunicationChannel`** – bidirectional discrete channel for future
  multi-agent grounded communication.

## Repository layout

```
aibaby/
  env/         # 10x10 VirtualWorld + optional gymnasium adapter
  models/      # small Transformer actor-critic
  algos/       # PPO
  curriculum/  # adaptive difficulty schedule
  agents/      # random baseline
  evaluation/  # eval + random comparison
  interface/   # language emergence / AGI seams
  dl/          # TensorBoard + checkpointing
scripts/       # train.py, play.py
configs/       # default.yaml
```
