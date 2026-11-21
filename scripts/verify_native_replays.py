"""Re-evaluate eight extracted representative Isaac Sim runs with an installed CellIO."""

import argparse
import gzip
import json
from pathlib import Path

from cellio.analysis.replay import read_attempt
from cellio.core.oracle.geometry import quiet
from cellio.inspection.replay import extract_witness, replay_witness
from cellio.io import digest, file_hash

FRESH = {
    "cell_a_healthy": "PASS",
    "cell_b_healthy": "PASS",
    "cell_c_upper_reference": "PASS",
    "cell_c_upper_stale": "FAIL",
    "cell_c_both_good_reference": "PASS",
    "cell_c_both_good_stale": "FAIL",
}
HISTORICAL = {
    "cell_a_stop_dwell": "603adae1-259f-4be7-b953-70ae3f4d7585",
    "cell_b_unexercised_clamp": "03ebf218-9130-403a-9a35-90cd0b70b58b",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def witness_digest(requirements):
    witnesses = {
        r["requirement_id"]: [r["verdict"], r["first_witness_step"]]
        for r in requirements
    }
    return digest(witnesses)


def verify_case(root, case):
    name = case["case"]
    directory = root / name
    if name in HISTORICAL:
        require(case["attempt_id"] == HISTORICAL[name], "Canonical attempt ID changed")
    else:
        require(
            case["compliance"] == FRESH[name], "Frozen qualification verdict changed"
        )
    require(
        file_hash(directory / "manifest.final.json") == case["manifest_sha256"],
        f"{name}: manifest differs from experiment index",
    )
    # read_attempt first performs the same complete verification as the CLI
    # verify-artifact, then recomputes the rules from the verified frames.
    abort = case.get("abort_step")
    dwell_end = None
    quiet_boundaries = 0

    def observe_dwell(frame, _updates):
        nonlocal quiet_boundaries, dwell_end
        step = frame["step"]
        if step < abort or dwell_end is not None:
            return
        if frame["truth"]["complete"] and quiet(frame["truth"], True, False) is True:
            quiet_boundaries += 1
        else:
            quiet_boundaries = 0
        if quiet_boundaries == 13:  # Twelve intervals between successive boundaries.
            dwell_end = step

    evidence = read_attempt(
        directory, on_frame=observe_dwell if abort is not None else None
    )
    require(
        evidence["completion_status"] == "COMPLETED"
        and evidence["end_step"] == 7200
        and evidence["run_id"] == case["run_id"]
        and evidence["attempt_id"] == case["attempt_id"]
        and evidence["source_digest"] == case["trace_chain_sha256"]
        and evidence["task_outcome"] == case["task"]
        and evidence["compliance_verdict"] == case["compliance"]
        and evidence["observed_results_equal"] is True
        and len(evidence["requirements"]) == case["requirement_count"]
        and witness_digest(evidence["requirements"]) == case["verdict_witness_sha256"],
        f"{name}: replay disagrees with expected identity, verdicts or first witness steps",
    )
    if name in FRESH:
        require(
            evidence["same_rule_revision"] is True
            and evidence["online_offline_equal"] is True,
            f"{name}: expected a verified recording-time rule identity match",
        )
    else:
        require(
            evidence["same_rule_revision"] is None
            and evidence["online_offline_equal"] is None,
            f"{name}: legacy recording-time identity must remain unknown",
        )

    detail = {}
    if name.startswith("cell_c_"):
        native = replay_witness(extract_witness(directory / "native"))
        observed = [
            (part["result_part_id"], part["observed_bin"]) for part in native["parts"]
        ]
        expected = list(zip(case["owners"], case["destinations"], strict=True))
        require(
            observed == expected and native["verdict"] == case["compliance"],
            f"{name}: native output poses or result owners disagree",
        )
        detail = {
            "result_owners": [v[0] for v in observed],
            "observed_bins": [v[1] for v in observed],
        }
    if name == "cell_a_stop_dwell":
        failed = {
            r["requirement_id"]: r["first_witness_step"]
            for r in evidence["requirements"]
            if r["verdict"] == "FAIL"
        }
        require(
            failed.get("cell_a.r06.abort_stopped") == case["deadline_step"]
            and dwell_end == case["first_dwell_end_step"],
            "Cell A stop dwell and deadline disagree with the canonical study",
        )
        detail = {
            "deadline_step": failed["cell_a.r06.abort_stopped"],
            "first_dwell_end_step": dwell_end,
        }
    if name == "cell_b_unexercised_clamp":
        (fault,) = read(directory / "faults.json")
        with gzip.open(directory / "commands.jsonl.gz", "rb") as stream:
            intents = (
                record["data"]
                for record in map(json.loads, stream)
                if record["kind"] == "intents"
            )
            closes = [
                intent["issued_step"]
                for intent in intents
                if intent["actuator_id"] == "actuator.clamp"
                and intent["kind"] == "JOINT_POSITION"
                and intent["value"] < 0
            ]
        require(
            fault["coverage"] == "NOT_EXERCISED"
            and fault["delayed_packet_count"] == 0
            and fault["trigger_match_step"] == case["trigger_step"]
            and not closes,
            "Cell B clamp trigger, command opportunity or delayed packet count changed",
        )
        detail = {
            "coverage": fault["coverage"],
            "trigger_step": fault["trigger_match_step"],
            "close_commands": len(closes),
            "delayed_packets": fault["delayed_packet_count"],
        }
    return {
        "case": name,
        "attempt_id": case["attempt_id"],
        "compliance": case["compliance"],
        "requirements": case["requirement_count"],
        "same_rule_revision": evidence["same_rule_revision"],
        "online_offline_equal": evidence["online_offline_equal"],
        "expected_witnesses_equal": True,
        **detail,
    }


def verify(root):
    index = read(root / "index.json")
    cases = index["cases"]
    require(
        index["schema"] == "cellio.native-replay-selection/1"
        and [case["case"] for case in cases] == [*FRESH, *HISTORICAL],
        "Expected the six qualification cases and two canonical exemplars",
    )
    results = [verify_case(root, case) for case in cases]
    return {
        "verified_cases": len(results),
        "matched_requirements": sum(r["requirements"] for r in results),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", nargs="?", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument("--output", type=Path, help="Save the CPU replay results")
    args = parser.parse_args()
    report = json.dumps(verify(args.directory), sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_bytes(report.encode("utf-8"))
    print(report, end="")
