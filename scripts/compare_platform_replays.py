"""Compare the Windows and WSL verdicts and first witnesses for a closed canonical campaign."""

import argparse
import json
from pathlib import Path

from cellio.analysis.campaign import attempt_histories, lines, safe_child
from cellio.io import file_hash, write_json
from cellio.runtime.campaign import finite_rows, read_plan, suite_design


def compare(directory, windows, wsl):
    plan = read_plan(directory / "campaign.plan.json")
    design = suite_design("canonical-v1")
    rows = plan["runs"]
    if (
        plan["suite_id"] != "canonical-v1"
        or plan["suite_design"] != design
        or rows != finite_rows("canonical-v1", plan["provenance"], design)
    ):
        raise ValueError("Expected the registered canonical campaign")
    source = plan["provenance"]["code_hash"]
    environments = [report["replay_environment"] for report in (windows, wsl)]
    if (
        environments[0]["os"] != "Windows"
        or environments[1]["os"] != "Linux"
        or "microsoft" not in environments[1]["kernel"].lower()
        or any(e["source_hash"] != source for e in environments)
    ):
        raise ValueError(
            "Both platform reports must replay the registered candidate on Windows and WSL"
        )
    ids = [r["run_id"] for r in rows]
    history = attempt_histories(lines(directory / "ledger.jsonl"), set(ids))
    attempts = []
    for report in (windows, wsl):
        first = report["attempts"]
        if (
            report["suite_id"] != plan["suite_id"]
            or report["plan_hash"] != plan["plan_hash"]
            or report["protocol_hash"] != plan["protocol_hash"]
            or [r["run_id"] for r in first] != ids
        ):
            raise ValueError("Report and registered canonical first attempts differ")
        attempts.append(first)
    differences = []
    fields = (
        "attempt_id",
        "source_digest",
        "rules_digest",
        "witness_digest",
        "outcome",
    )
    for row, left, right in zip(rows, *attempts, strict=True):
        mismatch = [k for k in fields if not left.get(k) or left[k] != right.get(k)]
        run = row["run_id"]
        if history[run] and history[run][0][-1]["state"] == "COMPLETED":
            ident = history[run][0][0]["attempt_id"]
            raw = safe_child(directory, run, ident)
            manifest = raw / "manifest.final.json"
            if (
                left.get("attempt_id") != ident
                or not manifest.is_file()
                or not (raw / "COMPLETE").is_file()
                or (raw / "COMPLETE").read_bytes()
                != (file_hash(manifest) + "\n").encode("ascii")
                or json.loads(manifest.read_bytes())["last_record_hash"]
                != left.get("source_digest")
            ):
                mismatch.append("sealed_first_attempt")
        else:
            mismatch.append("first_attempt_ledger")
        for os_name, value in zip(("windows", "wsl"), (left, right), strict=True):
            if (
                value["status"] != "COMPLETED"
                or value.get("error")
                or value.get("online_offline_equal") is not True
                or value.get("end_step") != row["config"]["episode"]["horizon_steps"]
            ):
                mismatch.append(os_name + "_incomplete_or_unverified")
        if mismatch:
            differences.append({"run_id": row["run_id"], "fields": mismatch})
    return {
        "status": "FAIL" if differences else "PASS",
        "plan_hash": plan["plan_hash"],
        "protocol_hash": plan["protocol_hash"],
        "environments": environments,
        "planned": len(ids),
        "matching_verified_first_attempts": len(ids) - len(differences),
        "differences": differences,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument(
        "--windows", type=Path, required=True, help="Windows metrics.json"
    )
    parser.add_argument("--wsl", type=Path, required=True, help="WSL metrics.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(
        args.campaign,
        json.loads(args.windows.read_bytes()),
        json.loads(args.wsl.read_bytes()),
    )
    result["report_sha256"] = {
        k: file_hash(p) for k, p in (("windows", args.windows), ("wsl", args.wsl))
    }
    write_json(args.output, result)
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 5


if __name__ == "__main__":
    raise SystemExit(main())
