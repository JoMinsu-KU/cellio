"""Replay the Cell A corner-3 qualification attempt and the slower-ascent diagnostic."""

import argparse
import json
import platform
from math import dist
from pathlib import Path
from statistics import median, quantiles

from cellio.analysis.replay import read, read_attempt
from cellio.core.oracle.geometry import grasp
from cellio.core.oracle.projector import offset
from cellio.io import digest, file_hash, write_json
from cellio.plugins import scene_plugin
from cellio.runtime.campaign import read_plan


def measured_grasp(truth):
    return {
        "part_xyz_m": truth["bodies"]["part_000"]["position_m"],
        "tcp_xyz_m": truth["bodies"]["cell_tcp"]["position_m"],
        "part_vertical_velocity_mps": truth["bodies"]["part_000"][
            "linear_velocity_mps"
        ][2],
        "tcp_vertical_velocity_mps": truth["bodies"]["cell_tcp"]["linear_velocity_mps"][
            2
        ],
        "part_minus_tcp_mm": [1000 * x for x in offset(truth)],
        "finger_gap_mm": 1000 * truth["plant_state"]["gripper.gap_m"],
        "active_contacts": truth["contacts"],
        "actual_grasp_contact": grasp(truth),
    }


def summarize(path):
    cfg = read(path / "config.resolved.json")
    state = read(path / "initial_state.json")
    corner = [state[k] for k in ("x_offset_m", "y_offset_m", "yaw_rad")]
    if (
        cfg["scene"]["cell_id"] != "cell_a"
        or cfg["faults"]
        or corner != [-0.001, 0.004, 0.03]
    ):
        raise ValueError(
            "Expected the healthy Cell A corner at (-0.001 m, +0.004 m, +0.03 rad)"
        )
    horizon = cfg["episode"]["horizon_steps"]
    plugin = scene_plugin("cell_a")
    oracle = plugin.create_oracle(horizon)
    oracle.reset(
        plugin.requirement_presets(horizon),
        read(path / "manifest.start.json")["generation"],
    )
    projector = oracle.projector
    samples = []
    violations = []
    measurements = {}
    peak = -1.0

    def measure(frame, updates):
        nonlocal peak
        k, truth = frame["step"], frame["truth"]
        facts = projector.project(frame).facts
        if projector.grasp_offset is not None and "anchor" not in measurements:
            measurements["anchor"] = {"step": k, **measured_grasp(truth)}
        if projector.lift_step == k:
            measurements["lift_admission"] = {"step": k, **measured_grasp(truth)}
        if not facts["carry_active"]:
            return
        drift = 1000 * dist(projector.grasp_offset, offset(truth))
        if facts["carry_valid"] != (drift <= 18.0):
            raise ValueError(f"Reconstructed carry predicate disagrees at S{k}")
        samples.append((k, drift))
        if drift > peak:
            peak = drift
            measurements["peak"] = {
                "step": k,
                "drift_mm": drift,
                **measured_grasp(truth),
            }
        if not facts["carry_valid"]:
            if not violations or violations[-1][1] != k - 1:
                violations.append([k, k])
            else:
                violations[-1][1] = k
            if "first_violation" not in measurements:
                measurements["first_violation"] = {
                    "step": k,
                    "drift_mm": drift,
                    **measured_grasp(truth),
                }

    evidence = read_attempt(path, on_frame=measure)
    if (
        evidence["completion_status"] != "COMPLETED"
        or evidence["end_step"] != 7200
        or not evidence["online_offline_equal"]
    ):
        raise ValueError(
            "A complete 7200-step run with matching online and replayed witnesses is required"
        )
    ds = [d for _, d in samples]
    measurements["active_samples"] = {
        "count": len(ds),
        "first_step": samples[0][0],
        "last_step": samples[-1][0],
        "minimum_mm": min(ds),
        "median_mm": median(ds),
        "p95_mm": quantiles(ds, n=20, method="inclusive")[-1],
        "maximum_mm": max(ds),
        "count_over_18_mm": sum(d > 18.0 for d in ds),
        "violation_intervals_inclusive": violations,
    }
    witnesses = {
        r["requirement_id"]: [r["verdict"], r["first_witness_step"]]
        for r in evidence["requirements"]
    }
    result = read(path / "result.json")
    physical = result["plant_provenance"]["finger_tensor_readback"]
    record = {
        "run_id": evidence["run_id"],
        "attempt_id": evidence["attempt_id"],
        "end_step": evidence["end_step"],
        "config_sha256": digest(cfg),
        "initial_state_sha256": digest(state),
        "plant_source_sha256": read(path / "environment.lock.json")[
            "cellio_source_sha256"
        ],
        "manifest_final_sha256": file_hash(path / "manifest.final.json"),
        "trace_chain_sha256": evidence["source_digest"],
        "rules_sha256": evidence["rules_digest"],
        "all_witnesses_sha256": digest(witnesses),
        "online_offline_equal": evidence["online_offline_equal"],
        "task_outcome": evidence["task_outcome"],
        "compliance": evidence["compliance_verdict"],
        "requirements": {
            k: v
            for k, v in witnesses.items()
            if v[0] == "FAIL" or k.split(".")[1] in ("r08", "r09", "r13", "r14")
        },
        "measured_finger_force_caps_n": physical["drive_effort_limits_n"],
        "r09": measurements,
    }
    plan_file = path.parents[1] / "campaign.plan.json"
    if plan_file.is_file():
        plan = read_plan(plan_file)
        if not any(r["run_id"] == record["run_id"] for r in plan["runs"]):
            raise ValueError("Attempt is absent from its containing plan")
        record["plan_sha256"] = plan["plan_hash"]
    ledger = path.parents[1] / "ledger.jsonl"
    if ledger.is_file():
        with ledger.open(encoding="utf-8") as stream:
            indices = {
                e["attempt_index"]
                for e in map(json.loads, stream)
                if e["run_id"] == record["run_id"]
                and e["attempt_id"] == record["attempt_id"]
            }
        if len(indices) != 1:
            raise ValueError("Missing or conflicting attempt lineage")
        record["attempt_index"] = indices.pop()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_attempt", type=Path)
    parser.add_argument("diagnostic_attempt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline, diagnostic = (
        summarize(p.resolve()) for p in (args.baseline_attempt, args.diagnostic_attempt)
    )
    for key in (
        "initial_state_sha256",
        "config_sha256",
        "rules_sha256",
        "measured_finger_force_caps_n",
    ):
        if baseline[key] != diagnostic[key]:
            raise ValueError(f"Pair differs in {key}")
    for key in ("anchor", "lift_admission"):
        if baseline["r09"][key] != diagnostic["r09"][key]:
            raise ValueError(f"Pair differs at {key}")
    if "plan_sha256" not in baseline or baseline.get("attempt_index") != 0:
        raise ValueError("Baseline must be the registered first attempt")
    if set(baseline["measured_finger_force_caps_n"].values()) != {4.0}:
        raise ValueError("Expected a measured 4 N drive force limit")
    write_json(
        args.output,
        {
            "schema": "cellio.loaded-ascent-comparison/1.0",
            "cell": "cell_a",
            "corner_index": 3,
            "initial_offsets": {"x_m": -0.001, "y_m": 0.004, "yaw_rad": 0.03},
            "drift_limit_mm": 18,
            "distribution": "Each active 120 Hz boundary counts once. P95 uses linear interpolation between ordered observations.",
            "evaluator_sha256": file_hash(Path(__file__)),
            "replay_platform": {
                "os": platform.system(),
                "kernel": platform.release(),
                "python": platform.python_version(),
            },
            "baseline": {
                "role": "original qualification first attempt",
                "initial_loaded_target_speed_cap_mps": 0.10,
                **baseline,
            },
            "slower_ascent": {
                "role": "development diagnostic on the observed corner",
                "initial_loaded_target_speed_cap_mps": 0.04,
                "initial_loaded_target_travel_m": 0.03,
                "subsequent_loaded_target_speed_cap_mps": 0.10,
                **diagnostic,
            },
        },
    )


if __name__ == "__main__":
    main()
