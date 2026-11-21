"""Regenerate publication plots from verified raw evidence and independent finite fixtures."""

import argparse
import csv
import gzip
import json
from math import isfinite
from pathlib import Path

from publication_panels import (
    architecture,
    coverage,
    oracle_mutants,
    stability,
    workcells,
)
from publication_sources import static_tables

from cellio.analysis.campaign import Trial
from cellio.analysis.replay import read_attempt
from cellio.io import file_hash, write_json
from cellio.reporting import report
from cellio.reporting.render import timeline


def development_timeline(path, output):
    config = json.loads((path / "config.resolved.json").read_bytes())
    result = json.loads((path / "result.json").read_bytes())
    assessed = read_attempt(path)
    faults = json.loads((path / "faults.json").read_bytes())
    if (
        result["backend"] != "isaac62"
        or len(faults) != 1
        or faults[0]["actual_start"] is None
        or assessed["observed_results_equal"] is not True
    ):
        raise ValueError(
            "A native realized fault and matching independent replay are required"
        )
    row = {"cell": config["scene"]["cell_id"], "arm": config["scenario_id"]}
    trial = Trial(
        row,
        path=path,
        attempt_id=assessed["attempt_id"],
        eval=assessed,
        fault=faults[0],
    )
    info = timeline(trial, output)
    info["origin"] = "development commissioning"
    info["observed_results_equal"] = assessed["observed_results_equal"]
    info["same_rule_revision"] = assessed["same_rule_revision"]
    info["events"] = [
        (faults[0][k], label)
        for k, label in (
            ("actual_start", "Fault active"),
            ("first_effect_step", "First effect"),
            ("actual_end", "Fault ended"),
        )
        if faults[0].get(k) is not None
    ]
    with gzip.open(path / "events.jsonl.gz", "rt", encoding="utf-8") as stream:
        for event in map(json.loads, stream):
            if event["data"]["event_type"] == "controller.abort":
                info["events"].append((event["step"], "Controller abort"))
    info["compliance"] = assessed["compliance_verdict"]
    info["task"] = assessed["task_outcome"]
    return info


