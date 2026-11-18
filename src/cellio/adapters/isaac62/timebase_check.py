"""Compare separate GUI and headless processes against the same single-step contract."""

import argparse
import json
from math import dist
from pathlib import Path
import subprocess
import sys

from cellio.core.models import CommandIntent, CommandKind, DeliveryDecision, Tick
from cellio.io import write_json
from .application import standalone_main
from .plant import IsaacPlant


def _measure(args):
    headless = args.worker == "headless"
    plant = IsaacPlant(headless=headless, asset_root=args.assets, robot_profile=args.profile)
    traces = []
    try:
        plant.load({"scene_id": "cell_a"})
        plant.reset({}, 1)
        for k in range(args.steps):
            tick = Tick("timebase-check", 1, k)
            truth = plant.snapshot(tick)
            decisions = ()
            if k == 0:
                command = CommandIntent("timebase-check:cmd:0", 0, tick.episode_id, 1, 0, 0,
                                        "actuator.belt", CommandKind.BOOL, True, 0)
                decisions = (DeliveryDecision(command, 0, 0, 0, "DELIVERED", None),)
            plant.apply(decisions, {}, tick)
            advanced = plant.advance_one_step(render=not headless and (k+1) % 4 == 0)
            traces.append({"boundary": k, "t0": advanced.engine_time_before_s, "t1": advanced.engine_time_after_s,
                           "rendered": advanced.rendered, "part_m": truth.bodies["part_000"].position_m})
        final = plant.snapshot(Tick("timebase-check", 1, args.steps))
        write_json(args.output, {"mode": args.worker, "steps": args.steps, "trace": traces,
                                 "final_part_m": final.bodies["part_000"].position_m,
                                 "elapsed_engine_s": traces[-1]["t1"] - traces[0]["t0"],
                                 "physics_hz": plant.provenance["physics_hz"]})
        return 0
    finally:
        plant.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--worker", choices=("gui", "headless"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 4 <= args.steps <= 7200:
        parser.error("steps must be between 4 and 7200")
    if args.worker:
        return standalone_main(lambda: _measure(args))
    samples = []
    for mode in ("headless", "gui"):
        destination = args.output.with_name(args.output.stem + "." + mode + ".json")
        subprocess.run([sys.executable, "-m", "cellio.adapters.isaac62.timebase_check",
                        "--assets", str(args.assets), "--profile", str(args.profile),
                        "--output", str(destination), "--steps", str(args.steps), "--worker", mode], check=True)
        samples.append(json.loads(destination.read_text(encoding="utf-8")))
    headless, gui = samples
    difference = max(dist(a["part_m"], b["part_m"]) for a, b in zip(headless["trace"], gui["trace"], strict=True))
    renders = [sum(row["rendered"] for row in case["trace"]) for case in samples]
    passed = (all(case["physics_hz"] == 120 and abs(case["elapsed_engine_s"] - args.steps/120) < 1e-6
                  for case in samples) and renders == [0, args.steps//4])
    write_json(args.output, {"passed": passed, "steps": args.steps, "rendered_frames": renders,
               "engine_elapsed_s": [case["elapsed_engine_s"] for case in samples],
               "max_part_position_difference_m": max(difference, dist(headless["final_part_m"], gui["final_part_m"]))})
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
