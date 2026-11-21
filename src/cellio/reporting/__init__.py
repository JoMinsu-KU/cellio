"""Generate a report from the frozen run plan and independently replayed artifacts."""

import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path

from cellio.analysis.campaign import group_metrics, load_campaign
from cellio.analysis.comparisons import confusion, mutant_pairs, overhead
from cellio.analysis.measurements import histogram_summary
from cellio.analysis.replay import read
from cellio.analysis.seed_sensitivity import canonical_seed_sensitivity
from cellio.analysis.suites import performance, reset_chains, soak_evidence
from cellio.environment import source_hash
from cellio.io import digest, file_hash, write_json

from .render import render_report, table, timeline


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


def oracle_report(plan, source, out):
    from cellio.acceptance import evaluate_gold, gold_directory
    from cellio.io import atomic_write, digest

    corpus = read(gold_directory() / "gold.json")
    expected = [(case["id"], digest(case)) for case in corpus]
    planned = [(row["case_id"], row["case_hash"]) for row in plan["runs"]]
    if planned != expected:
        raise ValueError("Oracle plan case IDs/hashes differ from the frozen corpus")
    evidence = evaluate_gold()
    stored = source / "oracle.results.json"
    recorded_equal = read(stored) == evidence if stored.is_file() else None
    status = (
        "NOT_RUN"
        if recorded_equal is None
        else "PASS"
        if recorded_equal and evidence["passed"]
        else "FAIL"
    )
    counts = {
        "N_plan": len(planned),
        "N_start": len(planned) if recorded_equal is not None else 0,
        "N_verified_cases": len(planned) if recorded_equal else 0,
    }
    data = {
        "schema_version": "cellio.metrics/1.0",
        "suite_id": plan["suite_id"],
        "plan_hash": plan["plan_hash"],
        "protocol_hash": plan["protocol_hash"],
        "campaign_status": status,
        "counts": counts,
        "recorded_matches_replay": recorded_equal,
        "recorded_results_sha256": file_hash(stored) if stored.is_file() else None,
        "oracle": oracle_corpus(),
        "independent_replay": evidence,
    }
    (out / "tables").mkdir(parents=True, exist_ok=True)
    write_json(out / "metrics.json", data)
    write_json(out / "tables/oracle.json", data["oracle"])
    rows = [
        {
            "case_id": c["id"],
            "matches_gold": c["passed"],
            **{
                prefix + "_" + key: value
                for prefix in ("expected", "actual")
                for key, value in c[prefix].items()
            },
        }
        for c in evidence["cases"]
    ]
    table(out / "tables/oracle_witnesses.csv", rows, list(rows[0]))
    labels = data["oracle"]["matrix_4x4"]
    table(
        out / "tables/oracle_confusion.csv",
        [{"expected": key, **vals} for key, vals in labels.items()],
        ["expected", *labels.keys()],
    )
    headline = (
        f"Campaign {status}. Independent replay evaluated {len(rows)} frozen cases."
    )
    links = [
        ("Confusion matrix", "tables/oracle_confusion.csv"),
        ("Case witnesses", "tables/oracle_witnesses.csv"),
        ("Replay decisions", "metrics.json"),
    ]
    md = (
        "# Oracle fixture campaign\n\n"
        + headline
        + "\n\n"
        + "\n".join(f"- [{k}]({v})" for k, v in links)
        + "\n"
    )
    html = (
        '<!doctype html><meta charset="utf-8"><title>CellIO oracle fixtures</title><h1>Oracle fixture campaign</h1><p>'
        + headline
        + "</p><ul>"
        + "".join(f'<li><a href="{v}">{k}</a></li>' for k, v in links)
        + "</ul>"
    )
    atomic_write(out / "report.md", md.encode())
    atomic_write(out / "report.html", html.encode())
    return {
        "output": str(out),
        "metrics": str(out / "metrics.json"),
        "report_html": str(out / "report.html"),
        "report_markdown": str(out / "report.md"),
        "counts": counts,
        "status": status,
    }


def attempt_replay(t):
    return {
        "run_id": t.row["run_id"],
        "attempt_id": t.attempt_id,
        "outcome": t.outcome,
        "status": t.status,
        "error": t.error,
        "online_offline_equal": t.eval.get("online_offline_equal"),
        "same_rule_revision": t.eval.get("same_rule_revision"),
        "observed_results_equal": t.eval.get("observed_results_equal"),
        "source_digest": t.eval.get("source_digest"),
        "rules_digest": t.eval.get("rules_digest"),
        "end_step": t.eval.get("end_step"),
        "witness_digest": digest(
            {
                r["requirement_id"]: [r["verdict"], r.get("first_witness_step")]
                for r in t.eval["requirements"]
            }
        )
        if t.eval.get("requirements")
        else None,
    }


def report(campaign_dir, output_dir):
    source, out = Path(campaign_dir).resolve(), Path(output_dir).resolve()
    plan, trials, extra = load_campaign(source)
    if plan["suite_id"] == "oracle-v1":
        return oracle_report(plan, source, out)
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
        "deadline_to_abort",
        "deadline_to_hold",
        "deadline_to_quiescence",
    )
    figures = {"failure_timeline": None}
    candidates = [t for t in trials if t.eval]

    def timeline_priority(t):
        realized = t.fault.get("actual_start") is not None
        failed = any(r["verdict"] == "FAIL" for r in t.eval["requirements"])
        return (realized, failed)

    failure = max(
        (t for t in candidates if any(timeline_priority(t))),
        key=timeline_priority,
        default=None,
    )
    if failure:
        figures["failure_timeline"] = timeline(failure, out)
    attempts = [
        {
            **attempt_replay(t),
            "path": str(t.path) if t.eval else None,
            "replay_command": f'cellio evaluate --artifact "{t.path}"'
            if t.eval
            else None,
        }
        for t in trials
    ]
    reuse_results = {
        "status": "NOT_RUN",
        "reason": "no recorded Cell A to Cell B regression audit",
    }
    evidence_root = next(
        (
            p
            for p in (source, source.parent)
            if (p / "release-evidence/reuse/scene_reuse.json").is_file()
        ),
        None,
    )
    if evidence_root is not None:
        from cellio.acceptance import checkout
        from cellio.release import audit_reuse

        # Recompute the exact committed-tree diff and isolated regressions.
        try:
            reuse_results = audit_reuse(checkout(), out)
        except FileNotFoundError as exc:
            reuse_results = {"status": "NOT_RUN", "reason": str(exc)}
    measurements = {
        "oracle": oracle_corpus(),
        "mutants": mutant_pairs(trials),
        "overhead": overhead(trials),
        "performance": performance(trials),
        "reset_chains": reset_chains(trials),
        "soak": soak_evidence(source, trials)
        if plan["suite_id"] == "soak-v1"
        else {"status": "NOT_RUN", "reason": "different suite"},
        "scene_reuse": reuse_results,
        "delays": {
            k: histogram_summary(delays[k], delay_sources[k]) for k in delay_keys
        },
    }
    completed = measurements["delays"]["task_completion"]
    if plan["suite_id"] == "canonical-v1":
        measurements["seed_sensitivity"] = canonical_seed_sensitivity(plan, trials)
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
        "replay_environment": {
            "os": platform.system(),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "source_hash": source_hash(),
        },
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
