"""Compare historical native Cell C records with the common framework evaluator."""

import argparse
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from cellio.analysis import evaluate
from cellio.artifacts import frames, verify_artifact
from cellio.inspection.archive import import_archive
from cellio.inspection.framework_oracle import DEST, FOLLOW, MEASURE, OWNER, SEQUENCE
from cellio.inspection.replay import replay_witness
from cellio.io import digest, file_hash

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "results" / "qualification" / "cell-c-softwarex-v1"
WITNESSES = (
    ROOT / "results" / "publication" / "softwarex-extension" / "cell-c-witnesses.zip"
)


def require(value, message):
    if not value:
        raise ValueError(message)


def inspect_case(source, artifact, case, witness, observations):
    ident = case["id"]
    (first,) = case["attempts"]
    native = source / first["attempt_id"] / "episode"
    require(
        first["attempt_id"] == f"{ident}/attempt-01"
        and first["core_verified"]
        and first["geometry_verified"]
        and first["status"] == "completed",
        f"{ident}: expected the original verified first attempt",
    )
    for name, expected in witness["source_sha256"].items():
        require(
            file_hash(native / name) == first["sha256"][name] == expected,
            f"{ident}: original native source changed: {name}",
        )
    if not artifact.exists():
        import_archive(native, artifact)
    verified = verify_artifact(artifact)
    require(
        verified["source_kind"] == "historical_native"
        and verified["artifact_digest"] == digest(witness),
        f"{ident}: different source",
    )
    replayed = evaluate(artifact)
    require(
        replayed["source_kind"] == "historical_native"
        and replayed["coverage"] == "recorded_events"
        and replayed["online_offline_equal"] is None,
        f"{ident}: archive must not claim an original online framework run",
    )
    portable = replay_witness(witness)
    require(
        replayed["compliance_verdict"] == portable["verdict"],
        f"{ident}: verdict differs",
    )
    observed = list(frames(artifact))
    require(
        len(observed) == len(witness["events"]) == verified["records"],
        f"{ident}: incomplete evidence",
    )
    stamps = {}
    for frame, event in zip(observed, witness["events"], strict=True):
        state = frame["truth"]["plant_state"]
        require(
            frame["step"] == event["frame"] * 4
            and (state["part"], state["event"]) == (event["part"], event["event"]),
            f"{ident}: changed source event or timestamp",
        )
        stamps[event["part"], event["event"]] = frame["step"]
    requirements = {r["requirement_id"]: r for r in replayed["requirements"]}
    evaluated_parts = replayed["scene_evidence"]["parts"]
    require(
        [p["part"] for p in evaluated_parts] == ["P1", "P2"],
        f"{ident}: missing evaluated part",
    )
    parts = []
    failures = {MEASURE: [], OWNER: [], FOLLOW: [], DEST: [], SEQUENCE: []}
    for row, actual in zip(portable["parts"], evaluated_parts, strict=True):
        part = row["part"]
        reference = observations[ident, part]
        require(
            reference["attempt_id"] == first["attempt_id"],
            f"{ident}: mismatched attempt",
        )
        report = {
            "part": part,
            "result_part_id": reference["decision"]["result_part_id"],
            "expected_bin": reference["true_bin"],
            "commanded_bin": reference["decision"]["route"],
            "observed_bin": reference["physical_output"]["region"],
            "geometry_height_mm": reference["geometry_reference"]["height_mm"],
            "probe_height_mm": reference["probe"]["height_mm"],
        }
        require(
            all(report[k] == row[k] == actual[k] for k in report)
            and actual["violations"] == row["violations"],
            f"{ident}/{part}: common result differs from historical evidence",
        )
        inspection = stamps[part, "inspection"]
        settled = stamps[part, "output_settled"]
        if reference["stale_result"]:
            failures[OWNER].append(inspection)
        if reference["physical_misroute"]:
            failures[DEST].append(settled)
        require(
            reference["decision"]["route"] == reference["physical_output"]["region"]
            and reference["expected_fault_prediction"],
            f"{ident}: unregistered outcome",
        )
        parts.append(report)
    require(set(requirements) == set(failures), f"{ident}: missing or additional rule")
    for rule, steps in failures.items():
        expected = "FAIL" if steps else "PASS"
        terminal = "inspection" if rule in (MEASURE, OWNER) else "output_settled"
        first_step = steps[0] if steps else stamps["P2", terminal]
        actual = requirements[rule]
        require(
            (actual["verdict"], actual["first_witness_step"], actual["violated_count"])
            == (expected, first_step, len(steps)),
            f"{ident}: {rule} differs from native evidence",
        )
    return {
        "case": ident,
        "source_sha256": witness["source_sha256"],
        "artifact_digest": verified["artifact_digest"],
        "rules_digest": replayed["rules_digest"],
        "verdict": replayed["compliance_verdict"],
        "requirements": {
            k: [v["verdict"], v["first_witness_step"]] for k, v in requirements.items()
        },
        "parts": parts,
    }


def compare(source, output):
    summary_path = REFERENCE / "summary.json"
    rows_path = REFERENCE / "observations.jsonl"
    summary = json.loads(summary_path.read_bytes())
    require(
        file_hash(rows_path) == summary["observations_sha256"],
        "Reference observations changed",
    )
    rows = [json.loads(line) for line in rows_path.read_bytes().splitlines()]
    index = {(row["case_id"], row["part_id"]): row for row in rows}
    design = summary["cases"]
    expected = {(c["id"], part) for c in design for part in ("P1", "P2")}
    require(
        len(rows) == len(index) == 2 * len(design) and expected == set(index),
        "Published cases and observations do not match",
    )
    cases = []
    with ZipFile(WITNESSES) as archive:
        require(
            sorted(archive.namelist()) == sorted(c["id"] + ".json" for c in design),
            "Published witness inventory differs from allocation",
        )
        for case in design:
            name = case["id"]
            archived = json.loads(archive.read(name + ".json"))
            cases.append(inspect_case(source, output / name, case, archived, index))
    require(
        len(design) == summary["completed_first_attempts"] == 24,
        "Missing native attempts",
    )
    return {
        "schema": "cellio.inspection.framework-historical-equivalence/1",
        "method": "Common evaluate on sparse sealed observations of historical native first attempts",
        "new_simulations": 0,
        "historical_native_attempts": len(cases),
        "parts_checked": len(rows),
        "rule_verdicts_and_first_witnesses_checked": sum(
            len(c["requirements"]) for c in cases
        ),
        "verdicts": dict(Counter(c["verdict"] for c in cases)),
        "summary_sha256": file_hash(summary_path),
        "observations_sha256": file_hash(rows_path),
        "witness_archive_sha256": file_hash(WITNESSES),
        "cases_digest": digest(cases),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, type=Path, help="Original native campaign root"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Directory for imported artifacts"
    )
    parser.add_argument(
        "--receipt", required=True, type=Path, help="Comparison JSON destination"
    )
    args = parser.parse_args()
    receipt = compare(args.source, args.output)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes(
        (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    )
    print(json.dumps({k: v for k, v in receipt.items() if k != "cases"}))


if __name__ == "__main__":
    main()
