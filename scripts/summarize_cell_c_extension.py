"""Independently re-open Cell C native traces and summarize the registered design."""

import argparse
import json
import math
import subprocess
import sys
import time
from collections import Counter
from itertools import pairwise
from pathlib import Path

from run_cell_c_extension import PROTOCOL, ROOT, digest, read, write


def require(value, message):
    if not value:
        raise ValueError(message)


def near(x, y, limit, message):
    require(
        math.isfinite(x) and abs(x - y) <= limit,
        f"{message}: {x} vs {y} (limit {limit})",
    )


def inspect_episode(folder, allocation, protocol):
    from cellio.inspection.study.verify_inspection import (
        physical_region,
        verify_replay,
        verify_seat,
    )

    result, events = (read(folder / f) for f in ("result.json", "events.json"))
    trace = [
        json.loads(s)
        for s in (folder / "motion.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    hashes = {
        name: digest(folder / name)
        for name in (
            "events.json",
            "result.json",
            "motion.jsonl",
            "live.usda",
            "replay.usda",
        )
    }
    require(
        result["motion_trace_sha256"] == hashes["motion.jsonl"],
        "trace changed after run completion",
    )
    recipe = result["recipe"]
    require(
        recipe == read(folder.parent.parent / "recipe.json"),
        "recipe echo differs from allocation",
    )
    require(
        all(recipe[k] == allocation[k] for k in allocation),
        "case diverged from its design",
    )
    require(recipe["protocol_sha256"] == digest(PROTOCOL), "protocol mismatch")
    require(
        result["status"] == "completed"
        and not result["diagnostic"]
        and result["condition"] == recipe["condition"],
        "unexpected native completion marker",
    )
    physics = result["physics"]
    require(
        physics["step_hz"] == 120 and physics["steps_per_pose"] == 4, "physics clock"
    )
    near(physics["gravity_m_s2"], 9.81, 0.001, "gravity")
    require(
        result["fps"] == 30 and len(trace) == result["frames"] + 1,
        "missing motion frames",
    )
    require(all(i == frame["frame"] for i, frame in enumerate(trace)), "trace sequence")
    model_manifest = read(folder / "stage/stage-manifest.json")
    for name, h in model_manifest["files"].items():
        require(digest(folder / "stage" / name) == h, f"scene asset changed {name}")
    require(
        result["source_sha256"]["cell_c_inspection.usda"]
        == model_manifest["files"]["cell_c_inspection.usda"],
        "native stage digest mismatch",
    )
    require(len(result["parts"]) == 2, "two parts required")
    expected = []
    for part in ("P1", "P2"):
        expected.extend(
            (part, e)
            for e in (
                "infeed_index",
                "grasp",
                "release",
                "inspection",
                "grasp",
                "release",
                "output_settled",
            )
        )
        if part == "P1":
            expected.append((part, "external_takeaway"))
    require(
        [(e["part"], e["event"]) for e in events] == expected
        and all(
            0 <= a["frame"] <= b["frame"] < len(trace) for a, b in pairwise(events)
        ),
        "event order",
    )
    inspections = [e for e in events if e["event"] == "inspection"]
    releases = [e for e in events if e["event"] == "release"]
    outputs = [e for e in events if e["event"] == "output_settled"]
    witness = []
    first_ok = None
    for i, (row, inspection, output) in enumerate(
        zip(result["parts"], inspections, outputs, strict=True)
    ):
        part = f"P{i + 1}"
        require(row["part"] == inspection["part"] == output["part"] == part, "identity")
        require(
            (inspection["probe"], inspection["reference"], inspection["decision"])
            == (row["probe"], row["geometry_reference"], row["decision"]),
            "result/event mismatch",
        )
        datum = protocol["nominal_mm"] + recipe["errors_mm"][i]
        ref = row["geometry_reference"]
        probe = row["probe"]
        geo_bin = (
            "OK"
            if abs(ref["height_mm"] - protocol["nominal_mm"])
            <= protocol["tolerance_mm"]
            else "NG"
        )
        near(
            ref["height_mm"],
            datum,
            protocol["reference_geometry_error_limit_mm"],
            "geometry vs drawing (mm)",
        )
        near(
            probe["height_mm"],
            ref["height_mm"],
            protocol["probe_to_drawing_limit_mm"],
            "probe vs geometry (mm)",
        )
        require(
            geo_bin == protocol["sequences"][recipe["sequence"]]["truth"][i]
            and (geo_bin == "OK")
            == probe["within_tolerance"]
            == ref["within_tolerance"],
            "dimension class",
        )
        ing = row["infeed_seat"]
        require(ing["seated"] and ing["region"] == "infeed", "missing source settle")
        pos_mm = [1000 * x for x in ing["position_m"]]
        error = math.dist(
            pos_mm[:2],
            [c + d for c, d in zip((-440.0, -248.0), recipe["infeed_offsets_mm"][i])],
        )
        require(
            error <= protocol["source_witness_xy_error_limit_mm"],
            f"unrealized XY offset: {error}",
        )
        verify_seat(row["fixture_seat"], "fixture", part)
        actual = row["physical_output"]
        observed = physical_region(actual["position_m"], actual["orientation_wxyz"])
        require(
            observed in ("OK", "NG") and observed == output["observed"],
            "unidentified observed bin",
        )
        verify_seat(actual, observed, part)
        at = output["frame"]
        check_part = trace[at]["parts"][part]
        require(
            physical_region(check_part["xyz"], check_part["wxyz"]) == observed,
            "trace/pose bin mismatch",
        )
        near(
            math.dist(check_part["xyz"], actual["position_m"]),
            0.0,
            1e-6,
            "final measured pose (m)",
        )
        speed = 30 * math.dist(check_part["xyz"], trace[at - 1]["parts"][part]["xyz"])
        require(speed < 0.0055, "still moving at output")
        release = releases[2 * i + 1]
        require(
            releases[2 * i]["station"] == "fixture"
            and release["station"] == row["decision"]["route"],
            "release route",
        )
        drop = (release["position_m"][2] - actual["position_m"][2]) * 1000
        require(1.5 < drop < 4.5, "missing independent drop to bin")
        decision = row["decision"]
        require(
            decision["part_id"] == part
            and decision["fresh"] == (decision["result_part_id"] == part),
            "ID consistency",
        )
        if i == 0:
            first_ok = geo_bin == "OK"
        expected_stale = bool(i and recipe["condition"] == "stale_ok" and first_ok)
        expected_bin = "OK" if expected_stale else geo_bin
        endpoint = (
            observed == expected_bin
            and decision["route"] == expected_bin
            and decision["result_part_id"] == ("P1" if expected_stale else part)
        )
        require(
            row["correct_destination"] == (observed == geo_bin),
            "native correctness flag",
        )
        witness.append(
            {
                "part_id": part,
                "probe": probe,
                "geometry_reference": ref,
                "decision": decision,
                "physical_output": actual,
                "fixture_seat": row["fixture_seat"],
                "infeed_seat": ing,
                "true_bin": geo_bin,
                "expected_fault_prediction": endpoint,
                "physical_misroute": observed != geo_bin,
                "stale_result": not decision["fresh"],
                "measured_initial_xy_error_mm": error,
                "release_to_tray_mm": drop,
                "trace_final_speed_m_s": speed,
            }
        )
    m = result["motion"]
    arm = m["clearance"]
    require(
        m["samples"] == arm["frames"] == result["frames"]
        and not arm["unresolved_pairs"]
        and arm["minimum_sampled_lower_bound_m"] > 0
        and m["max_tcp_error_m"] <= 0.0005
        and m["max_orientation_error_rad"] <= 0.005
        and m["minimum_joint_limit_margin_rad"] >= 0
        and m["max_stage_fk_difference_m"] <= 1e-6,
        "kinematic clearance or motion completeness",
    )
    replay = verify_replay(folder, trace, inspections, result["parts"])
    return {
        "schema": 1,
        "case_id": recipe["id"],
        "condition": recipe["condition"],
        "sequence": recipe["sequence"],
        "placement": recipe["placement"],
        "frames": len(trace),
        "parts": witness,
        "replay": replay,
        "clearance_m": arm["minimum_sampled_lower_bound_m"],
        "sha256": hashes,
        "model_manifest_sha256": digest(folder / "stage/stage-manifest.json"),
        "robot_lock_sha256": model_manifest["robot_lock_sha256"],
        "source_sha256": result["source_sha256"],
    }


def audit(attempt, allocation, protocol):
    from cellio.inspection.study import verify_inspection
    from cellio.inspection.study.verify_inspection import verify_geometry_audits

    case = read(allocation)
    recipe = read(attempt.parent / "recipe.json")
    row = next(r for r in case["rows"] if r["id"] == recipe["id"])
    episode = attempt / "episode"
    report = {
        "case_id": recipe["id"],
        "script_sha256": digest(Path(__file__)),
        "replay_verifier_sha256": digest(Path(verify_inspection.__file__)),
    }
    try:
        report.update(inspect_episode(episode, row, protocol), core_verified=True)
        for path, expected in case["source_sha256"].items():
            filename = Path(path).name
            if filename in report["source_sha256"]:
                require(
                    expected == report["source_sha256"][filename],
                    f"changed native source {filename}",
                )
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        RuntimeError,
        AssertionError,
    ) as exc:
        report.update(core_verified=False, error=f"{type(exc).__name__}: {exc}")
    if report.get("core_verified"):
        try:
            update = {
                "folder": str(episode),
                "frames": report["frames"],
                "sha256": report["sha256"],
            }
            verify_geometry_audits(update, None)
            report["geometry_audit"] = update["additional_geometry_audits"]
            report["geometry_verified"] = True
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OSError,
            RuntimeError,
            AssertionError,
        ) as exc:
            report.update(
                geometry_verified=False, geometry_error=f"{type(exc).__name__}: {exc}"
            )
            # Preserve a failed addendum as an observed geometric diagnostic.
            for item in (
                "self-clearance.json",
                "gripper-mesh.json",
                "payload-clearance.json",
            ):
                if (episode / item).is_file():
                    report["sha256"][item] = digest(episode / item)
    write(attempt / "audit.json", report)
    print(
        f"{recipe['id']} core {report['core_verified']}, geometry {report.get('geometry_verified')}, {report.get('geometry_error', report.get('error', ''))}",
        flush=True,
    )


