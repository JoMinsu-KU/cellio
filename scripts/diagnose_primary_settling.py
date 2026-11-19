"""Read only the sealed canonical reference trajectories that failed R06."""

import argparse
import gzip
import json
from collections import Counter
from math import isfinite
from pathlib import Path
from statistics import median

from publish_primary import require, save

from cellio.analysis.campaign import safe_child
from cellio.analysis.replay import read
from cellio.core.oracle.geometry import FINGERS, quiet
from cellio.environment import source_hash
from cellio.io import atomic_write, file_hash

ARM = {f"panda_joint{n}": 0.02 for n in range(1, 8)}
BELT = "belt.surface_speed_mps"
LIMITS = {**ARM, **dict.fromkeys(FINGERS, 0.003), BELT: 0.001}
BUNDLE = {
    "control.operation": "CANCEL_OPERATION",
    "actuator.robot": "HOLD",
    "actuator.gripper": "HOLD",
    "actuator.belt": "BOOL",
}


def sample(frame):
    truth = frame["truth"]
    values = {}
    for name in LIMITS:
        if name == BELT:
            value = truth["plant_state"].get(name)
        else:
            joint = truth["joints"].get(name, {})
            value = joint.get("velocity") if joint.get("valid") is True else None
        values[name] = (
            value if type(value) in (int, float) and isfinite(value) else None
        )
    missing = [name for name, velocity in values.items() if velocity is None]
    if not truth.get("complete"):
        missing.append("truth.complete")
    exceed = {
        name: velocity
        for name, velocity in values.items()
        if velocity is not None and abs(velocity) > LIMITS[name]
    }
    if not missing:
        require(
            quiet(truth, True, False) is (not exceed), "Physical predicate discrepancy"
        )
    return {
        "step": frame["step"],
        "invalid": missing,
        "outside_limit": exceed,
        "record_hash": frame["record_hash"],
    }


def command_receipts(frame, seen, issued):
    step = frame["step"]
    for cmd in frame["intents"]:
        name = cmd["actuator_id"]
        if BUNDLE.get(name) == cmd["kind"] and (
            name != "actuator.belt" or cmd["value"] is False
        ):
            issued[cmd["command_id"]] = name
    receipt = frame.get("actuation") or {}
    returned = {
        command["command_id"]: command
        for command in receipt.get("applied_commands", [])
    }
    for command in frame["applied"]:
        command_id = command["command_id"]
        if command_id not in issued or command["status"] != "APPLIED":
            continue
        name = issued[command_id]
        require(
            receipt["accepted"]
            and command == returned.get(command_id)
            and command["applied_step"] == step
            and name == command["actuator_id"],
            "Frame and actuator receipt disagree",
        )
        if name != "control.operation":
            require(
                receipt["targets_by_actuator"][name] == command["effective_value"],
                "Applied target differs",
            )
        if name == "actuator.belt":
            require(command["effective_value"] == 0, "Belt stop target differs")
        seen.setdefault(
            name,
            {
                "command_id": command_id,
                "issued_step": command["issued_step"],
                "applied_step": step,
            },
        )
    return (
        set(seen) == set(BUNDLE)
        and frame["fault_audit"].get("pending_command_count") == 0
    )


