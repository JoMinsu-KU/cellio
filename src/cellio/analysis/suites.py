"""Suite-specific independent units and resource summaries."""

from collections import defaultdict
from math import acos, dist
from pathlib import Path

from .campaign import lines
from .replay import read
from .statistics import distribution, plateau, ratio


def performance(trials):
    groups = defaultdict(list)
    for t in trials:
        groups[(t.row["cell"], t.row.get("benchmark_mode") or "standard")].append(t)
    output = {}
    for (cell, mode), items in groups.items():
        eligible = [t for t in items if t.status == "COMPLETED" and t.measurement]

        def per_metric(key, unit, factor=1, eligible=eligible, items=items):
            good = [t for t in eligible if type(t.performance.get(key)) in (float, int)]
            return distribution(
                [t.performance[key] * factor for t in good],
                unit,
                [t.attempt_id for t in good],
                {"no_verified_measurement": len(items) - len(good)},
            )

        def intervals(attr, eligible=eligible, items=items):
            good = [t for t in eligible if getattr(t.measurement, attr)]
            return distribution(
                (v for t in good for v in getattr(t.measurement, attr)),
                "ms / four physics steps",
                [t.attempt_id for t in good],
                {"no_verified_measurement": len(items) - len(good)},
            )

        values = {
            "critical_path_all": intervals("intervals"),
            "critical_path_active": intervals("active_intervals"),
        }
        if mode in ("B1", "B2"):
            for timing in values.values():
                timing["p99_target_ms"] = 3.33
                timing["p99_target_assessment"] = (
                    "INSUFFICIENT_EVIDENCE" if timing["p99"] is None or len(timing["source_artifact_ids"]) != len(items)
                    else "MEETS_TARGET" if timing["p99"] <= 3.33 else "ABOVE_TARGET"
                )
        for key, unit, factor in (
            ("startup_wall_ns", "s", 1e-9),
            ("running_wall_ns", "s", 1e-9),
            ("warmup_wall_ns", "s", 1e-9),
            ("finalization_wall_ns", "s", 1e-9),
            ("hard_reload_wall_ns", "s", 1e-9),
            ("rtf", "sim s / wall s", 1),
            ("peak_rss_bytes", "bytes", 1),
            ("peak_vram_bytes", "bytes", 1),
            ("end_gpu_device_used_bytes", "device bytes", 1),
            ("writer_max_backlog", "records", 1),
            ("trace_bytes", "gzip bytes", 1),
        ):
            values[key] = per_metric(key, unit, factor)
        output[cell + "." + mode] = values
    return output


def compare_s0(a, b):
    if not a or not b or not a.get("complete") or not b.get("complete"):
        return {"verdict": "UNKNOWN", "reason": "missing/invalid initial snapshot"}
    errors = {
        "position_m": 0.0,
        "orientation_rad": 0.0,
        "linear_mps": 0.0,
        "angular_rps": 0.0,
        "revolute_rad": 0.0,
        "prismatic_m": 0.0,
    }
    for key in ("bodies", "joints"):
        if not a.get(key) or set(a[key]) != set(b.get(key, {})):
            return {"verdict": "UNKNOWN", "reason": f"missing {key}"}
        for name, left in a[key].items():
            right = b[key][name]
            if not left.get("valid") or not right.get("valid"):
                return {"verdict": "UNKNOWN", "reason": f"invalid {name}"}
            if key == "bodies":
                for field, metric in (
                    ("position_m", "position_m"),
                    ("linear_velocity_mps", "linear_mps"),
                    ("angular_velocity_rps", "angular_rps"),
                ):
                    errors[metric] = max(
                        errors[metric], dist(left[field], right[field])
                    )
                q = abs(
                    sum(
                        x * y
                        for x, y in zip(
                            left["orientation_wxyz"],
                            right["orientation_wxyz"],
                            strict=True,
                        )
                    )
                )
                errors["orientation_rad"] = max(
                    errors["orientation_rad"], 2 * acos(min(1.0, q))
                )
            else:
                if left["kind"] != right["kind"]:
                    return {"verdict": "FAIL", "reason": "joint kind changed: " + name}
                metric = (
                    "prismatic_m" if left["kind"] == "prismatic" else "revolute_rad"
                )
                errors[metric] = max(
                    errors[metric], abs(left["position"] - right["position"])
                )
    bounds = {
        "position_m": 0.0001,
        "orientation_rad": 0.001,
        "linear_mps": 0.001,
        "angular_rps": 0.001,
        "revolute_rad": 0.0001,
        "prismatic_m": 0.0001,
    }
    return {
        "verdict": "PASS" if all(errors[k] <= bounds[k] for k in errors) else "FAIL",
        "max_error": errors,
        "limits": bounds,
    }


