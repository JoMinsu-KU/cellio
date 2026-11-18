"""Evaluate release criteria against tests and verified campaign artifacts."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from cellio.io import file_hash, write_json


def checkout():
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "fixtures/oracle/gold.json").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        "Run acceptance from the source checkout with its frozen fixtures"
    )


def evaluate_gold():
    from cellio.core.oracle.fixtures import evaluate_fixture

    folder = checkout() / "fixtures" / "oracle"
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
        "G01": ["unit/test_config.py"],
        "G02": ["unit/test_core_contracts.py", "unit/test_fault_contracts.py"],
        "G03": [
            "unit/test_oracle_gold.py",
            "unit/test_oracle_temporal.py",
            "unit/test_oracle_frames.py",
            "integration/test_episode_worker.py",
        ],
    }
    gates = {key: _tests(root, files) for key, files in suites.items()}
    gold = evaluate_gold()
    gates["G03"].update(corpus_sha256=gold["corpus_sha256"], labels=gold["labels"])
    if not gold["passed"]:
        gates["G03"]["status"] = "FAIL"
    if level != "r0":
        gates.update(_campaign_gates(Path(campaign_root), level))
    missing = [g for g, v in gates.items() if v["status"] == "NOT_RUN"]
    result = {
        "level": level,
        "passed": all(v["status"] == "PASS" for v in gates.values()),
        "missing": missing,
        "gates": gates,
    }
    write_json(Path(report_path), result)
    return result


def _campaign_gates(folder, level):
    from cellio.analysis.campaign import load_campaign
    from cellio.analysis.suites import reset_chains

    result = {}
    cache = {}

    def trials(suite):
        if suite not in cache:
            path = folder / suite
            if not (path / "campaign.plan.json").is_file():
                return []
            plan, items, _ = load_campaign(path)
            if plan["suite_id"] != suite or any(
                t.row["config"]["profile"] != "isaac62_lockstep" for t in items
            ):
                raise ValueError(f"Unexpected campaign/profile in {path}")
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
    evidence = {
        (c, f): [t for t in main if t.row["cell"] == c and applies(t, f)]
        for c in ("cell_a", "cell_b")
        for f in (f"F{i}" for i in range(1, 7))
    }
    record(
        "G04",
        main,
        all(
            any(t.flags()["N_realized"] for t in group)
            and any(t.flags()["N_clear"] for t in group)
            for group in evidence.values()
        ),
    )
    record(
        "G05",
        main,
        all(
            any(
                t.fault.get("physical_hold_verdict") == "PASS"
                for t in evidence[c, "F6"]
            )
            for c in ("cell_a", "cell_b")
        ),
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
            commissioning + boundaries + installs + reset + main + overhead + soak
        )
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
            and bool(soak),
        )
        record(
            "G10",
            main,
            len(main) == 820
            and all(
                t.started and t.status == "COMPLETED" and t.eval.get("end_step") == 7200
                for t in main
            ),
        )
        sp = folder / "soak-v1" / "soak.result.json"
        info = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
        record(
            "G11",
            soak,
            info.get("wall_seconds", 0) >= 7200
            and info.get("completed", 0) >= 200
            and all(t.status == "COMPLETED" for t in soak),
            info,
        )
        record(
            "G15",
            overhead,
            len(overhead) == 180
            and all(
                t.status == "COMPLETED" and t.performance.get("running_wall_ns", 0) > 0
                for t in overhead
            )
            and (folder / "overhead-v1" / "reports" / "metrics.json").is_file(),
        )
        from cellio.release import release_gates
        result.update(release_gates(checkout(), folder))
    return result
