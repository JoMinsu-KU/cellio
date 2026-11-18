"""Generate a report from the frozen run plan and independently replayed artifacts."""

import sys
from collections import Counter, defaultdict
from pathlib import Path

from cellio.analysis.campaign import group_metrics, load_campaign
from cellio.analysis.comparisons import confusion, mutant_pairs, overhead
from cellio.analysis.measurements import histogram_summary
from cellio.analysis.replay import read
from cellio.analysis.suites import performance, reset_chains, soak
from cellio.io import file_hash, write_json

from .render import render_report, timeline


def oracle_corpus():
    for folder in (
        Path(__file__).resolve().parents[3] / "fixtures" / "oracle",
        Path(sys.prefix) / "share" / "cellio" / "fixtures" / "oracle",
    ):
        file = folder / "gold.json"
        if file.is_file():
            if (
                file_hash(file)
                != (folder / "gold.sha256").read_text(encoding="ascii").split()[0]
            ):
                raise ValueError("Oracle gold checksum does not match frozen corpus")
            return {
                "status": "MEASURED",
                "sha256": file_hash(file),
                **confusion(read(file)),
            }
    return {"status": "NOT_RUN", "reason": "frozen oracle corpus unavailable"}


def report(campaign_dir, output_dir):
    source, out = Path(campaign_dir).resolve(), Path(output_dir).resolve()
    plan, trials, extra = load_campaign(source)
    for trial in trials:
        if trial.path and out.is_relative_to(trial.path):
            raise ValueError(
                "Select a report destination outside raw attempt directories"
            )
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    cells = defaultdict(list)
    delays = defaultdict(Counter)
    delay_sources = defaultdict(list)
    for t in trials:
        r = t.row
        groups[r["cell"], r["arm"], r["controller_id"]].append(t)
        cells[r["cell"]].append(t)
        if t.measurement and t.eval:
            for key, hist in t.measurement.latencies.items():
                delays[key].update(hist)
                if hist:
                    delay_sources[key].append(t.attempt_id)
    groups_json = [
        {
            "group": {"cell": c, "arm": a, "controller": ctrl},
            **group_metrics(v, plan["suite_id"] == "canonical-v1"),
        }
        for (c, a, ctrl), v in sorted(groups.items())
    ]
    cell_json = {
        c: group_metrics(ts, plan["suite_id"] == "canonical-v1")
        for c, ts in cells.items()
    }
    equal = {}
    if len(cells) == 2:
        for key in next(iter(cell_json.values()))["rates"]:
            vals = [v["rates"][key]["value"] for v in cell_json.values()]
            equal[key] = (
                sum(vals) / len(vals) if all(v is not None for v in vals) else None
            )
    delay_keys = (
        "start_error",
        "end_error",
        "observation_delay",
        "command_delay",
        "restore_resume_delay",
        "oracle_detection_delay",
        "path_restore_delay",
        "task_completion",
        "invalid_to_abort",
        "invalid_to_hold",
        "invalid_to_quiescence",
    )
    figures = {"failure_timeline": None}
    failure = next(
        (
            t
            for t in trials
            if t.eval and any(r["verdict"] == "FAIL" for r in t.eval["requirements"])
        ),
        None,
    )
    if failure:
        figures["failure_timeline"] = timeline(failure, out)
    attempts = [
        {
            "run_id": t.row["run_id"],
            "attempt_id": t.attempt_id,
            "outcome": t.outcome,
            "status": t.status,
            "error": t.error,
            "online_offline_equal": t.eval.get("online_offline_equal"),
            "path": str(t.path) if t.eval else None,
            "replay_command": f'cellio evaluate --artifact "{t.path}"'
            if t.eval
            else None,
        }
        for t in trials
    ]
    elapsed_file = source / "soak.result.json"
    elapsed = read(elapsed_file).get("wall_seconds") if elapsed_file.is_file() else None
    reuse = source / "scene_reuse.json"
    measurements = {
        "oracle": oracle_corpus(),
        "mutants": mutant_pairs(trials),
        "overhead": overhead(trials),
        "performance": performance(trials),
        "reset_chains": reset_chains(trials),
        "soak": soak(trials, elapsed)
        if plan["suite_id"] == "soak-v1"
        else {"status": "NOT_RUN", "reason": "different suite"},
        "scene_reuse": read(reuse)
        if reuse.is_file()
        else {
            "status": "NOT_RUN",
            "reason": "extension diff and measured work log unavailable",
        },
        "delays": {
            k: histogram_summary(delays[k], delay_sources[k]) for k in delay_keys
        },
    }
    completed = measurements["delays"]["task_completion"]
    measurements["delays"]["task_completion_seconds"] = {
        **completed,
        "unit": "sim s",
        **{
            k: v / 120 if v is not None else None
            for k, v in completed.items()
            if k in ("numerator", "median", "p95", "p99", "max")
        },
    }
    metrics = {
        "schema_version": "cellio.metrics/1.0",
        "suite_id": plan["suite_id"],
        "protocol_hash": plan["protocol_hash"],
        "plan_hash": plan["plan_hash"],
        "overall": group_metrics(trials),
        "cells": cell_json,
        "equal_weight_cell_mean": equal,
        "groups": groups_json,
        "attempts": attempts,
        "retries": extra,
        **measurements,
        **figures,
    }
    for key, value in measurements.items():
        write_json(out / "tables" / (key + ".json"), value)
    write_json(out / "metrics.json", metrics)
    render_report(metrics, out)
    return {
        "output": str(out),
        "metrics": str(out / "metrics.json"),
        "report_html": str(out / "report.html"),
        "report_markdown": str(out / "report.md"),
        "counts": metrics["overall"]["counts"],
    }


__all__ = ["report"]