def reset_chains(trials):
    groups = defaultdict(dict)
    for t in trials:
        if t.row.get("chain_id"):
            groups[t.row["chain_id"]][t.row["chain_slot"]] = t
    rows = []
    for ident, slots in groups.items():
        row = {
            "chain_id": ident,
            "status": "INCOMPLETE",
            "source_artifact_ids": [t.attempt_id for t in slots.values() if t.eval],
        }
        rows.append(row)
        if set(slots) != {"control", "predecessor", "post_reset"}:
            continue
        control, prior, post = (
            slots[s] for s in ("control", "predecessor", "post_reset")
        )
        if not all(t.eval for t in (control, prior, post)):
            continue
        kind = prior.row["test_control"]["kind"]
        if kind != "full_h" and (
            prior.fault.get("actual_start") is None
            or kind == "fault_end_offset"
            and (prior.fault.get("actual_end") is None or not prior.flags()["N_clear"])
        ):
            row["status"] = "SKIPPED_PRECONDITION"
            continue
        if kind != "full_h":
            origin = (
                prior.fault["actual_end"]
                if kind == "fault_end_offset"
                else prior.fault["actual_start"]
            )
            if (
                prior.status != "CANCELLED"
                or prior.eval["end_step"] != origin + prior.row["test_control"]["steps"]
            ):
                row["status"] = "FAIL"
                row["reason"] = "predecessor did not stop at the frozen reset boundary"
                continue
        s0 = compare_s0(
            read(control.path / "initial_snapshot.json"),
            read(post.path / "initial_snapshot.json"),
        )
        software = next(
            (
                r["verdict"]
                for r in post.eval["requirements"]
                if ".r16." in r["requirement_id"]
            ),
            None,
        )
        row.update(
            physical_s0=s0,
            software_clear=software,
            post_reset_goal=post.outcome,
            control_goal=control.outcome,
        )
        if (
            software in (None, "UNKNOWN", "NOT_APPLICABLE")
            or s0["verdict"] == "UNKNOWN"
        ):
            row["status"] = "UNKNOWN"
        else:
            row["status"] = (
                "PASS"
                if (
                    s0["verdict"] == software == "PASS"
                    and control.outcome == post.outcome == "SUCCESS"
                    and prior.status in ("COMPLETED", "CANCELLED")
                )
                else "FAIL"
            )
    return {
        "chains": rows,
        "unit": "chain",
        "recovery": ratio(
            sum(r["status"] == "PASS" for r in rows),
            len(rows),
            [v for r in rows for v in r["source_artifact_ids"]],
        ),
    }


def soak(trials, elapsed_seconds):
    data = [t for t in trials if t.row.get("chain_slot") == "measurement"]
    warm = sum(t.row.get("chain_slot") == "warmup" for t in trials)
    done = sum(t.status == "COMPLETED" for t in data)
    errors = sum(t.flags()["N_error"] for t in trials)
    return {
        "warmup_rows": warm,
        "measured_rows": len(data),
        "measured_completed": done,
        "elapsed_wall_seconds": elapsed_seconds,
        "stop_rule": "completed >= 200 AND elapsed_wall_seconds >= 7200",
        "protection_limit_seconds": 86400,
        "infrastructure_errors": errors,
        "status": "FAIL"
        if errors
        else "PASS"
        if done >= 200 and elapsed_seconds is not None and elapsed_seconds >= 7200
        else "INCOMPLETE",
        "rss": plateau(
            [(i, t.performance.get("end_rss_bytes")) for i, t in enumerate(data)], "rss"
        ),
        "vram": plateau(
            [(i, t.performance.get("end_vram_bytes")) for i, t in enumerate(data)],
            "vram",
        ),
        "source_artifact_ids": [t.attempt_id for t in data if t.eval],
    }


