"""Prepare or compare a read-only, path-free review of a sealed canonical campaign."""

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

from compare_platform_replays import compare as compare_platforms

from cellio.analysis.campaign import (
    Trial,
    attempt_histories,
    lines,
    safe_child,
    verify_plan_inputs,
)
from cellio.analysis.comparisons import mutant_pairs
from cellio.analysis.replay import read, rule_list
from cellio.analysis.statistics import quantile
from cellio.environment import source_hash
from cellio.io import atomic_write, canonical, digest, file_hash
from cellio.runtime.campaign import finite_rows, read_plan, suite_design

CRLF_INDEXES = {"first-attempt-index.json", "matched-mutant-pairs.json"}


def require(ok, context):
    if not ok:
        raise ValueError(context)


def save(path, data):
    """Keep the two previously indexed SHA-256 values identical on Windows and POSIX."""
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path.name in CRLF_INDEXES:
        text = text.replace("\n", "\r\n")
    atomic_write(path, text.encode("utf-8"))


def extract(folder, journal, qualification):
    plan = read_plan(folder / "campaign.plan.json")
    design = suite_design("canonical-v1")
    require(
        plan["suite_id"] == "canonical-v1"
        and plan["provenance"]["code_hash"]
        == source_hash()
        == qualification["package_source_sha256"]
        and plan["suite_design"] == design
        and plan["runs"] == finite_rows("canonical-v1", plan["provenance"], design),
        "Frozen candidate or design differs",
    )
    official = [
        row
        for row in lines(journal)
        if row.get("suite") == "canonical-v1" and row["event"] == "first_attempt"
    ]
    by_id = {row["run_id"]: row for row in official}
    require(
        len(official) == len(by_id) == len(plan["runs"]) == 820,
        "Missing/duplicate first-attempt replay entry",
    )
    histories = attempt_histories(lines(folder / "ledger.jsonl"), set(by_id))
    records = []
    for row in plan["runs"]:
        run_id = row["run_id"]
        history = histories[run_id]
        journal_event = by_id[run_id]
        records.append(
            read_sealed_trial(folder, row, history, journal_event, plan["provenance"])
        )
    return (
        plan,
        records,
        {
            "plan_file_sha256": file_hash(folder / "campaign.plan.json"),
            "ledger_file_sha256": file_hash(folder / "ledger.jsonl"),
            "first_attempt_journal_digest": digest(official),
            "journal_id": qualification["coordinator_journal_id"],
        },
        official,
    )


