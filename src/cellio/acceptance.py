"""Evaluate release criteria against tests and verified campaign artifacts."""

from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import xml.etree.ElementTree as ET

from cellio.io import digest, file_hash, write_json


def checkout():
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "fixtures/oracle/gold.json").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        "Run acceptance from the source checkout with its frozen fixtures"
    )


def gold_directory():
    from_source = Path(__file__).resolve().parents[2] / "fixtures/oracle"
    if (from_source / "gold.json").is_file():
        return from_source
    return Path(sysconfig.get_path("data")) / "share/cellio/fixtures/oracle"


def evaluate_gold():
    from cellio.core.oracle.fixtures import evaluate_fixture

    folder = gold_directory()
    source = folder / "gold.json"
    checks = []
    cases = json.loads(source.read_text(encoding="utf-8"))
    for case in cases:
        actual = evaluate_fixture(case)
        observed = {
            k: getattr(actual, k)
            for k in ("verdict", "first_witness_step", "obligation_count", "reason")
        }
        observed["coverage_missed"] = actual.verdict == "NOT_APPLICABLE"
        checks.append(
            {
                "id": case["id"],
                "passed": observed == case["expected"],
                "expected": case["expected"],
                "actual": observed,
            }
        )
    counts = Counter(case["expected"]["verdict"] for case in cases)
    hash_ok = (
        file_hash(source)
        == (folder / "gold.sha256").read_text(encoding="utf-8").split()[0]
    )
    return {
        "passed": hash_ok
        and len({c["id"] for c in checks}) == 144
        and counts == {"PASS": 48, "FAIL": 48, "UNKNOWN": 24, "NOT_APPLICABLE": 24}
        and all(c["passed"] for c in checks),
        "corpus_sha256": file_hash(source),
        "labels": dict(counts),
        "cases": checks,
    }


