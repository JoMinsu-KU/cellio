"""Deterministic campaign rows, append-only attempt accounting, and plan ownership."""

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import re

from cellio.config import resolve
from cellio.config.schema import CELLS, MODELS
from cellio.io import CellIOError, canonical, digest, write_json


ARMS = ("healthy", "f1_low", "f1_high", "f2", "f3", "f4", "f5", "f6")
MUTANT_ARMS = ("f5", "f6", "f1_high", "f4", "f2", "f3")
FINITE = {
    "canonical-v1",
    "reset-v1",
    "overhead-v1",
    "clean-install",
    "commission-v1",
    "boundaries-v1",
    "pilot-v1",
    "reuse-v1",
}
GENERATOR = {
    "warmup": 10,
    "minimum_completed": 200,
    "minimum_seconds": 7200,
    "limit_seconds": 86400,
}


def initial_state(cell, seed):
    key = hashlib.sha256(f"cellio/1.0/{cell}/{seed}/scene".encode()).digest()
    rng = random.Random(int.from_bytes(key[:16], "big"))
    bound_x, bound_y = (0.001, 0.004) if cell == "cell_a" else (0.002, 0.002)
    return {
        "x_offset_m": rng.uniform(-bound_x, bound_x),
        "y_offset_m": rng.uniform(-bound_y, bound_y),
        "yaw_rad": rng.uniform(-0.03, 0.03),
        "mass_kg": 0.05,
    }


def scenario(
    cell, arm="healthy", seed=1000, controller="reference", profile="isaac62_lockstep"
):
    if cell not in CELLS or arm not in ARMS:
        raise CellIOError("CFG_REFERENCE", f"Unknown cell/arm: {cell}/{arm}")
    a = cell == "cell_a"
    faults = []
    if arm != "healthy":
        family = arm[:2].upper()
        params = {}
        if family == "F1":
            target, stage = (
                ("belt.stopped", "wait_stop")
                if a
                else ("robot.clear_fixture", "fixture_retreat")
            )
            params = {"value": arm == "f1_high"}
        else:
            target, stage = (
                ("gripper.closed", "grasp") if a else ("clamp.closed", "clamp_close")
            )
            if family in ("F4", "F6"):
                target = "actuator.gripper" if a else "actuator.clamp"
            if family in ("F3", "F4"):
                params = {"delay_steps": 24}
        duration = {"F1": 480 if a else 1200, "F4": 120, "F6": 480 if a else 600}.get(
            family, 480
        )
        faults.append(
            dict(
                fault_id="fault_primary",
                family=family,
                model_id=MODELS[family],
                target_id=target,
                trigger={
                    "kind": "controller_event",
                    "event_type": "controller.stage_enter",
                    "event_filter": {"stage_id": stage},
                },
                arm={"open_step": 0, "close_step": 4800},
                offset_steps=0,
                duration_steps=duration,
                parameters=params,
                require_exercised=True,
            )
        )
    return resolve(
        {
            "schema_version": "cellio.config/1.0",
            "scenario_id": f"{cell}.{arm}",
            "profile": profile,
            "scene": {"cell_id": cell},
            "controller": {"id": controller},
            "seeds": {"scene": seed, "controller": 0, "fault": 0},
            "requirements": {"preset": cell},
            "faults": faults,
        }
    )


def make_row(
    suite,
    provenance,
    cell,
    arm,
    seed,
    repeat=0,
    controller="reference",
    *,
    chain=None,
    slot=None,
    mode=None,
    test=None,
    state=None,
    reload=False,
):
    cfg = scenario(cell, arm, seed, controller)
    if reload:
        cfg["episode"]["reset_mode"] = "hard_reload"
    if state is None:
        state = initial_state(cell, seed)
    row = dict(
        suite_id=suite,
        cell=cell,
        arm=arm,
        controller_id=controller,
        controller_revision=1,
        initial_state_id=seed,
        repeat_index=repeat,
        initial_state=state,
        config_hash=digest(cfg),
        chain_id=chain,
        chain_slot=slot,
        benchmark_mode=mode,
        test_control=test or {"kind": "full_h"},
        protocol_hash=provenance["protocol_hash"],
    )
    row["run_id"] = digest({"row": row, "provenance": provenance})[:24]
    return {**row, "config": cfg}


