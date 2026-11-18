"""Publish a small, path-free register of sealed workstation development attempts."""

import argparse
import gzip
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from math import dist, isclose
from pathlib import Path
from statistics import mean

from cellio.analysis.campaign import lines
from cellio.artifacts import verify_artifact
from cellio.io import file_hash
from cellio.runtime.campaign import initial_state


def read(path):
    return json.loads(path.read_bytes())


def pick(data, keys):
    return {k: data[k] for k in keys if k in data}


def read_run(directory):
    proof = verify_artifact(directory)
    result = read(directory / "result.json")
    config = read(directory / "config.resolved.json")
    if result["backend"] != "isaac62" or config["profile"] != "isaac62_lockstep":
        raise ValueError("Commissioning tree contains an unexpected execution profile")
    supervisor = directory.parent.parent
    records = [
        s
        for s in lines(supervisor / "ledger.jsonl")
        if s["attempt_id"] == proof["attempt_id"]
    ]
    if not records or records[-1]["state"] not in (
        "COMPLETED",
        "INFRA_ERROR",
        "CANCELLED",
    ):
        raise ValueError(
            "Missing terminal supervisor record for " + proof["attempt_id"]
        )
    job = read(supervisor / ".workers" / proof["attempt_id"] / "job.json")["episode"]
    if (
        job["run_id"] != proof["run_id"]
        or job["attempt_id"] != proof["attempt_id"]
        or job["config"] != config
    ):
        raise ValueError("Worker inputs and sealed artifact differ")
    cell, seed = config["scene"]["cell_id"], config["seeds"]["scene"]
    state = read(directory / "initial_state.json")
    if job["initial_state"] != state:
        raise ValueError("Worker initial state differs from the sealed artifact")
    profile = job["plant_options"].get("robot_profile") or {}
    declared = pick(
        profile,
        (
            "finger_force_limit_n",
            "gripper_slew_mps",
            "gripper_open_slew_mps",
            "finger_stiffness_n_per_m",
            "finger_damping_n_s_per_m",
            "finger_armature_kg",
            "articulation_velocity_iterations",
            "solve_articulation_contact_last",
        ),
    )
    profile_actual = result.get("plant_provenance") or {}
    effective = pick(
        profile_actual,
        (
            "device",
            "solver",
            "physics_hz",
            "gravity",
            "finger_drivers",
            "articulation_solver",
            "finger_drive_settings",
            "finger_tensor_readback",
            "rigid_body_masses_kg",
            "gripper_slew_mps",
            "gripper_open_slew_mps",
            "belt_run_speed_mps",
        ),
    )
    env = read(directory / "environment.lock.json")
    fingerprint = env.get("cellio_source_sha256")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or len(fingerprint) != 64
    ):
        raise ValueError("Invalid source fingerprint")
    manifest = read(directory / "manifest.final.json")
    sizes = manifest["files"]
    reqs = {s["requirement_id"]: s for s in read(directory / "requirements.json")}
    goal = reqs.get(cell + ".r14.goal", {})
    done = abort = reason = None
    with gzip.open(directory / "events.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            e = json.loads(line)["data"]
            if e["event_type"] == "controller.done":
                done = e["step"]
            elif e["event_type"] == "controller.abort":
                abort, reason = e["step"], e["payload"].get("reason")
    faults = read(directory / "faults.json")
    f = (
        pick(
            faults[0],
            (
                "family",
                "target_id",
                "trigger_match_step",
                "actual_start",
                "actual_end",
                "first_effect_step",
                "effect_status",
                "coverage",
                "boundary_effect_status",
                "restoration_status",
                "functional_reuse_status",
                "physical_hold_verdict",
            ),
        )
        if faults
        else None
    )
    perf = read(directory / "performance.json")
    planned = next((e for e in records if e["state"] == "PLANNED"), None)
    elapsed = (records[-1]["unix_ns"] - planned["unix_ns"]) / 1e9 if planned else None
    sample = initial_state(cell, seed)
    sample_matches = all(
        state[k] == sample[k]
        for k in ("x_offset_m", "y_offset_m", "yaw_rad", "mass_kg")
    )
    if sample_matches:
        basis = "matches_registered_seed_sampler"
    elif all(state[k] == 0 for k in ("x_offset_m", "y_offset_m", "yaw_rad")):
        basis = "explicit_center_pose"
    else:
        basis = "explicit_offset_pose"
    report = {
        "cell": cell,
        "arm": config["scenario_id"].split(".")[-1],
        "configured_scene_seed": seed,
        "initial_state_basis": basis,
        "initial_state": state,
        "run_id": proof["run_id"],
        "attempt_id": proof["attempt_id"],
        "closed_utc": datetime.fromtimestamp(
            records[-1]["unix_ns"] / 1e9, UTC
        ).isoformat(timespec="seconds"),
        "headless": job["plant_options"]["headless"],
        "horizon_steps": config["episode"]["horizon_steps"],
        "actual_advances": proof["ticks"],
        "end_step": proof["end_step"],
        "execution_status": records[-1]["state"],
        "artifact_status": proof["completion_status"],
        "exit_reason": result["reason"],
        "task_outcome": result["task_outcome"],
        "compliance": result["compliance_verdict"],
        "goal_verdict": goal.get("verdict"),
        "goal_achieved_step": goal.get("first_witness_step")
        if goal.get("verdict") == "PASS"
        else None,
        "controller_done_step": done,
        "controller_abort_step": abort,
        "controller_abort_reason": reason,
        "fault": f,
        "physics_profile": {
            "declared_runtime_profile": config["profile"],
            "robot_profile_overrides": declared,
            "runtime_fields": effective,
        },
        "source_sha256_when_recorded": fingerprint,
        "manifest_final_sha256": file_hash(directory / "manifest.final.json"),
        "trace_chain_sha256": proof["artifact_digest"],
        "environment_lock_sha256": sizes["environment.lock.json"]["sha256"],
        "timing": {
            **{
                k: perf.get(k)
                for k in (
                    "running_wall_ns",
                    "startup_wall_ns",
                    "warmup_wall_ns",
                    "finalization_wall_ns",
                    "rtf",
                )
            },
            "supervisor_planned_to_terminal_s": elapsed,
        },
        "memory": pick(
            perf,
            (
                "peak_rss_bytes",
                "peak_vram_bytes",
                "peak_gpu_device_used_bytes",
                "vram_measurement",
                "device_memory_measurement",
            ),
        ),
        "sealed_attempt_bytes": sum(v["size"] for v in sizes.values())
        + (directory / "manifest.final.json").stat().st_size
        + (directory / "COMPLETE").stat().st_size,
        "compressed_trace_bytes": sizes["trace.jsonl.gz"]["size"],
        "video": pick(
            result.get("video") or {},
            ("frame_count", "first_step", "last_step", "fps", "size", "sha256"),
        )
        or None,
    }
    if report["exit_reason"] and any(c in report["exit_reason"] for c in ("/", "\\")):
        raise ValueError("Exit reason is not a portable identifier")
    return report


def parity(root):
    modes = [read(root / f"parity-1.{mode}.json") for mode in ("headless", "gui")]
    record = read(root / "parity-1.json")
    if [m["mode"] for m in modes] != ["headless", "gui"]:
        raise ValueError("Unexpected parity inputs")
    n = record["steps"]
    for m in modes:
        if (
            m["steps"] != n
            or m["physics_hz"] != 120
            or [p["boundary"] for p in m["trace"]] != list(range(n))
            or any(
                not isclose(p["t1"] - p["t0"], 1 / 120, abs_tol=1e-8)
                for p in m["trace"]
            )
            or any(
                not isclose(p["t1"], q["t0"], abs_tol=1e-8)
                for p, q in zip(m["trace"], m["trace"][1:])
            )
        ):
            raise ValueError("Invalid recorded physics advancement")
    elapsed = [m["trace"][-1]["t1"] - m["trace"][0]["t0"] for m in modes]
    images = [sum(p["rendered"] for p in m["trace"]) for m in modes]
    worst = max(
        [
            dist(a["part_m"], b["part_m"])
            for a, b in zip(modes[0]["trace"], modes[1]["trace"], strict=True)
        ]
        + [dist(modes[0]["final_part_m"], modes[1]["final_part_m"])]
    )
    if (
        elapsed != record["engine_elapsed_s"]
        or images != record["rendered_frames"]
        or worst != record["max_part_position_difference_m"]
    ):
        raise ValueError("Parity summary differs from the recorded time series")
    passed = (
        all(isclose(v, n / 120, abs_tol=1e-8) for v in elapsed)
        and worst <= 1e-6
        and not any(p["rendered"] for p in modes[0]["trace"])
        and all(
            p["rendered"] == ((p["boundary"] + 1) % 4 == 0) for p in modes[1]["trace"]
        )
    )
    if record["passed"] != passed:
        raise ValueError("Reported GUI parity differs from timing and pose evidence")
    return {
        "cell": "cell_a",
        "initial_state_basis": "fixed_default_pose",
        "passed": passed,
        "steps": n,
        "elapsed_engine_s": elapsed,
        "headless_gui_render_count": images,
        "max_part_position_difference_m": worst,
        "physics_hz": 120,
        "source_sha256": {
            f.name: file_hash(f) for f in sorted(root.glob("parity-1*.json"))
        },
    }


def fingers(root):
    file = root / "finger-twist-1.json"
    source = read(file)
    phases = defaultdict(list)
    for row in source["samples"]:
        phases[row["phase"]].append(row)
    result = {}
    for phase, rows in phases.items():
        contact = [j for r in rows for j in r["fingers"].values()]
        offsets = [
            [x - y for x, y in zip(r["part_m"], r["tcp_m"], strict=True)] for r in rows
        ]
        computed = {
            "samples": len(rows),
            "bilateral_samples": sum(r["bilateral"] for r in rows),
            "mean_abs_qdot_minus_encoder_mps": mean(
                abs(j["qdot_mps"] - j["encoder_delta_mps"]) for j in contact
            ),
            "mean_abs_qdot_minus_twist_mps": mean(
                abs(j["qdot_mps"] - j["link_twist_mps"]) for j in contact
            ),
            "max_part_tcp_offset_change_m": max(dist(offsets[0], v) for v in offsets),
        }
        if any(
            not isclose(float(source["summary"][phase][key]), val, abs_tol=1e-12)
            for key, val in computed.items()
        ):
            raise ValueError("Finger diagnostic summary differs from raw samples")
        result[phase] = computed
    passed = (
        result["lifted_grasp"]["bilateral_samples"] == 120
        and result["reduced_effort"]["max_part_tcp_offset_change_m"] >= 0.04
    )
    if source["passed"] != passed:
        raise ValueError(
            "Reported finger diagnostic differs from contact and slip evidence"
        )
    return {
        "cell": source["cell_id"],
        "initial_state_basis": "fixed_default_pose",
        "source_sha256": file_hash(file),
        "sample_count": len(source["samples"]),
        "diagnostic_criteria_met": passed,
        "reduced_effort_drive_readback_n": source["slip_force_limit_n"],
        "phases": result,
    }


def collect(root, output, workers, through_utc):
    markers = sorted(root.glob("*/*/*/COMPLETE"))
    allowed_ids = set()
    until_ns = (int(through_utc.timestamp()) + 1) * 10**9 if through_utc else None
    for ledger in sorted(root.glob("*/ledger.jsonl")):
        for transition in lines(ledger):
            terminal = transition["state"] in ("COMPLETED", "INFRA_ERROR", "CANCELLED")
            if terminal and (until_ns is None or transition["unix_ns"] < until_ns):
                allowed_ids.add(transition["attempt_id"])
    directories = [m.parent for m in markers if m.parent.name in allowed_ids]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(read_run, directories))
    rows.sort(key=lambda x: (x["closed_utc"], x["attempt_id"]))
    replay_dir = Path(__file__).resolve().parents[1] / "results" / "workstation"
    documents = {
        "a-before-tuning": 20,
        "b-before-tuning": 20,
        "a-4n": 4,
        "b-10kg": 4,
        "b-directional": 4,
    }
    by_id = {row["attempt_id"]: row for row in rows}
    for label, force in documents.items():
        replay_file = replay_dir / f"commissioning-{label}-windows.json"
        replay = read(replay_file)
        row = by_id.get(replay["attempt_id"])
        if row is None:
            continue
        if replay["source_digest"] != row["trace_chain_sha256"]:
            raise ValueError("Published replay points at a different trace")
        row["physics_profile"]["adr_002_documented_force_limit_n"] = force
        row["published_replay_filename"] = replay_file.name
    full = [
        r
        for r in rows
        if r["headless"]
        and r["actual_advances"] == r["horizon_steps"] == 7200
        and r["execution_status"] == r["artifact_status"] == "COMPLETED"
    ]
    groups = {
        cell: [r for r in full if r["cell"] == cell] for cell in ("cell_a", "cell_b")
    }
    metrics = {}
    for cell, items in groups.items():
        metrics[cell] = {
            "full_attempts": len(items),
            "mean_running_s": mean(r["timing"]["running_wall_ns"] / 1e9 for r in items),
            "mean_supervisor_s": mean(
                r["timing"]["supervisor_planned_to_terminal_s"] for r in items
            ),
            "mean_sealed_bytes": mean(r["sealed_attempt_bytes"] for r in items),
        }
    estimates = {
        "estimator": "410 planned attempts per cell multiplied by that cell's observed arithmetic mean; serial execution",
        "source_count": len(full),
        "cell_means": metrics,
        "running_hours": 410
        * sum(d["mean_running_s"] for d in metrics.values())
        / 3600,
        "supervised_hours": 410
        * sum(d["mean_supervisor_s"] for d in metrics.values())
        / 3600,
        "sealed_artifact_GiB": 410
        * sum(d["mean_sealed_bytes"] for d in metrics.values())
        / 2**30,
    }
    data = {
        "schema_version": "cellio.development_commissioning/1.0",
        "scope": "Native Windows Isaac development commissioning; exploratory settings and repeated conditions",
        "sample_definition": "Every sealed attempt discovered in the commissioning tree at enumeration, including early errors and short clock/video checks. The capacity subset uses every headless, completed H=7200 attempt, regardless of its task or compliance result.",
        "time_and_storage_basis": "RUNNING is measured episode wall time. Supervisor time spans PLANNED to terminal, including boot and closure. Bytes sum sealed files, the final manifest and COMPLETE. Cache, simulator assets, unsealed logs, retries and separate studies are excluded from the projection.",
        "initial_state_basis": "Seed matches require identical x/y offsets, yaw and mass to the registered sampler. Original attempts predate the proposal log. Explicit poses retain their nominal seed label.",
        "physics_fields": "Worker overrides are explicit request values. ADR 002 states the intended force for identified, hash-matched published replays. Runtime articulation_solver and finger_drive_settings are USD/PhysX attribute readbacks; finger_tensor_readback and rigid_body_masses_kg are live tensor measurements where available. Slew values are software target limits. Absent historical fields have no recorded readback.",
        "counts": {
            "sealed": len(rows),
            "full_headless_completed": len(full),
            "execution_status": dict(Counter(r["execution_status"] for r in rows)),
            "full_headless_task_outcomes": dict(
                Counter(r["task_outcome"] for r in full)
            ),
        },
        "latest_included_closure_utc": rows[-1]["closed_utc"],
        "attempts": rows,
        "development_capacity_estimate_for_820": estimates,
        "diagnostics": {
            "gui_headless_timebase": parity(root),
            "finger_velocity_and_relative_slip": fingers(root),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commissioning", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/workstation/commissioning-register.json"),
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--through-utc",
        type=datetime.fromisoformat,
        help="Include terminal ledger records through this UTC second (ISO 8601)",
    )
    args = parser.parse_args()
    if args.through_utc and args.through_utc.utcoffset() != UTC.utcoffset(None):
        parser.error("--through-utc requires an explicit UTC timestamp")
    report = collect(args.commissioning, args.output, args.workers, args.through_utc)
    print(
        json.dumps(
            {k: report[k] for k in ("counts", "development_capacity_estimate_for_820")},
            indent=2,
        )
    )