def _tests(root, files):
    if os.environ.get("CELLIO_ACCEPTANCE_TEST_CHILD") == "1":
        return {
            "status": "FAIL",
            "diagnostic": "An acceptance test attempted to recursively run the acceptance suite",
        }
    paths = [root / "tests" / name for name in files]
    if any(not p.is_file() for p in paths):
        return {
            "status": "NOT_RUN",
            "missing": [str(p) for p in paths if not p.is_file()],
        }
    with tempfile.TemporaryDirectory() as scratch:
        xml = Path(scratch) / "results.xml"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *map(str, paths),
                "--basetemp",
                str(Path(scratch) / "pytest"),
                "--junitxml",
                str(xml),
            ],
            cwd=root,
            env={**os.environ, "CELLIO_ACCEPTANCE_TEST_CHILD": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        cases = ET.parse(xml).findall(".//testcase") if xml.is_file() else []
        failed = [
            c.get("name")
            for c in cases
            if list(c.findall("failure")) + list(c.findall("error"))
        ]
        skipped = [c.get("name") for c in cases if c.find("skipped") is not None]
        return {
            "status": "PASS"
            if cases and not failed and not skipped and proc.returncode == 0
            else "FAIL",
            "tests": len(cases),
            "test_ids": [c.get("name") for c in cases],
            "failed": failed,
            "skipped": skipped,
            "sources": {str(p.relative_to(root)): file_hash(p) for p in paths},
            "diagnostic": proc.stdout[-2500:] if proc.returncode else None,
        }


def assess(level, campaign_root, report_path):
    root = checkout()
    suites = {
        "G01": ["unit/test_config.py", "integration/test_cli_supervisor.py"],
        "G02": ["unit/test_core_contracts.py", "unit/test_fault_contracts.py"],
        "G03": [
            "unit/test_oracle_gold.py",
            "unit/test_oracle_temporal.py",
            "unit/test_oracle_frames.py",
            "integration/test_episode_worker.py",
            "integration/test_rule_identity.py",
        ],
    }
    gates = {key: _tests(root, files) for key, files in suites.items()}
    gold = evaluate_gold()
    gates["G03"].update(corpus_sha256=gold["corpus_sha256"], labels=gold["labels"])
    if not gold["passed"]:
        gates["G03"]["status"] = "FAIL"
    release_fields = {}
    if level != "r0":
        gates.update(_campaign_gates(Path(campaign_root), level, release_fields))
    if level == "r3":
        for number in range(1, 16):
            gates.setdefault(f"G{number:02d}", {"status": "NOT_RUN", "sources": []})
    missing = [g for g, v in gates.items() if v["status"] == "NOT_RUN"]
    result = {
        "level": level,
        "passed": all(v["status"] == "PASS" for v in gates.values()),
        "missing": missing,
        "gates": gates,
    }
    if level == "r3":
        from cellio import __version__

        result.update(
            software_version=__version__,
            spec_version="1.1",
            status="PASS"
            if result["passed"]
            else "FAIL"
            if any(g["status"] == "FAIL" for g in gates.values())
            else "INCOMPLETE",
            must_gates=[{"id": key, "status": gates[key]["status"]} for key in sorted(gates)],
            known_limitations=[
                "Two simulated Franka workcells on one Windows workstation.",
                "Development included main seed 1000. The secondary analysis identifies all 68 affected rows.",
            ],
            review_date=datetime.now(UTC).date().isoformat(),
            **release_fields,
        )
        if result["passed"]:
            result["engineering_status"] = "R3_COMPLETE"
    write_json(Path(report_path), result)
    return result


def _campaign_gates(folder, level, release_fields):
    from cellio.analysis.campaign import load_campaign
    from cellio.analysis.suites import fault_coverage, reset_chains, soak_evidence
    from cellio.runtime.campaign import finite_rows, suite_design

    result = {}
    cache = {}
    plans, retries = {}, {}

    def trials(suite):
        if suite not in cache:
            path = folder / suite
            if not (path / "campaign.plan.json").is_file():
                return []
            plan, items, extra = load_campaign(path)
            if plan["suite_id"] != suite or any(
                t.row["config"]["profile"] != "isaac62_lockstep" for t in items
            ):
                raise ValueError(f"Unexpected campaign/profile in {path}")
            design = suite_design(suite)
            if (
                plan.get("suite_design") != design
                or plan["plan_mode"] != design["mode"]
            ):
                raise ValueError(
                    f"{suite} has a different design than the release protocol"
                )
            if design["mode"] == "finite" and plan["runs"] != finite_rows(
                suite, plan["provenance"], design
            ):
                raise ValueError(
                    f"Rows or order differ from the predeclared {suite} protocol"
                )
            plans[suite], retries[suite] = plan, extra
            cache[suite] = items
        return cache[suite]

    def record(name, inputs, passed, details=None):
        result[name] = {
            "status": ("PASS" if passed else "FAIL") if inputs else "NOT_RUN",
            "sources": [t.attempt_id for t in inputs if t.attempt_id],
            "details": details,
        }

    def clean(t):
        return (
            t.status == "COMPLETED"
            and t.error is None
            and t.eval.get("online_offline_equal") is True
            and t.outcome == "SUCCESS"
            and t.eval.get("compliance_verdict") == "PASS"
        )

    def applies(t, family):
        return any(f["family"] == family for f in t.row["config"]["faults"])

    commissioning = trials("commission-v1")
    pilot = trials("pilot-v1")
    if level == "r1":
        a = [t for t in commissioning if t.row["cell"] == "cell_a"]
        record("R1_NORMAL", a, len(a) == 20 and all(clean(t) for t in a))
        a = [t for t in pilot if t.row["cell"] == "cell_a"]
        record(
            "R1_FAULTS",
            a,
            all(
                any(applies(t, f) and t.flags()["N_realized"] for t in a)
                for f in ("F1", "F6")
            )
            and any(t.fault.get("physical_hold_verdict") == "PASS" for t in a),
        )
        return result
    main = trials("canonical-v1") if level == "r3" else pilot
    coverage = fault_coverage(main)
    record(
        "G04",
        main,
        all(group["realized"] and group["restored"] for group in coverage.values()),
    )
    record(
        "G05",
        main,
        all(coverage[c, "F6"]["held"] for c in ("cell_a", "cell_b")),
    )
    boundaries = trials("boundaries-v1")
    installs = trials("clean-install")
    complete = all(
        len([t for t in items if t.row["cell"] == c]) == n
        and all(clean(t) for t in items)
        for c in ("cell_a", "cell_b")
        for items, n in ((commissioning, 20), (boundaries, 8), (installs, 5))
    )
    record("G06", commissioning + boundaries + installs, complete)
    checks = _tests(
        checkout(),
        ["integration/test_episode_worker.py", "integration/test_trace_conformance.py"],
    )
    result["G09"] = checks
    reset = trials("reset-v1")
    chains = reset_chains(reset)["chains"] if reset else []
    record(
        "G08", reset, len(chains) == 48 and all(x["status"] == "PASS" for x in chains)
    )
    if level == "r3":
        overhead = trials("overhead-v1")
        soak = trials("soak-v1")
        all_runs = (
            commissioning
            + boundaries
            + pilot
            + installs
            + reset
            + main
            + overhead
            + soak
        )
        reruns = [r for suite in retries.values() for r in suite]
        record(
            "G07",
            all_runs,
            all(
                t.attempt_id
                and t.error is None
                and t.status in ("COMPLETED", "CANCELLED")
                for t in all_runs
            )
            and bool(main)
            and bool(soak)
            and all(
                r["integrity"]
                and r["error"] is None
                and r["status"] in {"COMPLETED", "CANCELLED"}
                for r in reruns
            )
            and all(
                len({p["provenance"][k] for p in plans.values()}) == 1
                for k in (
                    "assets_hash",
                    "code_hash",
                    "environment_hash",
                    "requirements_hash",
                )
            ),
            {"retry_ids": [r["attempt_id"] for r in reruns]},
        )
        record(
            "G10",
            main,
            len(main) == 820
            and all(
                t.started
                and t.status == "COMPLETED"
                and t.eval.get("end_step") == 7200
                and t.error is None
                and t.eval.get("online_offline_equal") is True
                for t in main
            ),
        )
        info = soak_evidence(folder / "soak-v1", soak) if soak else {}
        record(
            "G11",
            soak,
            info.get("status") == "PASS",
            info,
        )
        metrics_path = folder / "overhead-v1" / "reports" / "metrics.json"
        metrics = (
            json.loads(metrics_path.read_text(encoding="utf-8"))
            if metrics_path.is_file()
            else {}
        )
        from cellio.analysis.comparisons import overhead as paired_overhead

        record(
            "G15",
            overhead,
            len(overhead) == 180
            and all(
                t.status == "COMPLETED" and t.performance.get("running_wall_ns", 0) > 0
                for t in overhead
            )
            and metrics.get("plan_hash") == plans["overhead-v1"]["plan_hash"]
            and digest(metrics.get("overhead")) == digest(paired_overhead(overhead)),
        )
        from cellio.release import release_gates

        result.update(release_gates(checkout(), folder))
        from cellio.analysis.targets import target_readout

        release_fields["campaign_ids"] = {
            suite: plan["plan_hash"] for suite, plan in sorted(plans.items())
        }
        release_fields["should_metrics"] = target_readout(
            main, overhead, reset, paired_overhead(overhead), info
        )
        sources = result["G13"].get("sources", [])
        release_fields["evidence_manifest"] = (
            {"path": sources[0], "sha256": file_hash(Path(sources[0]))}
            if sources and Path(sources[0]).is_file()
            else None
        )
    return result
