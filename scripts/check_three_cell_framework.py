"""Check fresh shared-run artifacts from the six-case, three-workcell qualification."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cellio.artifacts import frames
from cellio.inspection.replay import extract_witness, replay_witness
from cellio.io import file_hash, write_json


def read(path):
    return json.loads(path.read_bytes())


def cli(verb, directory):
    output = subprocess.check_output(
        [sys.executable, "-m", "cellio", verb, "--artifact", str(directory)]
    )
    return json.loads(output)


def applied_routes(directory):
    readings = [
        e for e in read(directory / "native/events.json") if e["event"] == "inspection"
    ]
    steps = {4 * reading["frame"] for reading in readings}
    ids = {"inspection.source_p2", "inspection.route_ok"}
    checks = []
    for frame in frames(directory):
        applied = [row for row in frame["applied"] if row["actuator_id"] in ids]
        if not applied:
            continue
        step = frame["step"]
        if (
            step not in steps
            or len(applied) != 2
            or any(
                row["status"] != "APPLIED" or row["applied_step"] != step
                for row in applied
            )
        ):
            raise ValueError(f"Unexpected route command at step {step}")
        values = {row["actuator_id"]: row["effective_value"] for row in applied}
        if set(values) != ids or any(
            type(value) is not bool for value in values.values()
        ):
            raise ValueError(f"Invalid route command at step {step}")
        reading = next(r for r in readings if 4 * r["frame"] == step)
        decision = reading["decision"]
        owner = "P2" if values["inspection.source_p2"] else "P1"
        route = "OK" if values["inspection.route_ok"] else "NG"
        if owner != decision["result_part_id"] or route != decision["route"]:
            raise ValueError(
                f"Shared command and native decision disagree at step {step}"
            )
        checks.append(
            {"part": reading["part"], "step": step, "owner": owner, "route": route}
        )
    if [row["part"] for row in checks] != ["P1", "P2"]:
        raise ValueError("Expected one applied route decision for each inspected part")
    return checks


def check(condition, run_root):
    root = run_root / condition["id"]
    ledger = [
        json.loads(line) for line in (root / "ledger.jsonl").read_bytes().splitlines()
    ]
    terminal = [
        row
        for row in ledger
        if row["state"]
        in {"COMPLETED", "INFRA_ERROR", "CANCELLED", "SKIPPED_PRECONDITION"}
    ]
    completed = [row for row in terminal if row["state"] == "COMPLETED"]
    if len(completed) != 1:
        raise ValueError(
            f"{condition['id']}: expected one completed attempt, found {len(completed)}"
        )
    identity = completed[0]
    directory = root / identity["run_id"] / identity["attempt_id"]
    result = read(directory / "result.json")
    config = read(directory / "config.resolved.json")
    checked = cli("verify-artifact", directory)
    evaluated = cli("evaluate", directory)
    expected = condition["compliance"]
    if not (
        checked["valid"]
        and checked["ticks"] == config["episode"]["horizon_steps"]
        and checked["artifact_digest"] == evaluated["source_digest"]
        and result["backend"] == "isaac62"
        and result["scene_plugin"]["scene_id"]
        == config["scene"]["cell_id"]
        == condition["cell"]
        and result["completion_status"] == evaluated["completion_status"] == "COMPLETED"
        and evaluated["online_offline_equal"] is True
        and evaluated["compliance_verdict"] == result["compliance_verdict"] == expected
    ):
        raise ValueError(
            f"{condition['id']}: shared run or independent evaluation mismatch"
        )
    row = {
        "id": condition["id"],
        "cell": condition["cell"],
        "run_id": identity["run_id"],
        "attempt_id": identity["attempt_id"],
        "attempt_states": [r["state"] for r in terminal],
        "controller": config["controller"]["id"],
        "artifact_digest": evaluated["source_digest"],
        "rules_digest": evaluated["rules_digest"],
        "ticks": checked["ticks"],
        "requirements": len(evaluated["requirements"]),
        "compliance": expected,
        "task_outcome": evaluated["task_outcome"],
        "online_offline_equal": True,
    }
    if condition["cell"] == "cell_c":
        observed = replay_witness(extract_witness(directory / "native"))
        owners = [part["result_part_id"] for part in observed["parts"]]
        destinations = [part["observed_bin"] for part in observed["parts"]]
        if (owners, destinations, observed["verdict"]) != (
            condition["owners"],
            condition["destinations"],
            expected,
        ):
            raise ValueError(f"{condition['id']}: native observations mismatch")
        row.update(
            parts=observed["parts"],
            native_sha256=observed["source_sha256"],
            applied_routes=applied_routes(directory),
        )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--protocol", type=Path, default=Path("protocols/three-cell-framework-v1.json")
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Collect fresh headless runs in a new directory before checking them.",
    )
    args = parser.parse_args()
    plan = read(args.protocol)
    if args.run:
        args.runs.mkdir(parents=True, exist_ok=False)
        for condition in plan["cases"]:
            print(f"Starting {condition['id']}", flush=True)
            config = ["--config", condition["config"]]
            subprocess.run(
                [sys.executable, "-m", "cellio", "validate", *config], check=True
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cellio",
                    "run",
                    *config,
                    "--headless",
                    "--output",
                    str(args.runs / condition["id"]),
                ],
                check=True,
            )
    results = [check(condition, args.runs) for condition in plan["cases"]]
    write_json(
        args.output,
        {
            "schema": "cellio.three-cell-framework-results/1",
            "protocol_sha256": file_hash(args.protocol),
            "profile": plan["profile"],
            "cases": results,
            "verified": True,
        },
    )
    cells = {row["cell"] for row in results}
    print(f"Verified {len(results)} fresh runs across {len(cells)} workcells.")


if __name__ == "__main__":
    main()