def watch(root, python, wait):
    from cellio.inspection.study import verify_inspection

    lock = root / "allocation.json"
    case_ids = [r["id"] for r in read(lock)["rows"]]
    analysis_sha = digest(Path(__file__))
    replay_sha = digest(Path(verify_inspection.__file__))
    while True:
        terminal = 0
        for name in case_ids:
            attempts = sorted((root / name).glob("attempt-[0-9][0-9]"))
            if not attempts:
                continue
            for p in attempts:
                if not (p / "status.json").exists():
                    continue
                status = read(p / "status.json")
                report = read(p / "audit.json") if (p / "audit.json").is_file() else {}
                needs_review = (
                    report.get("script_sha256") != analysis_sha
                    or report.get("replay_verifier_sha256") != replay_sha
                )
                if status["status"] == "completed" and needs_review:
                    rc = subprocess.run(
                        [
                            python,
                            str(Path(__file__).resolve()),
                            "one",
                            "--root",
                            str(root),
                            "--attempt",
                            str(p),
                        ],
                        cwd=ROOT,
                        check=False,
                    ).returncode
                    if rc:
                        raise RuntimeError(f"Audit process failed for {p}: exit {rc}")
            if (attempts[-1] / "status.json").exists():
                terminal += 1
        if not wait or terminal == len(case_ids):
            break
        time.sleep(8)