def reproduce(
    campaign, output, companion_campaigns=(), renders=None, timeline_attempt=None
):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    evidence = report(campaign, output)
    data = json.loads(Path(evidence["metrics"]).read_text(encoding="utf-8"))
    static_tables(output)
    suites = {data["suite_id"]: data}
    for path in companion_campaigns:
        plan = json.loads((path / "campaign.plan.json").read_bytes())
        suite = plan["suite_id"]
        if suite in suites or suite not in (
            "oracle-v1",
            "canonical-v1",
            "overhead-v1",
            "reset-v1",
            "soak-v1",
        ):
            raise ValueError(f"Duplicate or inapplicable supplementary suite: {suite}")
        links = report(path, output / "sources" / suite)
        suites[suite] = json.loads(Path(links["metrics"]).read_bytes())
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    plt.rcParams.update(
        {
            "font.size": 10,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    created = []

    def save(fig, name):
        if fig.get_layout_engine() is None:
            fig.tight_layout()
        for extension in ("pdf", "png"):
            dest = figures / f"{name}.{extension}"
            metadata = (
                {"CreationDate": None, "ModDate": None} if extension == "pdf" else {}
            )
            fig.savefig(dest, dpi=190, bbox_inches="tight", metadata=metadata)
            created.append(str(dest))
        plt.close(fig)

    architecture(plt, save)
    canonical = suites.get("canonical-v1", {})
    failure = canonical.get("failure_timeline") or data.get("failure_timeline")
    if timeline_attempt is not None:
        failure = development_timeline(timeline_attempt, output)
    if failure and failure.get("fault_family"):
        timeline_root = (
            output / "sources/canonical-v1"
            if canonical
            and canonical is not data
            and failure is canonical.get("failure_timeline")
            else output
        )
        with (timeline_root / failure["csv"]).open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        fig, axes = plt.subplots(4, 1, figsize=(9, 6), sharex=True)
        steps = [int(r["step"]) for r in rows]

        def number(s):
            if s in ("True", "False"):
                return int(s == "True")
            return float(s) if s else float("nan")

        for key, label in (("raw", "Raw"), ("observed", "Observed")):
            axes[0].step(
                steps,
                [number(r[key]) for r in rows],
                where="post",
                label=label,
                alpha=0.8,
            )
        axes[0].set_ylabel(failure["signal"])
        axes[0].legend()
        joint_values = [number(r["actual_q_m"]) for r in rows]
        axes[1].plot(steps, joint_values)
        finite = [x for x in joint_values if isfinite(x)]
        if finite and max(finite) - min(finite) < 0.0001:
            axes[1].set_ylim(min(finite) - 0.001, max(finite) + 0.001)
        axes[1].ticklabel_format(style="plain", useOffset=False)
        axes[1].set_ylabel("Joint (m)")
        for axis in "xyz":
            axes[2].plot(
                steps, [number(r["part_" + axis + "_m"]) for r in rows], label=axis
            )
        axes[2].set_ylabel("Part (m)")
        axes[2].legend(ncols=3)
        events = tuple(
            (key, label)
            for key, label in (
                ("issued", "Issue"),
                ("applied", "Apply"),
                ("fail_witnesses", "Requirement FAIL"),
            )
        )
        for i, (key, label) in enumerate(events):
            selected = [int(r["step"]) for r in rows if r[key]]
            axes[3].scatter(selected, [i] * len(selected), marker="|")
        axes[3].set_yticks(range(3), [s[1] for s in events])
        axes[3].set_xlabel("Physics step")
        if failure.get("events"):
            for step, label in failure["events"]:
                axes[0].axvline(step, lw=0.8, ls="--", alpha=0.6)
            notes = "   ·   ".join(
                f"{label} {step}" for step, label in sorted(failure["events"])
            )
            fig.suptitle(
                f"{failure['origin'].title()}  |  {failure['fault_family']}  |  {failure['task']}  |  compliance {failure['compliance']}\n{notes}",
                fontsize=10,
            )
        for ax in axes:
            window = None
            for r in rows:
                transitions = r["fault_events"].split()
                if "ACTIVE" in transitions:
                    window = int(r["step"])
                if window is not None and "ENDED" in transitions:
                    ax.axvspan(window, int(r["step"]), color="#d9a961", alpha=0.2)
                    window = None
            if window is not None:
                ax.axvspan(window, steps[-1], color="#d9a961", alpha=0.2)
        save(fig, "figure3_failure_timeline")
    canonical = suites.get("canonical-v1", {})
    panels = {
        "figure1_architecture": "rendered from ports and runtime records",
        "figure2_native_workcells": "rendered"
        if workcells(plt, save, renders)
        else "pending native Cell A/B fixed-camera renders",
        "figure3_failure_timeline": "rendered"
        if failure and failure.get("fault_family")
        else "pending verified fault timeline",
        "figure4_realization_task_compliance": "rendered"
        if coverage(plt, save, canonical)
        else "pending canonical first attempts",
        "figure5_oracle_mutant_validation": "rendered"
        if oracle_mutants(plt, save, data["oracle"], canonical)
        else "pending verified fixtures",
        "figure6_overhead_reset_soak": "rendered"
        if stability(plt, save, suites)
        else "pending overhead, reset or soak measurements",
    }
    published = {
        "panels": panels,
        "measurement_coverage": {
            "oracle_gold_cases": sum(
                sum(row.values())
                for row in data["oracle"].get("matrix_4x4", {}).values()
            ),
            "mutant_reference_pairs_with_both_attempt_ids": sum(
                bool(p["candidate"] and p["reference"])
                for rows in canonical.get("mutants", {}).get("models", {}).values()
                for p in rows
            ),
            "canonical_planned": canonical.get("overall", {})
            .get("counts", {})
            .get("N_plan"),
            "canonical_started": canonical.get("overall", {})
            .get("counts", {})
            .get("N_start"),
            "overhead_pairs_with_measured_time": sum(
                p["overhead_percent"] is not None
                for d in suites.get("overhead-v1", {}).get("overhead", {}).values()
                for p in d["pairs"]
            ),
            "reset_chains_with_trace_evidence": sum(
                bool(r["source_artifact_ids"])
                for r in suites.get("reset-v1", {})
                .get("reset_chains", {})
                .get("chains", [])
            ),
            "soak_verified_duration_s": suites.get("soak-v1", {})
            .get("soak", {})
            .get("elapsed_wall_seconds")
            if suites.get("soak-v1", {}).get("soak", {}).get("measurement_end_ns")
            else None,
        },
        "timeline_source": {
            k: failure[k]
            for k in (
                "attempt_id",
                "trace_chain_sha256",
                "fault_family",
                "origin",
                "task",
                "compliance",
                "events",
            )
            if k in failure
        }
        if failure and failure.get("fault_family")
        else None,
        "inputs": {
            suite: {"plan_hash": d["plan_hash"], "protocol_hash": d["protocol_hash"]}
            for suite, d in suites.items()
        },
        "figure_files": {Path(path).name: file_hash(Path(path)) for path in created},
    }
    write_json(figures / "index.json", published)
    return {"metrics": evidence["metrics"], "generated": created, **published}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--timeline-attempt",
        type=Path,
        help="Verified native development fault attempt for a separately labelled Figure 3",
    )
    parser.add_argument(
        "--companion-campaign",
        type=Path,
        action="append",
        default=[],
        help="Additional raw suite to combine in the same panels; repeat as needed",
    )
    parser.add_argument(
        "--renders",
        type=Path,
        help="Directory containing reviewed native fixed-camera PNGs and fixed-camera.json",
    )
    opts = parser.parse_args()
    print(
        json.dumps(
            reproduce(
                opts.campaign,
                opts.output,
                opts.companion_campaign,
                opts.renders,
                opts.timeline_attempt,
            ),
            indent=2,
        )
    )