def read_sealed_trial(folder, row, history, journal_event, provenance):
    """Join a planned row to its sole ledger attempt, coordinator event, and verified snapshot files."""
    run_id = row["run_id"]
    require(
        len(history) == 1
        and history[0][0]["attempt_index"] == 0
        and history[0][0]["parent_attempt_id"] is None
        and history[0][-1]["state"] == "COMPLETED"
        and journal_event["status"] == "COMPLETED"
        and not journal_event["error"],
        "Ineligible first attempt " + run_id,
    )
    attempt_id = history[0][0]["attempt_id"]
    directory = safe_child(folder, run_id, attempt_id)
    require(
        attempt_id == journal_event["attempt_id"], "Journal/ledger mismatch " + run_id
    )
    verify_plan_inputs(directory, row, provenance)
    manifest = read(directory / "manifest.final.json")
    manifest_hash = file_hash(directory / "manifest.final.json")
    require(
        (directory / "COMPLETE").read_bytes() == (manifest_hash + "\n").encode("ascii")
        and manifest["run_id"] == run_id
        and manifest["attempt_id"] == attempt_id,
        "Unsealed or wrong attempt " + run_id,
    )
    for name in ("requirements.json", "faults.json", "result.json"):
        require(
            file_hash(directory / name) == manifest["files"][name]["sha256"],
            "Source changed " + name,
        )
    result = read(directory / "result.json")
    requirements = read(directory / "requirements.json")
    faults = read(directory / "faults.json")
    require(
        result["requirements"] == requirements
        and result["faults"] == faults
        and journal_event["outcome"] == result["task_outcome"]
        and result["artifact_digest"] == manifest["last_record_hash"],
        "Online evidence mismatch " + run_id,
    )
    require(
        result["backend"] == "isaac62"
        and result["end_step"]
        == result["last_valid_snapshot"]
        == result["last_successful_advance"]
        == 7200
        and result["completion_status"] == "COMPLETED"
        and not result["errors"],
        "Incomplete native episode " + run_id,
    )
    require(len(faults) == int(row["arm"] != "healthy"), "Unexpected number of faults")
    fault = faults[0] if faults else {}
    witnesses = {
        verdict["requirement_id"]: [verdict["verdict"], verdict["first_witness_step"]]
        for verdict in requirements
    }
    r06 = next(
        verdict for verdict in requirements if ".r06." in verdict["requirement_id"]
    )
    aborts = []
    if row["controller_id"] == "reference" and r06["verdict"] == "FAIL":
        require(
            file_hash(directory / "events.jsonl.gz")
            == manifest["files"]["events.jsonl.gz"]["sha256"],
            "Changed abort evidence",
        )
        with gzip.open(directory / "events.jsonl.gz", "rb") as stream:
            for event in map(json.loads, stream):
                if event["data"]["event_type"] == "controller.abort":
                    aborts.append(event["step"])
    primary = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "cell": row["cell"],
        "controller": row["controller_id"],
        "arm": row["arm"],
        "family": fault.get("family"),
        "scene_seed": row["initial_state_id"],
        "repeat": row["repeat_index"],
        "initial_state_sha256": digest(row["initial_state"]),
        "config_sha256": row["config_hash"],
        "task": result["task_outcome"],
        "compliance": result["compliance_verdict"],
        "end_step": result["end_step"],
        "rules_sha256": digest(rule_list(row["config"], None)),
        "witness_sha256": digest(witnesses),
        "manifest_sha256": manifest_hash,
        "requirements_sha256": manifest["files"]["requirements.json"]["sha256"],
        "trace_gzip_sha256": manifest["files"]["trace.jsonl.gz"]["sha256"],
        "trace_chain_sha256": manifest["last_record_hash"],
        "realized": fault.get("first_effect_step") is not None,
        "restoration_status": fault.get("restoration_status"),
        "physical_hold": fault.get("physical_hold_verdict"),
        "r06_verdict": r06["verdict"],
        "r06_step": r06["first_witness_step"],
        "abort_steps_if_reference_r06_fail": aborts,
    }
    # The official Windows coordinator emitted the journal entry only after reading its
    # first attempt with read_trial. It refuses to advance on missing or different replay.
    trial = Trial(
        row,
        started=True,
        status="COMPLETED",
        attempt_id=attempt_id,
        eval={
            "task_outcome": primary["task"],
            "compliance_verdict": primary["compliance"],
            "requirements": requirements,
            "online_offline_equal": True,
        },
        fault=fault,
    )
    return trial, primary


def counts(items):
    outcomes = Counter(entry["task"] for _, entry in items)
    compliance = Counter(entry["compliance"] for _, entry in items)
    faults = [entry for _, entry in items if entry["family"]]
    joint = Counter((entry["task"], entry["compliance"]) for _, entry in items)
    return {
        "planned_first_attempts": len(items),
        "evaluable": sum(trial.evaluable for trial, _ in items),
        "distinct_scene_seeds": len({entry["scene_seed"] for _, entry in items}),
        "distinct_cell_states": len(
            {(entry["cell"], entry["initial_state_sha256"]) for _, entry in items}
        ),
        "task_outcomes": dict(sorted(outcomes.items())),
        "compliance": dict(sorted(compliance.items())),
        "task_and_compliance": [
            {"task": task, "compliance": compliance_value, "count": count}
            for (task, compliance_value), count in sorted(joint.items())
        ],
        "fault_planned": len(faults),
        "fault_realized": sum(entry["realized"] for entry in faults),
        "logged_path_restored": sum(
            entry["restoration_status"] == "PATH_RESTORED" for entry in faults
        ),
        "realized_and_logged_path_restored": sum(
            entry["realized"] and entry["restoration_status"] == "PATH_RESTORED"
            for entry in faults
        ),
    }


def grouped(items, keys):
    groups = defaultdict(list)
    for t, entry in items:
        groups[tuple(entry.get(k) for k in keys)].append((t, entry))
    return [
        {**dict(zip(keys, g, strict=True)), **counts(rows)}
        for g, rows in sorted(
            groups.items(), key=lambda u: tuple(x or "" for x in u[0])
        )
    ]