def paired_contrasts(first_observations, protocol):
    by_key = {
        (o["sequence"], o["placement"], o["condition"], o["part_id"]): o
        for o in first_observations
    }
    pairs = []
    for sequence in protocol["sequences"]:
        for placement in protocol["placement_profiles"]:
            if any(
                (sequence, placement, c, p) not in by_key
                for c in protocol["conditions"]
                for p in ("P1", "P2")
            ):
                continue
            normal = [by_key[(sequence, placement, "normal", p)] for p in ("P1", "P2")]
            stale = [by_key[(sequence, placement, "stale_ok", p)] for p in ("P1", "P2")]
            pairs.append(
                {
                    "sequence": sequence,
                    "placement": placement,
                    "normal_bins": [o["physical_output"]["region"] for o in normal],
                    "stale_bins": [o["physical_output"]["region"] for o in stale],
                    "normal_consumed_result_ids": [
                        o["decision"]["result_part_id"] for o in normal
                    ],
                    "stale_consumed_result_ids": [
                        o["decision"]["result_part_id"] for o in stale
                    ],
                    "max_paired_probe_difference_mm": max(
                        abs(a["probe"]["height_mm"] - b["probe"]["height_mm"])
                        for a, b in zip(normal, stale)
                    ),
                    "max_paired_infeed_position_difference_mm": 1000
                    * max(
                        math.dist(
                            a["infeed_seat"]["position_m"],
                            b["infeed_seat"]["position_m"],
                        )
                        for a, b in zip(normal, stale)
                    ),
                }
            )
    return pairs