def diagnose_trial(folder, entry):
    """Reconstruct the first actual dwell from measured boundary velocities after abort."""
    directory = safe_child(folder, entry["run_id"], entry["attempt_id"])
    manifest = read(directory / "manifest.final.json")
    require(
        file_hash(directory / "manifest.final.json") == entry["manifest_sha256"]
        and (directory / "COMPLETE").read_text().strip() == entry["manifest_sha256"],
        "Attempt seal changed",
    )
    for file, key in (
        ("trace.jsonl.gz", "trace_gzip_sha256"),
        ("requirements.json", "requirements_sha256"),
    ):
        require(
            file_hash(directory / file)
            == entry[key]
            == manifest["files"][file]["sha256"],
            "Sealed file mismatch",
        )
    rules = {
        v["requirement_id"].split(".")[1]: v
        for v in read(directory / "requirements.json")
    }
    (abort,) = entry["abort_steps_if_reference_r06_fail"]
    require(
        entry["cell"] == "cell_a"
        and entry["arm"] in ("f2", "f3")
        and rules["r06"]["first_witness_step"] == entry["r06_step"] == abort + 72
        and rules["r05"]["verdict"] == "PASS"
        and rules["r06"]["verdict"] == "FAIL",
        "Selected rule mismatch",
    )
    receipts, issued, points = {}, {}, {}
    bundle_step = previous = previous_streak_boundary = first_12_sample_end = (
        first_dwell_end
    ) = None
    streak = streak_max_in_window = invalid_count = 0
    deadline_streak = None
    earlier_intervals = []
    finger_violations = []
    arm_belt_streak = 0
    arm_belt_complete = None
    with gzip.open(directory / "trace.jsonl.gz", "rb") as raw:
        header = json.loads(next(raw))
        require(
            header["run_id"] == entry["run_id"]
            and header["attempt_id"] == entry["attempt_id"],
            "Header mismatch",
        )
        for step, line in enumerate(raw):
            if step < abort:
                continue
            if step > entry["end_step"]:
                break
            frame = json.loads(line)
            require(
                frame["truth"]["tick"]["step"] == frame["step"] == step
                and frame["truth"]["tick"]["hz"] == 120,
                "Trace gap or changed clock",
            )
            current = sample(frame)
            invalid_count += bool(current["invalid"])
            if step <= abort + 72:
                if not current["invalid"] and not (
                    set(current["outside_limit"]) - set(FINGERS)
                ):
                    arm_belt_streak += 1
                    if arm_belt_streak == 13 and arm_belt_complete is None:
                        arm_belt_complete = step
                else:
                    arm_belt_streak = 0
            if step >= abort + 60 and set(current["outside_limit"]) & set(FINGERS):
                if finger_violations and finger_violations[-1][1] == step - abort - 1:
                    finger_violations[-1][1] = step - abort
                else:
                    finger_violations.append([step - abort, step - abort])
            if (
                step <= abort + 8
                and command_receipts(frame, receipts, issued)
                and bundle_step is None
            ):
                bundle_step = step
            if step in (abort, abort + 60, abort + 72):
                points[str(step - abort)] = current
            if current["invalid"] or current["outside_limit"]:
                if streak:
                    earlier_intervals.append([step - abort - streak, step - abort - 1])
                streak = 0
            else:
                if not streak:
                    previous_streak_boundary = previous
                streak += 1
                if streak == 12 and first_12_sample_end is None:
                    first_12_sample_end = step
                if streak == 13 and first_dwell_end is None:
                    first_dwell_end = step
                    points["before_first_dwell"] = previous_streak_boundary
                    points["dwell_complete"] = current
            if step <= abort + 72:
                streak_max_in_window = max(streak_max_in_window, streak)
            if step == abort + 72:
                deadline_streak = streak
            if first_dwell_end is not None and step >= abort + 72:
                break
            previous = current
    end = current["step"]
    require(
        (first_dwell_end is not None or end == entry["end_step"])
        and streak_max_in_window < 13
        and bundle_step == rules["r05"]["first_witness_step"],
        "Evidence or reconstructed registered verdict differs",
    )
    return {
        **{
            step: entry[step]
            for step in (
                "run_id",
                "attempt_id",
                "scene_seed",
                "repeat",
                "arm",
                "rules_sha256",
                "manifest_sha256",
                "trace_gzip_sha256",
                "trace_chain_sha256",
                "requirements_sha256",
            )
        },
        "abort_step": abort,
        "r05_witness": bundle_step,
        "r06_witness": abort + 72,
        "bundle_receipts": receipts,
        "first12_start": first_12_sample_end - 11
        if first_12_sample_end is not None
        else None,
        "first12_complete": first_12_sample_end,
        "dwell_start": first_dwell_end - 12 if first_dwell_end is not None else None,
        "dwell_complete": first_dwell_end,
        "lateness_steps": first_dwell_end - (abort + 72)
        if first_dwell_end is not None
        else None,
        "observed_end_step": end,
        "max_consecutive_quiet_observations_through_deadline": streak_max_in_window,
        "consecutive_quiet_observations_at_deadline": deadline_streak,
        "earlier_quiet_ranges_relative_to_abort": earlier_intervals,
        "finger_exceedance_ranges_relative_to_abort_from_plus60": finger_violations,
        "arm_and_belt_first_dwell_completion_within_window": arm_belt_complete,
        "invalid_snapshots_inspected": invalid_count,
        "point_measurements": points,
    }