def dist(xs):
    return {
        "n": len(xs),
        "minimum": min(xs, default=None),
        "median": quantile(xs, 0.5),
        "p95": quantile(xs, 0.95),
        "maximum": max(xs, default=None),
        "histogram": [[k, n] for k, n in sorted(Counter(xs).items())],
    }


def r06_analysis(items):
    reference = [entry for _, entry in items if entry["controller"] == "reference"]
    violations = [entry for entry in reference if entry["r06_verdict"] == "FAIL"]
    rows = []
    for entry in violations:
        candidates = [
            a
            for a in entry["abort_steps_if_reference_r06_fail"]
            if a <= entry["r06_step"]
        ]
        rows.append(
            {
                "run_id": entry["run_id"],
                "attempt_id": entry["attempt_id"],
                "cell": entry["cell"],
                "arm": entry["arm"],
                "state": entry["scene_seed"],
                "repeat": entry["repeat"],
                "first_violation_step": entry["r06_step"],
                "abort_steps": candidates,
                "elapsed_steps": entry["r06_step"] - candidates[0]
                if len(candidates) == 1
                else None,
            }
        )
    return {
        "reference_total": len(reference),
        "r06_verdicts": dict(Counter(entry["r06_verdict"] for entry in reference)),
        "failures": len(rows),
        "by_cell_arm": [
            {
                "cell": c,
                "arm": a,
                "reference_total": sum(
                    entry["cell"] == c and entry["arm"] == a for entry in reference
                ),
                "n_fail": len(v),
                "witness_steps": dist([entry["first_violation_step"] for entry in v]),
                "abort_to_witness_steps": dist(
                    [
                        entry["elapsed_steps"]
                        for entry in v
                        if entry["elapsed_steps"] is not None
                    ]
                ),
            }
            for (c, a), v in sorted(_group_failure(rows).items())
        ],
        "absolute_first_witness_steps": dist([x["first_violation_step"] for x in rows]),
        "abort_to_witness_steps": dist(
            [x["elapsed_steps"] for x in rows if x["elapsed_steps"] is not None]
        ),
        "excluded_from_abort_to_witness": sum(
            entry["elapsed_steps"] is None for entry in rows
        ),
        "rows": rows,
    }


def _group_failure(rows):
    output = defaultdict(list)
    for item in rows:
        output[item["cell"], item["arm"]].append(item)
    return output


def pair_summary(items):
    analysis = mutant_pairs([trial for trial, _ in items])
    by_run = {entry["run_id"]: entry for _, entry in items}
    by_attempt = {entry["attempt_id"]: entry for _, entry in items}
    models = {}
    pair_rows = []
    for controller, pairs in analysis["models"].items():
        stats = Counter(pair["status"] for pair in pairs)
        models[controller] = {
            "planned_pairs": len(pairs),
            "evaluable_and_both_realized": stats["KILLED"]
            + stats["MUTATION_UNINFORMATIVE"],
            "status_counts": dict(stats),
            "cells": {
                cell: dict(
                    Counter(pair["status"] for pair in pairs if pair["cell"] == cell)
                )
                for cell in ("cell_a", "cell_b")
            },
            "nominal_diagnostics": sum(
                diagnostic["mutant"] == controller
                for diagnostic in analysis["nominal_diagnostics"]
            ),
        }
        for pair in pairs:
            event = by_run[pair["run_id"]]
            reference = by_attempt.get(pair["reference"])
            pair_rows.append(
                {
                    **pair,
                    "mutant": controller,
                    "arm": event["arm"],
                    "repeat": event["repeat"],
                    "reference_effect_realized": reference["realized"]
                    if reference
                    else None,
                    "mutant_effect_realized": event["realized"],
                }
            )
    distinct = analysis["distinct_kill_yield"]
    return {
        "distinct_models_detected": distinct["numerator"],
        "registered_distinct_models": distinct["denominator"],
        "planned_fault_pairs": len(pair_rows),
        "pair_status_counts": dict(Counter(pair["status"] for pair in pair_rows)),
        "healthy_mutant_diagnostics_excluded_from_target_detection": len(
            analysis["nominal_diagnostics"]
        ),
        "by_model": models,
    }, pair_rows