def preflight_ledger(previous):
    lock = read(previous / "allocation.json")
    attempts = []
    for row in lock["rows"]:
        for p in sorted((previous / row["id"]).glob("attempt-*")):
            if not (p / "start.json").is_file():
                continue
            files = {
                name: digest(p / name)
                for name in ("start.json", "stdout.log", "stderr.log", "status.json")
                if (p / name).is_file()
            }
            started = read(p / "start.json")
            log = (p / "stderr.log").read_text(encoding="utf-8", errors="replace")
            detail = read(p / "status.json") if (p / "status.json").is_file() else {}

            events_path = p / "episode/events.json"
            inspections = (
                sum(event["event"] == "inspection" for event in read(events_path))
                if events_path.is_file()
                else 0
            )

            attempts.append(
                {
                    "id": row["id"],
                    "started_utc": started["started_utc"],
                    "exit_code": detail.get("exit_code"),
                    "status_record_present": bool(detail),
                    "observed_0xc0000139": "0xc0000139" in log.lower(),
                    "inspection_events": inspections,
                    "sha256": files,
                }
            )
    return {
        "allocation_sha256": digest(previous / "allocation.json"),
        "planned": len(lock["rows"]),
        "startup_attempts": len(attempts),
        "unstarted": len(lock["rows"]) - len(attempts),
        "source_sha256": lock["source_sha256"],
        "attempts": attempts,
        "reason": "Kit startup failed after standalone USD had already been loaded; revised runtime starts Kit before composing USD. Same scientific protocol and placement recipes.",
    }