def soak_evidence(directory, trials):
    """Validate a contiguous, timed soak from one long-lived worker."""
    directory = Path(directory)
    summary = directory / "soak.result.json"
    result = soak(trials, None)
    if not summary.is_file():
        return {
            **result,
            "reason": "supervisor timing and closed worker evidence unavailable",
        }
    try:
        timing = read(summary)
        pid, start, stop = (
            timing[k]
            for k in ("worker_pid", "measurement_start_ns", "measurement_end_ns")
        )
        if (
            not all(type(v) is int and v > 0 for v in (pid, start, stop))
            or stop < start
        ):
            raise ValueError("Invalid monotonic window or worker PID")
        elapsed = (stop - start) / 1e9
        if timing["wall_seconds"] != elapsed or not 7200 <= elapsed <= 86400:
            raise ValueError("Elapsed time differs from the permitted measured window")
        warmup = [t for t in trials if t.row["chain_slot"] == "warmup"]
        data = [t for t in trials if t.row["chain_slot"] == "measurement"]
        if (
            warmup + data != list(trials)
            or len(warmup) != timing["warmup"]
            or len(warmup) != 10
            or len(data) != timing["completed"]
            or len(data) < 200
        ):
            raise ValueError("Warmup/measurement counts differ from the stop rule")
        if any(t.status != "COMPLETED" or t.error or not t.eval for t in trials):
            raise ValueError(
                "Every warmup and measured attempt needs a sealed, completed artifact"
            )
        ledger = list(lines(directory / "ledger.jsonl"))
        ordered = [e["monotonic_ns"] for e in ledger]
        if any(type(t) is not int for t in ordered) or ordered != sorted(ordered):
            raise ValueError("Invalid ledger monotonic clock sequence")
        launch = [e for e in ledger if e.get("pid") is not None]
        closed = [e for e in ledger if e["state"] == "COMPLETED"]
        planned = [e for e in ledger if e["state"] == "PLANNED"]
        ids = [t.attempt_id for t in trials]
        for entries in (launch, closed, planned):
            if [e["attempt_id"] for e in entries] != ids:
                raise ValueError(
                    "Ledger attempt sequence differs from the frozen generator"
                )
        if any(e["pid"] != pid for e in launch):
            raise ValueError("Soak process changed")
        if (
            not closed[len(warmup) - 1]["monotonic_ns"]
            <= start
            <= planned[len(warmup)]["monotonic_ns"]
        ):
            raise ValueError("Measurement started outside the post-warmup boundary")
        workers = [
            w.parent
            for w in (directory / ".workers").glob("*/process.json")
            if read(w).get("pid") == pid
        ]
        if len(workers) != 1:
            raise ValueError("Cannot identify a unique soak process")
        worker = workers[0]
        beat, summary_exit = (
            read(worker / "heartbeat.json"),
            read(worker / "result.json"),
        )
        if (
            beat.get("pid") != pid
            or beat.get("state") != "EXITED"
            or not closed[-1]["monotonic_ns"] <= stop <= beat["monotonic_ns"]
            or summary_exit.get("completion_status") != "COMPLETED"
            or summary_exit.get("episode_count") != len(ids)
        ):
            raise ValueError("Worker shutdown or measurement end is unverified")
        worker_rows = list(lines(worker / "results.jsonl"))
        for i, (t, entry) in enumerate(zip(trials, worker_rows, strict=True)):
            if (
                entry.get("attempt_id") != t.attempt_id
                or entry.get("run_id") != t.row["run_id"]
                or entry.get("generation") != i
                or entry.get("completion_status") != "COMPLETED"
                or read(t.path / "manifest.start.json").get("generation") != i
            ):
                raise ValueError(
                    "Attempt or generation differs from the long-lived worker record"
                )
        return {
            **soak(trials, elapsed),
            "worker_pid": pid,
            "worker_directory": str(worker),
            "measurement_start_ns": start,
            "measurement_end_ns": stop,
        }
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return {**result, "status": "FAIL", "reason": str(exc)}