def _canonical_rows(suite, provenance):
    for seed in range(1000, 1020):
        for cell in CELLS:
            for arm in ARMS:
                for repeat in range(2):
                    yield make_row(suite, provenance, cell, arm, seed, repeat)
            if seed < 1005:
                for i, arm in enumerate(MUTANT_ARMS, 1):
                    controller = f"mutant.c_m{i}"
                    for repeat in range(2):
                        yield make_row(
                            suite, provenance, cell, arm, seed, repeat, controller
                        )
                    yield make_row(
                        suite, provenance, cell, "healthy", seed, controller=controller
                    )


def _reset_rows(suite, provenance):
    arms = ("f1_high", "f2", "f3", "f4", "f5", "f6")
    for cell in CELLS:
        for arm in arms:
            for order in "abcd":
                chain = f"{cell}.{arm}.{order}"
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    "healthy",
                    1000,
                    chain=chain,
                    slot="control",
                )
                test = {
                    "a": {"kind": "full_h"},
                    "b": {"kind": "fault_start_offset", "steps": 8},
                    "c": {"kind": "fault_end_offset", "steps": 12},
                    "d": {"kind": "fault_start_offset", "steps": 4},
                }[order]
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    "healthy" if order == "a" else arm,
                    1000,
                    chain=chain,
                    slot="predecessor",
                    test=test,
                    reload=True,
                )
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    "healthy",
                    1000,
                    chain=chain,
                    slot="post_reset",
                    reload=True,
                )


def finite_rows(suite, provenance):
    if suite == "canonical-v1":
        rows = list(_canonical_rows(suite, provenance))
    elif suite == "reset-v1":
        return list(_reset_rows(suite, provenance))
    elif suite == "overhead-v1":
        rows = [
            make_row(
                suite,
                provenance,
                c,
                "f4" if mode == "B2" else "healthy",
                s,
                r,
                mode=mode,
            )
            for c in CELLS
            for s in range(1000, 1010)
            for r in range(3)
            for mode in ("B0", "B1", "B2")
        ]
    elif suite in {"commission-v1", "clean-install"}:
        n = 20 if suite == "commission-v1" else 5
        fixed = dict(x_offset_m=0.0, y_offset_m=0.0, yaw_rad=0.0, mass_kg=0.05)
        return [
            make_row(suite, provenance, c, "healthy", 0, r, state=fixed)
            for c in CELLS
            for r in range(n)
        ]
    elif suite == "boundaries-v1":
        import itertools

        rows = []
        for c in CELLS:
            bx, by = (0.001, 0.004) if c == "cell_a" else (0.002, 0.002)
            for i, (x, y, yaw) in enumerate(
                itertools.product((-bx, bx), (-by, by), (-0.03, 0.03))
            ):
                rows.append(
                    make_row(
                        suite,
                        provenance,
                        c,
                        "healthy",
                        i,
                        state=dict(
                            x_offset_m=x, y_offset_m=y, yaw_rad=yaw, mass_kg=0.05
                        ),
                    )
                )
    elif suite == "pilot-v1":
        rows = [
            make_row(suite, provenance, c, a, s)
            for c in CELLS
            for s in range(5)
            for a in ARMS
        ]
    else:
        raise CellIOError("CFG_SUITE_UNKNOWN", f"No executable finite suite: {suite}")
    groups = defaultdict(list)
    for row in rows:
        groups[row["initial_state_id"]].append(row)
    rng = random.Random(20261118)
    result = []
    for group in groups.values():
        rng.shuffle(group)
        result.extend(group)
    return result