def export(root, out, previous=None):
    allocation = read(root / "allocation.json")
    rows = []
    observations = []
    for registered in allocation["rows"]:
        case = {
            "id": registered["id"],
            "sequence": registered["sequence"],
            "placement": registered["placement"],
            "condition": registered["condition"],
            "attempts": [],
        }
        for attempt in sorted((root / registered["id"]).glob("attempt-[0-9][0-9]")):
            status = (
                read(attempt / "status.json")
                if (attempt / "status.json").is_file()
                else {"status": "incomplete"}
            )
            info = {
                "attempt_id": case["id"] + "/" + attempt.name,
                "status": status["status"],
                "wall_seconds": status.get("wall_seconds"),
            }
            if (attempt / "audit.json").is_file():
                a = read(attempt / "audit.json")
                info.update(
                    {
                        k: a[k]
                        for k in (
                            "core_verified",
                            "geometry_verified",
                            "frames",
                            "clearance_m",
                            "replay",
                            "sha256",
                            "geometry_audit",
                            "error",
                            "geometry_error",
                            "script_sha256",
                            "replay_verifier_sha256",
                            "model_manifest_sha256",
                            "robot_lock_sha256",
                            "source_sha256",
                        )
                        if k in a
                    }
                )
                if a.get("geometry_verified"):
                    payload = read(attempt / "episode/payload-clearance.json")
                    info["geometry_audit"].update(
                        payload_gauge_hardware_lower_bound_m=payload[
                            "minimum_gauge_sampled_bound_m"
                        ],
                        payload_nonmating_lower_bound_m=payload[
                            "minimum_nonmating_sampled_bound_m"
                        ],
                        payload_interpolated_poses=sum(
                            i["interpolated_poses"] for i in payload["intervals"]
                        ),
                        closest_payload_interface=payload["closest"],
                    )
                for part in a.get("parts", []):
                    obs = {k: case[k] for k in ("sequence", "placement", "condition")}
                    observations.append(
                        {
                            **obs,
                            "case_id": case["id"],
                            "attempt_id": info["attempt_id"],
                            **part,
                        }
                    )
            case["attempts"].append(info)
        rows.append(case)
    out.mkdir(parents=True, exist_ok=True)
    obs_path = out / "observations.jsonl"
    pending = obs_path.with_suffix(".partial")
    pending.write_text(
        "".join(
            json.dumps(o, separators=(",", ":"), allow_nan=False) + "\n"
            for o in observations
        ),
        encoding="utf-8",
        newline="\n",
    )
    pending.replace(obs_path)
    first = [c["attempts"][0] for c in rows if c["attempts"]]
    first_ids = {a["attempt_id"] for a in first}
    first_o = [o for o in observations if o["attempt_id"] in first_ids]
    summary = {
        "protocol_sha256": digest(PROTOCOL),
        "allocation_sha256": digest(root / "allocation.json"),
        "frozen_sources": allocation["source_sha256"],
        "planned_runs": len(rows),
        "started_first_attempts": len(first),
        "completed_first_attempts": sum(x["status"] == "completed" for x in first),
        "core_verified_first_attempts": sum(
            bool(x.get("core_verified")) for x in first
        ),
        "geometry_verified_first_attempts": sum(
            bool(x.get("geometry_verified")) for x in first
        ),
        "core_verified_parts_first_attempts": len(first_o),
        "observations_sha256": digest(obs_path),
        "expected_endpoint_count": sum(o["expected_fault_prediction"] for o in first_o),
        "physical_misroutes": dict(
            Counter(o["condition"] for o in first_o if o["physical_misroute"])
        ),
        "stale_tags": dict(
            Counter(o["condition"] for o in first_o if o["stale_result"])
        ),
        "stale_and_correct_bin": dict(
            Counter(
                o["condition"]
                for o in first_o
                if o["stale_result"] and not o["physical_misroute"]
            )
        ),
        "cases": rows,
    }
    summary["paired_contrasts"] = paired_contrasts(first_o, read(PROTOCOL))
    summary["completed_matched_pairs"] = len(summary["paired_contrasts"])
    geometry = [a for a in first if a.get("geometry_verified")]
    if geometry:
        audit = [a["geometry_audit"] for a in geometry]
        summary["sampled_geometry"] = {
            "verified_runs": len(geometry),
            "checked_grasps": sum(a["checked_grasps"] for a in audit),
            "sampled_link_frames": sum(a["frames"] for a in geometry),
            "sampled_nonadjacent_link_pair_frames": sum(
                a["frames"] * a["geometry_audit"]["self_pairs_per_frame"]
                for a in geometry
            ),
            "interpolated_carried_part_poses": sum(
                a["payload_interpolated_poses"] for a in audit
            ),
            "min_arm_equipment_bound_m": min(a["clearance_m"] for a in geometry),
            "min_self_clearance_bound_m": min(
                a["self_clearance_lower_bound_m"] for a in audit
            ),
            "min_payload_gauge_hardware_bound_m": min(
                a["payload_gauge_hardware_lower_bound_m"] for a in audit
            ),
            "min_payload_nonmating_bound_m": min(
                a["payload_nonmating_lower_bound_m"] for a in audit
            ),
            "min_payload_pin_radial_bound_m": min(
                a["payload_pin_radial_lower_bound_m"] for a in audit
            ),
            "min_payload_including_precision_fit_bound_m": min(
                a["payload_sampled_clearance_lower_bound_m"] for a in audit
            ),
        }
    if first_o:
        summary["max_probe_minus_geometry_abs_mm"] = max(
            abs(o["probe"]["height_mm"] - o["geometry_reference"]["height_mm"])
            for o in first_o
        )
        summary["max_actual_minus_commanded_infeed_xy_mm"] = max(
            o["measured_initial_xy_error_mm"] for o in first_o
        )
        summary["min_release_to_tray_mm"] = min(
            o["release_to_tray_mm"] for o in first_o
        )
        summary["max_release_to_tray_mm"] = max(
            o["release_to_tray_mm"] for o in first_o
        )
    if previous is not None:
        summary["prior_runtime_revision"] = preflight_ledger(previous)
    pending = out / "summary.partial"
    pending.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    pending.replace(out / "summary.json")
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k
                not in (
                    "cases",
                    "frozen_sources",
                    "paired_contrasts",
                    "prior_runtime_revision",
                )
            }
        ),
        flush=True,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["one", "collect", "export"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--attempt", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument(
        "--previous",
        type=Path,
        help="Optional prior startup-only revision to retain in the denominator ledger",
    )
    p.add_argument("--wait", action="store_true")
    args = p.parse_args()
    root = args.root.resolve()
    if args.action == "one":
        return audit(args.attempt.resolve(), root / "allocation.json", read(PROTOCOL))
    if args.action == "collect":
        return watch(root, sys.executable, args.wait)
    if not args.out:
        p.error("export requires --out")
    return export(
        root, args.out.resolve(), args.previous.resolve() if args.previous else None
    )


if __name__ == "__main__":
    main()
