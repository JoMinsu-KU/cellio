"""Freeze real calibration and successful commissioning evidence before the main campaign."""

import argparse
from pathlib import Path
from cellio.analysis.campaign import load_campaign
from cellio.environment import data_root, lock_context
from cellio.io import digest, file_hash, write_json
from cellio.runtime.campaign import ARMS, FINITE, suite_design


def freeze(root, calibration):
    ctx, identity = lock_context("isaac62_lockstep")
    if not calibration.is_file():
        raise ValueError("Missing measured robot frame calibration")
    evidence = {}
    for suite, per_cell in (
        ("commission-v1", 20),
        ("boundaries-v1", 8),
        ("pilot-v1", 5 * len(ARMS)),
    ):
        plan, rows, _ = load_campaign(root / suite)
        for key in (
            "environment_hash",
            "assets_hash",
            "code_hash",
            "requirements_hash",
        ):
            if plan["provenance"][key] != identity[key]:
                raise ValueError(f"{suite} {key} differs from the current candidate")
        for cell in ("cell_a", "cell_b"):
            block = [t for t in rows if t.row["cell"] == cell]
            if len(block) != per_cell or any(
                t.status != "COMPLETED"
                or t.eval.get("online_offline_equal") is not True
                for t in block
            ):
                raise ValueError(
                    f"Incomplete or non-replayable evidence: {suite}/{cell}"
                )
            if suite != "pilot-v1" and any(
                t.outcome != "SUCCESS" or t.eval["compliance_verdict"] != "PASS"
                for t in block
            ):
                raise ValueError(f"Healthy commissioning failed: {suite}/{cell}")
        evidence[suite] = {
            "plan_hash": plan["plan_hash"],
            "attempts": [
                {"attempt_id": t.attempt_id, "trace_hash": t.eval["source_digest"]}
                for t in rows
            ],
        }
    pilots = rows[:10]
    stored = sum(
        sum(f.stat().st_size for f in t.path.iterdir() if f.is_file()) for t in pilots
    )
    nsteps = sum(t.row["config"]["episode"]["horizon_steps"] for t in pilots)
    protocol = {
        "schema_version": "cellio.protocol/1.0",
        "support_profile": "P-ISAAC62-WIN-CONDA",
        "physics_hz": 120,
        "controller_divisor": 4,
        "horizon_steps": 7200,
        "warmup_steps": 240,
        "solver": "TGS",
        "device": "cpu",
        "main_seeds": list(range(1000, 1020)),
        "pilot_bytes_per_step": stored / nsteps,
        "pilot_attempt_ids": [t.attempt_id for t in pilots],
        "robot_profile_sha256": file_hash(calibration),
        "candidate": {k: v for k, v in identity.items() if k != "protocol_hash"},
        "suite_design_hashes": {
            s: digest(suite_design(s))
            for s in sorted(FINITE | {"oracle-v1", "soak-v1"})
        },
        "evidence": evidence,
    }
    dst = data_root() / "locks" / "protocol.lock.json"
    if dst.exists():
        previous = dst.with_name("protocol." + file_hash(dst)[:16] + ".json")
        if not previous.exists():
            previous.write_bytes(dst.read_bytes())
    write_json(dst, protocol)
    print(f"Protocol SHA-256 {digest(protocol)}")


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--campaign-root", type=Path, default=data_root() / "campaigns")
    args.add_argument(
        "--calibration",
        type=Path,
        default=data_root() / "locks" / "robot_profile.lock.json",
    )
    options = args.parse_args()
    freeze(options.campaign_root, options.calibration)
