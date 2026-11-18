"""Compare the researcher-authored manual and modular observation-fault paths on identical scene inputs."""

import argparse
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from cellio.analysis.manual import ManualObservationFault
from cellio.analysis.manual_comparison import compare
from cellio.analysis.replay import read
from cellio.environment import data_root, lock_context
from cellio.io import digest, file_hash, write_json
from cellio.runtime.campaign import exclusive, initial_state, scenario
from cellio.runtime.episode import run_episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True, choices=("cell_a", "cell_b"))
    parser.add_argument(
        "--arm", required=True, choices=("f1_low", "f1_high", "f2", "f3", "f5")
    )
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--backend", choices=("mock", "isaac62"), default="isaac62")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slot", choices=("manual", "modular"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    out = args.output.resolve()
    profile = "mock_lockstep" if args.backend == "mock" else "isaac62_lockstep"
    if args.slot:
        ctx, identity = lock_context(profile)
        cfg = scenario(args.cell, args.arm, args.seed, profile=profile)
        instance = str(uuid4())
        path = out / args.slot / instance
        result = run_episode(
            cfg,
            path,
            run_id=digest([identity, args.cell, args.arm, args.seed, args.slot])[:24],
            attempt_id=instance,
            initial_state=initial_state(args.cell, args.seed),
            **ctx,
            backend=args.backend,
            fault_factory=ManualObservationFault if args.slot == "manual" else None,
        )
        write_json(
            out / (args.slot + ".json"),
            {
                "directory": str(path),
                "completion_status": result["completion_status"],
                "provenance": identity,
                "script_sha256": file_hash(Path(__file__)),
            },
        )
        return 0 if result["completion_status"] == "COMPLETED" else 4
    out.mkdir(parents=True, exist_ok=False)
    worker = data_root() / (
        "gpu-worker" if args.backend == "isaac62" else "mock-worker"
    )
    worker.mkdir(parents=True, exist_ok=True)
    with exclusive(worker):
        for slot in ("modular", "manual"):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--cell",
                args.cell,
                "--arm",
                args.arm,
                "--seed",
                str(args.seed),
                "--backend",
                args.backend,
                "--output",
                str(out),
                "--slot",
                slot,
            ]
            subprocess.run(command, check=True)
    workers = [read(out / (slot + ".json")) for slot in ("modular", "manual")]
    proof = compare(*(Path(w["directory"]) for w in workers))
    proof["worker_provenance"] = [w["provenance"] for w in workers]
    proof["script_sha256"] = [w["script_sha256"] for w in workers]
    if (
        workers[0]["provenance"] != workers[1]["provenance"]
        or workers[0]["script_sha256"] != workers[1]["script_sha256"]
    ):
        proof["status"] = "INCOMPARABLE_PROVENANCE"
    write_json(out / "comparison.json", proof)
    print(proof["status"], out / "comparison.json")
    return 0 if proof["status"] == "PASS" else 6


if __name__ == "__main__":
    raise SystemExit(main())
