"""Package only the sealed data needed to replay the specified eight native examples."""

import argparse
import gzip
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from verify_native_replays import FRESH, HISTORICAL, read, require, witness_digest

from cellio.artifacts import verify_artifact
from cellio.io import file_hash

REPO = Path(__file__).resolve().parents[1]
CANONICAL_INDEX = (
    REPO / "results/publication/candidate-8ad96bd/primary/first-attempt-index.json"
)
PROTOCOL = REPO / "protocols/three-cell-framework-v1.json"
PRIVATE_MARKERS = (
    rb"(?:^|[^A-Za-z0-9_])[A-Za-z]:[/\\]",
    rb"/Users/|/home/|\\Users\\|AppData[/\\]|file://|/mnt/[a-z]/",
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{26,}|AKIA[0-9A-Z]{16}",
    rb'https?://[^/\s"]+@',
    rb"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
)
PRIVATE = re.compile(b"|".join(PRIVATE_MARKERS), re.IGNORECASE)


def audit_text(path):
    # Read the decompressed text. USD, vendor assets, video, logs and worker
    # envelopes are outside the manifest inventory used below.
    compressed = path.name.endswith(".jsonl.gz")
    if not compressed and (
        path.suffix not in (".json", ".jsonl", ".lock") and path.name != "COMPLETE"
    ):
        raise ValueError(f"Unexpected non-text inventory member: {path.name}")
    opener = gzip.open if compressed else open
    with opener(path, "rb") as stream:
        for line in stream:
            if any(
                prefix in line
                for prefix in (
                    b"\\",
                    b":/",
                    b"/home/",
                    b"/Users/",
                    b"/mnt/",
                    b"PRIVATE KEY",
                    b"gh",
                    b"AKIA",
                    b"@",
                )
            ):
                require(
                    not PRIVATE.search(line),
                    f"Sensitive path or credential marker in {path.name}",
                )


def entry(name, compressed=True):
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def select_cases(new_runs, summary, canonical):
    """Use the frozen six-case recipe plus the two previously indexed historical trials."""
    report = read(summary)
    design = read(PROTOCOL)["cases"]
    require(
        report["verified"] and report["protocol_sha256"] == file_hash(PROTOCOL),
        "Qualification did not verify",
    )
    require(
        [v["id"] for v in report["cases"]] == [r["id"] for r in design] == list(FRESH),
        "Fresh case set or order differs from the six-case design",
    )
    chosen = []
    for condition, row in zip(design, report["cases"], strict=True):
        name = condition["id"]
        directory = new_runs / name / row["run_id"] / row["attempt_id"]
        require(
            row["attempt_states"] == ["COMPLETED"] and row["compliance"] == FRESH[name],
            f"{name}: unexpected reference verdict or attempt history",
        )
        chosen.append(
            (
                directory,
                {
                    "case": name,
                    "cohort": "v1.1.1 common-framework qualification",
                    "run_id": row["run_id"],
                    "attempt_id": row["attempt_id"],
                    "task": "SUCCESS",
                    "compliance": FRESH[name],
                    "trace_chain_sha256": row["artifact_digest"],
                    "requirement_count": row["requirements"],
                    **{
                        k: condition[k]
                        for k in ("owners", "destinations")
                        if k in condition
                    },
                },
            )
        )
    historical = {r["attempt_id"]: r for r in read(CANONICAL_INDEX)}
    for name, attempt in HISTORICAL.items():
        row = historical[attempt]
        directory = canonical / row["run_id"] / attempt
        extra = (
            {"abort_step": 1840, "deadline_step": 1912, "first_dwell_end_step": 1938}
            if (name == "cell_a_stop_dwell")
            else {"trigger_step": 3273}
        )
        chosen.append(
            (
                directory,
                {
                    "case": name,
                    "cohort": "8ad96bd canonical first attempts",
                    "run_id": row["run_id"],
                    "attempt_id": attempt,
                    "task": row["task"],
                    "compliance": row["compliance"],
                    "manifest_sha256": row["manifest_sha256"],
                    "trace_chain_sha256": row["trace_chain_sha256"],
                    "verdict_witness_sha256": row["witness_sha256"],
                    "requirement_count": 16 if row["cell"] == "cell_a" else 18,
                    **extra,
                },
            )
        )
    return chosen


def write_case(archive, directory, case):
    checked = verify_artifact(directory)
    require(
        checked["valid"]
        and checked["completion_status"] == "COMPLETED"
        and checked["ticks"] == 7200
        and checked["artifact_digest"] == case["trace_chain_sha256"]
        and checked["attempt_id"] == case["attempt_id"]
        and checked["run_id"] == case["run_id"],
        f"{case['case']}: native attempt or recorded digest differs",
    )
    manifest = read(directory / "manifest.final.json")
    manifest_sha = file_hash(directory / "manifest.final.json")
    results = read(directory / "requirements.json")
    witness_sha = witness_digest(results)
    result = read(directory / "result.json")
    require(
        result["backend"] == "isaac62"
        and result["task_outcome"] == case["task"]
        and result["compliance_verdict"] == case["compliance"]
        and len(results) == case["requirement_count"],
        "Unexpected sealed result",
    )
    if case["case"] in HISTORICAL:
        require(
            case["manifest_sha256"] == manifest_sha
            and case["verdict_witness_sha256"] == witness_sha,
            "Historical file or verdict identity differs from published index",
        )
    else:
        require(
            "rule_lock_sha256" in checked
            and read(directory / "environment.lock.json")["packages"]["cellio"]
            == "1.1.1",
            "Fresh qualification must originate from v1.1.1",
        )
    for name in sorted(set(manifest["files"]) | {"manifest.final.json", "COMPLETE"}):
        path = directory / name
        audit_text(path)
        with (
            path.open("rb") as src,
            archive.open(
                entry(f"{case['case']}/{name}", path.suffix != ".gz"), "w"
            ) as dest,
        ):
            shutil.copyfileobj(src, dest, 1024 * 1024)
    return {
        **case,
        "manifest_sha256": manifest_sha,
        "verdict_witness_sha256": witness_sha,
    }


def package(runs, summary, canonical, output):
    choices = select_cases(runs, summary, canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        target = Path(temp) / "native.zip"
        with zipfile.ZipFile(target, "x") as archive:
            rows = [write_case(archive, folder, case) for folder, case in choices]
            archive.writestr(
                entry("index.json"),
                json.dumps(
                    {
                        "schema": "cellio.native-replay-selection/1",
                        "source_release": "1.1.1",
                        "canonical_index_sha256": file_hash(CANONICAL_INDEX),
                        "qualification_summary_sha256": file_hash(summary),
                        "cases": rows,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            archive.writestr(
                entry("verify_native_replays.py"),
                Path(__file__).with_name("verify_native_replays.py").read_bytes(),
            )
            archive.writestr(
                entry("README.md"), (REPO / "docs/native_replay.md").read_bytes()
            )
        if os.name == "nt":
            target.rename(output)
        else:
            os.link(target, output)
    return {
        "bytes": output.stat().st_size,
        "sha256": file_hash(output),
        "cases": len(choices),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--canonical", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            package(args.runs, args.summary, args.canonical, args.output), indent=2
        )
    )