def cohort(records):
    mutant, _ = pair_summary(records)
    return {
        "overall": counts(records),
        "by_cell": grouped(records, ["cell"]),
        "by_controller": grouped(records, ["controller"]),
        "by_cell_controller_arm": grouped(records, ["cell", "controller", "arm"]),
        "reference_by_cell_family": grouped(
            [
                (trial, entry)
                for trial, entry in records
                if entry["controller"] == "reference"
            ],
            ["cell", "family"],
        ),
        "allocation": {
            "reference": sum(
                entry["controller"] == "reference" for _, entry in records
            ),
            "target_mutant": mutant["planned_fault_pairs"],
            "healthy_mutant": mutant[
                "healthy_mutant_diagnostics_excluded_from_target_detection"
            ],
        },
        "reference_r06": r06_analysis(records),
        "mutation": mutant,
    }


def emit_markdown(summary, output):
    co = summary["cohorts"]
    text = [
        "# Canonical first-attempt analysis",
        "",
        "The primary analysis uses all registered first attempts. The secondary development-exposure disclosure compares two disjoint subsets of those same attempts. It was documented during development. Scene conditions repeat within a seed, and mutant coverage ends at seed 1004.",
        "",
        "| Cohort | First attempts | Distinct cell–states | Reference / targeted mutant / healthy mutant | Task success / controlled abort / failed | Compliance pass / fail |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, g in co.items():
        n = g["overall"]
        a = g["allocation"]
        t = n["task_outcomes"]
        c = n["compliance"]
        text.append(
            f"| {label} | {n['planned_first_attempts']} | {n['distinct_cell_states']} | {a['reference']} / {a['target_mutant']} / {a['healthy_mutant']} | {t.get('SUCCESS', 0)} / {t.get('CONTROLLED_ABORT', 0)} / {t.get('FAILED', 0)} | {c.get('PASS', 0)} / {c.get('FAIL', 0)} |"
        )
    main = co["All 820"]
    mu = main["mutation"]
    r6 = main["reference_r06"]
    text += [
        "",
        "## Reference-controller outcomes",
        "",
        "Each cell and arm contributes 40 attempts from the same 20 scene seeds with two repeats per seed.",
        "",
        "| Cell | Arm | Success | Controlled abort | Failed | Compliance pass | Compliance fail |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in main["by_cell_controller_arm"]:
        if r["controller"] == "reference":
            t, c = r["task_outcomes"], r["compliance"]
            text.append(
                f"| {r['cell']} | {r['arm']} | {t.get('SUCCESS', 0)} | {t.get('CONTROLLED_ABORT', 0)} | {t.get('FAILED', 0)} | {c.get('PASS', 0)} | {c.get('FAIL', 0)} |"
            )
    text += [
        "",
        "## Matched mutant detection",
        "",
        f"{mu['distinct_models_detected']} of {mu['registered_distinct_models']} mutant types produced a targeted failure against a passing or inapplicable rule in its paired reference. Each pair used the same cell, physical state, fault arm and repeat. The 60 healthy mutant runs are diagnostic. The 120 faulted mutant episodes reuse matching reference episodes from the 640 reference rows.",
        "",
        "| Model | Target pairs | Killed | Exercised without targeted failure | Missing exercise | Infra / ineligible |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for ctrl, d in mu["by_model"].items():
        s = d["status_counts"]
        text.append(
            f"| {ctrl} | {d['planned_pairs']} | {s.get('KILLED', 0)} | {s.get('MUTATION_UNINFORMATIVE', 0)} | {s.get('NOT_EXERCISED', 0)} | {s.get('INFRA_ERROR', 0) + s.get('INELIGIBLE', 0)} |"
        )
    text += [
        "",
        "## Reference R06 abort-stop failures",
        "",
        "| Cell | Fault arm | R06 failures / reference attempts | First violation step, min–max | Abort-to-first-violation steps, min–max |",
        "|---|---|---:|---:|---:|",
    ]
    for g in r6["by_cell_arm"]:
        w, d = g["witness_steps"], g["abort_to_witness_steps"]
        text.append(
            f"| {g['cell']} | {g['arm']} | {g['n_fail']} / {g['reference_total']} | {w['minimum']}–{w['maximum']} | {d['minimum']}–{d['maximum']} |"
        )
    text += [
        "",
        f"R06 failed in {r6['failures']} of {r6['reference_total']} reference attempts. The interval measures the time from the recorded abort declaration to the requirement's first failed deadline. The separate physical-settling analysis reconstructs the first qualifying dwell from the logged joint and belt velocities.",
        "",
        "The accompanying JSON records each cell, controller and arm, including full task and compliance counts, the denominator for each group and R06 timing histograms. Source rows and matched pairs are indexed by attempt ID. The source index binds each included run to its sealed manifest and trace-chain digest.",
        "",
    ]
    text += [
        "[Measured physical settling following R06 failures](r06-physical-diagnosis.md) · [Windows and WSL report checks](report-comparison.json)",
        "",
    ]
    atomic_write(output / "primary-review.md", "\n".join(text).encode("utf-8"))


def report_check(metrics, report, summary, rows, pairs):
    """Cross-check the extractor against the report, including all mutant rule witnesses."""
    require(
        report["suite_id"] == "canonical-v1"
        and report["plan_hash"] == summary["plan_hash"]
        and report["protocol_hash"] == summary["protocol_hash"]
        and report["replay_environment"]["source_hash"] == summary["source_hash"],
        "Report provenance differs",
    )
    ids = [i["run_id"] for i in rows]
    require(
        ids == [a["run_id"] for a in report["attempts"]],
        "Report row identity/order differs",
    )
    for raw, row in zip(rows, report["attempts"], strict=True):
        expect = {
            "attempt_id": raw["attempt_id"],
            "source_digest": raw["trace_chain_sha256"],
            "rules_digest": raw["rules_sha256"],
            "witness_digest": raw["witness_sha256"],
            "outcome": raw["task"],
            "status": "COMPLETED",
            "end_step": 7200,
            "error": None,
            "online_offline_equal": True,
        }
        require(
            all(row[k] == v for k, v in expect.items()),
            "Report first attempt mismatch " + raw["run_id"],
        )
    official = [
        {"mutant": model, **pair}
        for model, pairs in report["mutants"]["models"].items()
        for pair in pairs
    ]
    require(
        official
        == [
            {
                k: v
                for k, v in pair.items()
                if k
                not in (
                    "arm",
                    "repeat",
                    "reference_effect_realized",
                    "mutant_effect_realized",
                )
            }
            for pair in pairs
        ],
        "Mutant pair statuses or witnesses differ",
    )
    totals = summary["cohorts"]["All 820"]["overall"]
    require(
        {
            k: v["numerator"]
            for k, v in report["overall"]["task_distribution"].items()
            if v["numerator"]
        }
        == totals["task_outcomes"],
        "Full outcome distribution differs",
    )
    require(
        {
            k: v["numerator"]
            for k, v in report["overall"]["compliance_distribution"].items()
            if v["numerator"]
        }
        == totals["compliance"],
        "Full compliance distribution differs",
    )
    conditions = {
        (row["cell"], row["controller"], row["arm"]): row
        for row in summary["cohorts"]["All 820"]["by_cell_controller_arm"]
    }
    for condition in report["groups"]:
        identifiers = condition["group"]
        expected = conditions.pop(
            (identifiers["cell"], identifiers["controller"], identifiers["arm"])
        )
        for key, metric in (
            ("task_outcomes", "task_distribution"),
            ("compliance", "compliance_distribution"),
        ):
            require(
                expected[key]
                == {
                    k: v["numerator"]
                    for k, v in condition[metric].items()
                    if v["numerator"]
                },
                "Condition mismatch for " + str(identifiers),
            )
    require(not conditions, "Missing reported conditions")
    for own, key in (("Seed 1000", "1000"), ("Seeds 1001–1019", "1001–1019")):
        left = summary["cohorts"][own]["overall"]
        right = report["seed_sensitivity"]["cohorts"][key]
        require(
            left["planned_first_attempts"] == right["counts"]["N_plan"]
            and left["evaluable"] == right["counts"]["N_eval"]
            and left["task_outcomes"] == right["task_outcomes_per_plan"],
            "Cohort mismatch " + key,
        )
    return {
        "platform": report["replay_environment"]["os"],
        "kernel": report["replay_environment"]["kernel"],
        "report_file_sha256": file_hash(metrics),
        "verified_first_attempts": len(rows),
        "matched_pair_decisions_and_target_witnesses": len(pairs),
        "status": "MATCH",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign", type=Path, required=True, help="Sealed canonical-v1 directory"
    )
    parser.add_argument(
        "--qualification",
        type=Path,
        required=True,
        help="Candidate qualification summary.json",
    )
    parser.add_argument(
        "--journal",
        type=Path,
        required=True,
        help="Coordinator journal.jsonl or its published first-attempt subset",
    )
    parser.add_argument(
        "--windows", type=Path, required=True, help="Windows metrics.json"
    )
    parser.add_argument("--wsl", type=Path, required=True, help="WSL metrics.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder, output = args.campaign.resolve(), args.output.resolve()
    require(
        not output.is_relative_to(folder),
        "Choose an output directory outside the sealed campaign",
    )
    qualification = read(args.qualification)
    plan, records, proof, journal_events = extract(folder, args.journal, qualification)
    reports = {"Windows": read(args.windows), "WSL": read(args.wsl)}
    comparison = compare_platforms(folder, reports["Windows"], reports["WSL"])
    require(
        comparison["status"] == "PASS"
        and comparison["matching_verified_first_attempts"] == len(records),
        "Canonical platform comparison did not verify every planned attempt",
    )

    # Preserve the original archive index and matching-pair index, including their ordering.
    rows = [entry for _, entry in records]
    _, pairs = pair_summary(records)
    subsets = {
        "All 820": records,
        "Seed 1000": [pair for pair in records if pair[1]["scene_seed"] == 1000],
        "Seeds 1001–1019": [pair for pair in records if pair[1]["scene_seed"] > 1000],
    }
    require(
        [len(subset) for subset in subsets.values()] == [820, 68, 752],
        "Registered population or subgroup differs",
    )
    summary = {
        "schema": "cellio.canonical-primary-review/1.0",
        "scope": "Primary all 820 registered first attempts. Secondary development-exposure sensitivity partitions the same data. Seed 1000 had been observed during development. The other 19 seeds were previously unseen. The development disclosure specified this partition separately from the protocol freeze.",
        "candidate_commit": qualification["candidate_commit"],
        "source_hash": plan["provenance"]["code_hash"],
        "plan_hash": plan["plan_hash"],
        "protocol_hash": plan["protocol_hash"],
        "provenance": plan["provenance"],
        "input_proof": proof,
        "qualification_file_sha256": file_hash(args.qualification),
        "extractor_file_sha256": file_hash(Path(__file__)),
        "platform_comparator_sha256": file_hash(
            Path(__file__).with_name("compare_platform_replays.py")
        ),
        "audit_method": "Reads sealed online results, requirement witnesses, fault summaries, input locks, ledger and coordinator records. The archive index retains the manifest digests of all 820 compressed traces. Both full platform replays are required to agree with the first-attempt index and all matched mutant decisions.",
        "denominators": "Task success uses every planned first attempt. Compliance is reported separately. Conditions share initial states within each cell and seed. Healthy mutant diagnostics are excluded from matched detection. Ineligible, unexercised and uninformative fault pairs stay in the registered denominator.",
        "cohorts": {name: cohort(subset) for name, subset in subsets.items()},
    }
    metrics_paths = {"Windows": args.windows, "WSL": args.wsl}
    decisions = {
        name: report_check(metrics_paths[name], report, summary, rows, pairs)
        for name, report in reports.items()
    }
    event_fields = {
        "attempt_id",
        "error",
        "event",
        "outcome",
        "run_id",
        "status",
        "suite",
        "unix_ns",
    }
    require(
        all(set(event) == event_fields for event in journal_events),
        "Unexpected coordinator field",
    )
    journal_bytes = b"".join(canonical(event) + b"\n" for event in journal_events)
    output.mkdir(parents=True, exist_ok=True)
    atomic_write(output / "coordinator-first-attempts.jsonl", journal_bytes)
    save(output / "first-attempt-index.json", rows)
    save(output / "matched-mutant-pairs.json", pairs)
    summary.update(
        source_index_file_sha256=file_hash(output / "first-attempt-index.json"),
        paired_index_file_sha256=file_hash(output / "matched-mutant-pairs.json"),
        coordinator_index_file_sha256=file_hash(
            output / "coordinator-first-attempts.jsonl"
        ),
    )
    save(output / "primary-review.json", summary)
    emit_markdown(summary, output)
    save(
        output / "report-comparison.json",
        {
            "summary_sha256": file_hash(output / "primary-review.json"),
            "reports": decisions,
        },
    )
    print(
        f"{len(rows)} verified first attempts and {len(pairs)} matched pairs on both platforms."
    )


if __name__ == "__main__":
    main()