def dist(values):
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "median": median(values) if values else None,
        "max": max(values) if values else None,
    }


def describe(rows):
    summary = {
        "n": len(rows),
        "never_reached_by_horizon": sum(r["dwell_complete"] is None for r in rows),
        "attempts_with_invalid_inspected_samples": sum(
            r["invalid_snapshots_inspected"] > 0 for r in rows
        ),
        "invalid_snapshots_inspected": sum(
            r["invalid_snapshots_inspected"] for r in rows
        ),
        "snapshots_inspected": sum(
            r["observed_end_step"] - r["abort_step"] + 1 for r in rows
        ),
    }
    for field in (
        "r05_witness",
        "r06_witness",
        "first12_start",
        "first12_complete",
        "dwell_start",
        "dwell_complete",
    ):
        summary[field + "_after_abort_steps"] = dist(
            [r[field] - r["abort_step"] for r in rows if r[field] is not None]
        )
    summary["dwell_lateness_steps"] = dist(
        [r["lateness_steps"] for r in rows if r["lateness_steps"] is not None]
    )
    summary["dwell_lateness_seconds"] = dist(
        [r["lateness_steps"] / 120 for r in rows if r["lateness_steps"] is not None]
    )
    summary["max_consecutive_quiet_observations_through_deadline"] = dist(
        [r["max_consecutive_quiet_observations_through_deadline"] for r in rows]
    )
    summary["consecutive_quiet_observations_at_deadline"] = dist(
        [r["consecutive_quiet_observations_at_deadline"] for r in rows]
    )
    summary["arm_and_belt_first_dwell_completion_within_window_after_abort"] = dist(
        [
            r["arm_and_belt_first_dwell_completion_within_window"] - r["abort_step"]
            for r in rows
            if r["arm_and_belt_first_dwell_completion_within_window"] is not None
        ]
    )
    summary["finger_exceedance_intervals_from_plus60_to_completed_dwell"] = [
        {"intervals": [list(pair) for pair in pattern], "attempts": n}
        for pattern, n in sorted(
            Counter(
                tuple(
                    map(
                        tuple,
                        r["finger_exceedance_ranges_relative_to_abort_from_plus60"],
                    )
                )
                for r in rows
            ).items()
        )
    ]
    summaries = {}
    for marker in ("0", "60", "72", "before_first_dwell"):
        snapshots = [r["point_measurements"].get(marker) for r in rows]
        measures = [p["outside_limit"] for p in snapshots if p is not None]
        summaries[marker] = {
            "observed": len(measures),
            "quiet_with_all_channels_valid": sum(
                p is not None and not p["outside_limit"] and not p["invalid"]
                for p in snapshots
            ),
            "sets_of_channels_over_limit": dict(
                sorted(
                    Counter(",".join(sorted(m)) or "(none)" for m in measures).items()
                )
            ),
            "abs_speed_when_over_limit": {
                n: dist([abs(m[n]) for m in measures if n in m])
                for n in LIMITS
                if any(n in m for m in measures)
            },
        }
    summary["points"] = summaries
    return summary


