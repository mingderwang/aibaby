"""Fetch all scalar timeseries from the local TensorBoard and emit metrics.json.

Mirrors the data shown by TensorBoard's "Timeseries" view at
http://localhost:6006/#timeseries for the 4 experiment versions.

Vercel note: runs locally (one-off) while the TensorBoard dev server is up;
the output JSON is what ships with the deployed static dashboard.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

TB = "http://localhost:6006/data/plugin/scalars"
RUNS = [
    "v0_gcp_cpu_500",
    "v1_tuned_200",
    "v2_valueclip_300",
    "v3_easyworld_200",
]

META = {
    "v0_gcp_cpu_500": {
        "label": "v0 · GCP CPU · 500 iters",
        "color": "#9ca3af",
    },
    "v1_tuned_200": {"label": "v1 · Tuned · 200 iters", "color": "#60a5fa"},
    "v2_valueclip_300": {"label": "v2 · Value-clip · 300 iters", "color": "#34d399"},
    "v3_easyworld_200": {"label": "v3 · Easy world · 200 iters", "color": "#f472b6"},
}

TAG_DESC = {
    "rollout/episode_reward": "Mean total reward per episode collected during rollout.",
    "rollout/avg_energy": "Energy remaining at the end of the rollout.",
    "rollout/episodes": "Episodes inside one 2048-step rollout (lower = longer episodes).",
    "rollout/bootstrap_value": "GAE bootstrap value used when the rollout cut mid-episode.",
    "ppo/rollout_length": "Transitions used in the PPO update.",
    "ppo/policy_loss": "Clipped PPO policy loss objective.",
    "ppo/value_loss": "Critic value loss (clipped in v2+).",
    "ppo/entropy": "Policy entropy (high = exploratory, low = converged).",
    "curriculum/difficulty": "Adaptive curriculum difficulty scalar [0,1].",
    "curriculum/stage": "Curriculum stage index.",
    "eval/trained_reward": "Mean reward of the trained policy on eval episodes.",
    "eval/random_reward": "Mean reward of the uniform-random baseline.",
    "eval/trained_success": "Fraction of eval episodes the trained policy survived to max_steps.",
    "eval/random_success": "Fraction of eval episodes the random baseline survived.",
}


def fetch(run: str, tag: str):
    url = f"{TB}/scalars?run={urllib.parse.quote(run)}&tag={urllib.parse.quote(tag)}"
    try:
        with urllib.request.urlopen(url) as r:
            return json.load(r)
    except Exception:
        return []


def main() -> None:
    with urllib.request.urlopen(f"{TB}/tags") as r:
        tags_by_run = json.load(r)
    all_tags = sorted({t for v in tags_by_run.values() for t in v})

    out = {"runs": {}, "tags": all_tags, "tag_desc": TAG_DESC}
    for run in RUNS:
        series = {}
        for tag in all_tags:
            pts = fetch(run, tag)
            if pts:
                series[tag] = [[p[1], p[2]] for p in pts]  # [step, value]
        out["runs"][run] = {"meta": META[run], "series": series}
        print(f"{run}: {sum(1 for v in series.values() if v)}/{len(all_tags)} tags")

    path = "/Users/mingderwang/projects/ai/aibaby/demo/web/metrics.json"
    text = json.dumps(out)
    with open(path, "w") as f:
        f.write(text)
    print(f"Wrote {path} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