def soak_row(provenance, index):
    warm = index < GENERATOR["warmup"]
    n = index if warm else index - GENERATOR["warmup"]
    return make_row(
        "soak-v1",
        provenance,
        CELLS[n % 2],
        "healthy" if warm else ARMS[(n // 2) % 8],
        1000 if warm else 1000 + (n // 16) % 20,
        n // 2 if warm else n // 320,
        slot="warmup" if warm else "measurement",
        reload=True,
    )


def create_plan(suite, provenance, output):
    missing = {
        "code_hash",
        "assets_hash",
        "environment_hash",
        "protocol_hash",
    } - provenance.keys()
    if missing:
        raise CellIOError(
            "CFG_LOCK_MISSING",
            f"Missing lock identities: {sorted(missing)}",
            exit_code=3,
        )
    if any(
        not isinstance(h, str)
        or len(h) != 64
        or any(c not in "0123456789abcdef" for c in h)
        for k, h in provenance.items()
        if k.endswith("_hash")
    ):
        raise CellIOError(
            "CFG_LOCK_MISSING", "Expected full SHA-256 identities", exit_code=3
        )
    plan = {
        "schema_version": "cellio.plan/1.0",
        "suite_id": suite,
        "provenance": provenance,
        "protocol_hash": provenance["protocol_hash"],
        "plan_mode": "finite",
    }
    if suite == "oracle-v1":
        from cellio.acceptance import checkout

        corpus = json.loads(
            (checkout() / "fixtures/oracle/gold.json").read_text(encoding="utf-8")
        )
        plan["runs"] = [
            {
                "case_id": item["id"],
                "case_hash": digest(item),
                "run_id": digest([suite, provenance, digest(item)])[:24],
            }
            for item in corpus
        ]
    elif suite == "soak-v1":
        plan.update(plan_mode="soak_generator", generator=GENERATOR, runs=[])
    else:
        plan["runs"] = finite_rows(suite, provenance)
    if len({r["run_id"] for r in plan["runs"]}) != len(plan["runs"]):
        raise CellIOError("RUN_PLAN_DUPLICATE", "Duplicate run identity")
    plan["plan_hash"] = digest(plan)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output):
        dest = output / "campaign.plan.json"
        if dest.exists() and read_plan(dest)["plan_hash"] != plan["plan_hash"]:
            raise CellIOError(
                "RUN_PLAN_EXISTS", "A different frozen plan occupies this directory"
            )
        write_json(dest, plan)
    return plan


def read_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("plan_hash") != digest(
        {k: v for k, v in plan.items() if k != "plan_hash"}
    ):
        raise CellIOError("RUN_PLAN_HASH", "Plan hash mismatch", exit_code=4)
    if (
        plan.get("schema_version") != "cellio.plan/1.0"
        or plan.get("plan_mode") not in ("finite", "soak_generator")
        or not isinstance(plan.get("runs"), list)
    ):
        raise CellIOError("CFG_SCHEMA", "Invalid campaign plan header")
    ids = set()
    for row in plan["runs"]:
        run_id = row.get("run_id")
        if (
            not isinstance(run_id, str)
            or not re.fullmatch("[0-9a-f]{24}", run_id)
            or run_id in ids
        ):
            raise CellIOError(
                "RUN_ID_INVALID", "Run IDs must be unique 24-digit hexadecimal strings"
            )
        ids.add(run_id)
        if plan["suite_id"] == "oracle-v1":
            expected = digest(["oracle-v1", plan["provenance"], row["case_hash"]])[:24]
        else:
            semantic = {k: v for k, v in row.items() if k not in ("run_id", "config")}
            expected = digest({"row": semantic, "provenance": plan["provenance"]})[:24]
            if (
                digest(row["config"]) != row.get("config_hash")
                or row["suite_id"] != plan["suite_id"]
            ):
                raise CellIOError(
                    "RUN_PLAN_HASH",
                    f"Config or suite mismatch in {run_id}",
                    exit_code=4,
                )
        if run_id != expected:
            raise CellIOError(
                "RUN_PLAN_HASH", f"Run identity mismatch: {run_id}", exit_code=4
            )
    return plan


def append_line(path, value):
    with Path(path).open("ab") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def exclusive(directory):
    file = (Path(directory) / ".campaign.lock").open("a+b")
    is_windows = os.name == "nt"
    try:
        if is_windows:
            import msvcrt

            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        file.close()
        raise CellIOError(
            "RUN_PLAN_LOCKED", "Another supervisor owns this plan", exit_code=4
        ) from exc
    try:
        yield
    finally:
        file.close()