def render(report, output):
    paragraphs = [
        "# Physical settling following the Cell A reference R06 failures",
        "",
        f"Secondary, retrospective diagnosis of {report['groups']['f2']['n']} Cell A F2 and {report['groups']['f3']['n']} Cell A F3 failures. Each arm has {len({r['scene_seed'] for r in report['rows']})} initial states and two repeats. Time zero is the recorded abort declaration. An interval of 12 elapsed simulation steps spans 13 boundary observations at 120 Hz.",
        "",
        "| Arm | Attempts | R05 receipt | First 12-sample completion | Registered dwell start | Registered dwell completion | Lateness |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, s in report["groups"].items():
        cells = []
        for f in (
            "r05_witness_after_abort_steps",
            "first12_complete_after_abort_steps",
            "dwell_start_after_abort_steps",
            "dwell_complete_after_abort_steps",
            "dwell_lateness_steps",
        ):
            distribution = s[f]
            cells.append(
                f"{distribution['min']}/{distribution['median']:g}/{distribution['max']}"
                if distribution["n"]
                else "unobserved"
            )
        paragraphs.append(f"| {label} | {s['n']} | " + " | ".join(cells) + " |")
    s = report["groups"]["Pooled"]
    lateness = s["dwell_lateness_seconds"]
    paragraphs += [
        "",
        "Table entries give minimum/median/maximum steps after abort, except lateness, which measures steps beyond the registered 72-step deadline. R05 covers operation cancellation, robot and gripper HOLD, a zero-speed belt target and an empty queue. The evidence joins native command IDs to accepted actuator receipts.",
        f"Among the {lateness['n']} attempts with an observed dwell, lateness was {1000 * lateness['min']:.1f}/{1000 * lateness['median']:.1f}/{1000 * lateness['max']:.1f} ms (minimum/median/maximum)."
        if lateness["n"]
        else "No attempt had an observed dwell by the horizon.",
        "",
        f"The retrospective search inspected {s['snapshots_inspected']:,} boundary observations from abort through first completed dwell or horizon. {s['never_reached_by_horizon']} attempts never reached the dwell, and {s['attempts_with_invalid_inspected_samples']} contained invalid measurements within the inspected interval.",
        "",
        "| Boundary | Channels exceeding their limits | Counts |",
        "|---|---|---:|",
    ]
    for k, label in (
        ("60", "Latest eligible dwell start, abort + 60"),
        ("72", "R06 deadline, abort + 72"),
        ("before_first_dwell", "Last boundary before actual qualifying dwell"),
    ):
        for names, count in s["points"][k]["sets_of_channels_over_limit"].items():
            paragraphs.append(f"| {label} | {names} | {count} |")
    paragraphs += [
        "",
        "Absolute speed limits are 0.020 rad/s for each arm joint, 0.003 m/s for each finger and 0.001 m/s for the belt.",
    ]
    for k, label in (
        ("60", "At abort + 60"),
        ("before_first_dwell", "Immediately before the qualifying dwell"),
    ):
        speed = s["points"][k]["abs_speed_when_over_limit"]
        measured = [
            f"finger {index} measured {speed[name]['min']:.6f}–{speed[name]['max']:.6f} m/s"
            for index, name in enumerate(FINGERS, start=1)
            if name in speed
        ]
        if measured:
            paragraphs.append(
                f"{label}, "
                + " and ".join(measured)
                + " in absolute speed among over-limit observations."
            )
    for pattern in s["finger_exceedance_intervals_from_plus60_to_completed_dwell"]:
        runs = ", ".join(
            f"+{lo} to +{hi}" if lo != hi else f"+{lo}"
            for lo, hi in pattern["intervals"]
        )
        paragraphs.append(
            f"From the last eligible start, at least one finger exceeded the speed bound on {runs} in {pattern['attempts']} attempts."
        )
    at_deadline = s["consecutive_quiet_observations_at_deadline"]
    other = s["arm_and_belt_first_dwell_completion_within_window_after_abort"]
    longest = s["max_consecutive_quiet_observations_through_deadline"]

    def observed_range(values):
        return (
            str(values["min"])
            if values["min"] == values["max"]
            else f"{values['min']}–{values['max']}"
        )

    paragraphs += [
        f"The longest uninterrupted stretch contained {observed_range(longest)} quiet observations within the registered window. The stretch ending at the deadline contained {observed_range(at_deadline)}.",
        f"Considering arm and belt speeds alone, the first full dwell ended {observed_range(other)} steps after abort in {other['n']} attempts."
        if other["n"]
        else "No attempt had a full arm-and-belt dwell in the registered window.",
    ]
    if other["n"] == s["n"] and s["attempts_with_invalid_inspected_samples"] == 0:
        paragraphs.append(
            "The measured finger velocities prevented the full-system stop dwell. A causal explanation of the residual finger movement would require additional evidence."
        )
    atomic_write(
        output / "r06-physical-diagnosis.md",
        ("\n".join(paragraphs) + "\n").encode("utf-8"),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign", type=Path, required=True, help="Sealed canonical-v1 directory"
    )
    parser.add_argument(
        "--primary",
        type=Path,
        required=True,
        help="Publication output from publish_primary.py",
    )
    args = parser.parse_args()
    output = args.primary.resolve()
    require(
        not output.is_relative_to(args.campaign.resolve()),
        "Choose an output directory outside the sealed campaign",
    )
    index = output / "first-attempt-index.json"
    primary = read(output / "primary-review.json")
    require(
        primary["source_hash"] == source_hash()
        and file_hash(args.campaign / "campaign.plan.json")
        == primary["input_proof"]["plan_file_sha256"]
        and file_hash(index) == primary["source_index_file_sha256"],
        "Primary inputs differ",
    )
    comparison = read(output / "report-comparison.json")
    require(
        comparison["summary_sha256"] == file_hash(output / "primary-review.json")
        and all(
            comparison["reports"][name]["status"] == "MATCH"
            for name in ("Windows", "WSL")
        ),
        "Both primary report checks must match",
    )
    selected = [
        i
        for i in read(index)
        if i["controller"] == "reference" and i["r06_verdict"] == "FAIL"
    ]
    require(
        selected
        and all(i["cell"] == "cell_a" and i["arm"] in ("f2", "f3") for i in selected),
        "This physical diagnostic is defined for Cell A F2/F3 reference failures; a case outside its scope requires separate analysis.",
    )
    rows = [diagnose_trial(args.campaign, i) for i in selected]
    report = {
        "schema": "cellio.canonical-r06-secondary/1.0",
        "source_hash": primary["source_hash"],
        "plan_hash": primary["plan_hash"],
        "protocol_hash": primary["protocol_hash"],
        "source_index_file_sha256": file_hash(index),
        "method_file_sha256": file_hash(Path(__file__)),
        "step_hz": 120,
        "limits": LIMITS,
        "scope": "All and only reference-controller R06 failures. Exploratory descriptive analysis of logged native simulation.",
        "inspection": "Checks the compressed bytes of every selected trace and its sealed online rule file. Inspects each post-abort boundary through first qualifying dwell or horizon, including abort+72. A speed above its absolute limit or an invalid observation breaks the contiguous dwell.",
        "groups": {
            "Pooled": describe(rows),
            **{
                arm: describe([r for r in rows if r["arm"] == arm])
                for arm in ("f2", "f3")
            },
        },
        "rows": rows,
    }
    save(output / "r06-physical-diagnosis.json", report)
    render(report, output)
    evidence_names = (
        "coordinator-first-attempts.jsonl",
        "first-attempt-index.json",
        "matched-mutant-pairs.json",
        "primary-review.json",
        "primary-review.md",
        "r06-physical-diagnosis.json",
        "r06-physical-diagnosis.md",
        "report-comparison.json",
    )
    lines = [f"{file_hash(output / name)}  {name}\n" for name in evidence_names]
    atomic_write(output / "checksums.sha256", "".join(lines).encode("ascii"))
    print("JSON SHA256", file_hash(output / "r06-physical-diagnosis.json"))


if __name__ == "__main__":
    main()
