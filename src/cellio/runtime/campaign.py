"""Deterministic campaign rows, append-only attempt accounting, and plan ownership."""

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sysconfig

from cellio.config import read_document, resolve
from cellio.config.schema import MODELS
from cellio.plugins import REFERENCE_SCENES, scene_plugin
from cellio.io import CellIOError, canonical, digest, file_hash, write_json


ARMS = ("healthy", "f1_low", "f1_high", "f2", "f3", "f4", "f5", "f6")
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


def suite_design(suite):
    if suite not in FINITE | {"soak-v1", "oracle-v1"}:
        raise CellIOError(
            "CFG_SUITE_UNKNOWN",
            f"Unknown suite {suite!r}. Available: {', '.join(sorted(FINITE | {'soak-v1', 'oracle-v1'}))}",
        )
    checkout = Path(__file__).resolve().parents[3]
    folder = (
        checkout / "protocols"
        if (checkout / "pyproject.toml").is_file()
        else Path(sysconfig.get_path("data")) / "share/cellio/protocols"
    )
    design = read_document(folder / f"{suite}.yaml")
    if (
        design.get("suite_id") != suite
        or design.get("schema_version") != "cellio.suite/1.0"
    ):
        raise CellIOError(
            "CFG_SUITE_DESIGN", f"Invalid protocol descriptor for {suite}"
        )
    return design


def initial_state(cell, seed):
    if cell not in REFERENCE_SCENES:
        return scene_plugin(cell).initial_state_sampler(seed)
    from cellio.scenes import get_scene

    scene = get_scene(cell)
    key = hashlib.sha256(f"cellio/1.0/{cell}/{seed}/scene".encode()).digest()
    rng = random.Random(int.from_bytes(key[:16], "big"))
    bound_x, bound_y = (0.001, 0.004) if cell == "cell_a" else (0.002, 0.002)
    proposals = []
    for _ in range(20):
        state = {
            "x_offset_m": rng.uniform(-bound_x, bound_x),
            "y_offset_m": rng.uniform(-bound_y, bound_y),
            "yaw_rad": rng.uniform(-0.03, 0.03),
            "mass_kg": 0.05,
        }
        geometry = scene.placement_geometry(state)
        proposals.append({"state": state, "geometry": geometry})
        if geometry["accepted"]:
            return {**state, "placement": {"mode": "sampled", "proposals": proposals}}
    raise CellIOError(
        "INIT_SAMPLING_FAILED",
        f"{cell} seed {seed} exhausted 20 geometry proposals: {json.dumps(proposals)}",
    )


def scenario(
    cell,
    arm="healthy",
    seed=1000,
    controller="reference",
    profile="isaac62_lockstep",
    *,
    variant="canonical",
):
    if cell not in REFERENCE_SCENES:
        if variant != "canonical":
            raise CellIOError("CFG_REFERENCE", f"Unsupported scene variant: {variant}")
        return scene_plugin(cell).scenario_presets(arm, seed, controller, profile)
    if arm not in ARMS:
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
            "scene": {"cell_id": cell, "variant_id": variant},
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
    variant="canonical",
):
    cfg = scenario(cell, arm, seed, controller, variant=variant)
    if reload:
        cfg["episode"]["reset_mode"] = "hard_reload"
    if state is None:
        state = initial_state(cell, seed)
    else:
        from cellio.scenes import get_scene

        geometry = get_scene(cell).placement_geometry(state)
        if not geometry["accepted"]:
            raise CellIOError(
                "INIT_SAMPLING_FAILED", f"Frozen {cell} placement rejected: {geometry}"
            )
        if "placement" not in state:
            state = {**state, "placement": {"mode": "fixed", "geometry": geometry}}
        else:
            stored = state["placement"]
            if stored.get("mode") == "sampled":
                accepted = stored["proposals"][-1]
                expected = {k: v for k, v in state.items() if k != "placement"}
                consistent = (
                    accepted["state"] == expected and accepted["geometry"] == geometry
                )
            else:
                consistent = stored == {"mode": "fixed", "geometry": geometry}
            if not consistent:
                raise CellIOError(
                    "INIT_SAMPLING_FAILED",
                    "Frozen placement differs from its recorded geometry",
                )
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


def _canonical_rows(suite, provenance, design):
    reference, mutants = design["reference"], design["mutants"]
    for seed in range(reference["first_state"], reference["last_state"] + 1):
        for cell in design["cells"]:
            for arm in reference["arms"]:
                for repeat in range(reference["repeats"]):
                    yield make_row(suite, provenance, cell, arm, seed, repeat)
            if mutants["first_state"] <= seed <= mutants["last_state"]:
                for controller, arm in zip(
                    mutants["ids"], mutants["matched_arms"], strict=True
                ):
                    for repeat in range(mutants["fault_repeats"]):
                        yield make_row(
                            suite, provenance, cell, arm, seed, repeat, controller
                        )
                    for repeat in range(mutants["healthy_repeats"]):
                        yield make_row(
                            suite, provenance, cell, "healthy", seed, repeat, controller
                        )


def _reset_rows(suite, provenance, design):
    control, predecessor, post = design["slots"]
    for cell in design["cells"]:
        for arm in design["arms"]:
            for order, condition in design["conditions"].items():
                chain = f"{cell}.{arm}.{order}"
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    "healthy",
                    design["state"],
                    chain=chain,
                    slot=control,
                )
                test = {k: v for k, v in condition.items() if k != "predecessor_arm"}
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    condition.get("predecessor_arm", arm),
                    design["state"],
                    chain=chain,
                    slot=predecessor,
                    test=test,
                    reload=True,
                )
                yield make_row(
                    suite,
                    provenance,
                    cell,
                    "healthy",
                    design["state"],
                    chain=chain,
                    slot=post,
                    reload=True,
                )


def finite_rows(suite, provenance, design=None):
    if design is None:
        design = suite_design(suite)
    if suite == "canonical-v1":
        rows = list(_canonical_rows(suite, provenance, design))
    elif suite == "reset-v1":
        return list(_reset_rows(suite, provenance, design))
    elif suite == "overhead-v1":
        rows = [
            make_row(
                suite,
                provenance,
                c,
                arm,
                s,
                r,
                mode=mode,
            )
            for c in design["cells"]
            for s in range(design["first_state"], design["last_state"] + 1)
            for r in range(design["repeats"])
            for mode, arm in design["mode_arms"].items()
        ]
    elif suite in {"commission-v1", "clean-install"}:
        return [
            make_row(
                suite, provenance, c, design["arm"], 0, r, state=design["initial_state"]
            )
            for c in design["cells"]
            for r in range(design["consecutive_runs_per_cell"])
        ]
    elif suite == "boundaries-v1":
        import itertools

        rows = []
        for c in design["cells"]:
            for i, (x, y, yaw) in enumerate(
                itertools.product(
                    design["x_bounds_m"][c],
                    design["y_bounds_m"][c],
                    design["yaw_bounds_rad"],
                )
            ):
                rows.append(
                    make_row(
                        suite,
                        provenance,
                        c,
                        design["arm"],
                        i,
                        state=dict(
                            x_offset_m=x, y_offset_m=y, yaw_rad=yaw, mass_kg=0.05
                        ),
                    )
                )
    elif suite in {"pilot-v1", "reuse-v1"}:
        rows = [
            make_row(
                suite,
                provenance,
                c,
                a,
                s,
                variant=design.get("variant_id", "canonical"),
            )
            for c in (design["cells"] if suite == "pilot-v1" else [design["cell"]])
            for s in range(design["first_state"], design["last_state"] + 1)
            for a in design["arms"]
        ]
    else:
        raise CellIOError("CFG_SUITE_UNKNOWN", f"No executable finite suite: {suite}")
    groups = defaultdict(list)
    for row in rows:
        groups[row["initial_state_id"]].append(row)
    rng = random.Random(design.get("state_block_shuffle_seed", 20261118))
    result = []
    for group in groups.values():
        rng.shuffle(group)
        result.extend(group)
    return result


def soak_row(provenance, index, design=None):
    design = design or suite_design("soak-v1")
    warmup = design["generator"]["warmup"]
    cells, arms = design["cells"], design["arms"]
    states = design["state_count"]
    round_length = len(cells) * len(arms)
    warm = index < warmup
    n = index if warm else index - warmup
    return make_row(
        "soak-v1",
        provenance,
        cells[n % len(cells)],
        "healthy" if warm else arms[(n // len(cells)) % len(arms)],
        design["first_state"] + (0 if warm else (n // round_length) % states),
        n // len(cells) if warm else n // (states * round_length),
        slot="warmup" if warm else "measurement",
        reload=True,
    )


def create_plan(suite, provenance, output):
    design = suite_design(suite)
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
        "suite_design": design,
    }
    if suite == "oracle-v1":
        from cellio.acceptance import gold_directory

        source = gold_directory() / "gold.json"
        if file_hash(source) != design["corpus_sha256"]:
            raise CellIOError(
                "CFG_SUITE_DESIGN", "Oracle fixture digest differs from the protocol"
            )
        corpus = json.loads(source.read_text(encoding="utf-8"))
        plan["runs"] = [
            {
                "case_id": item["id"],
                "case_hash": digest(item),
                "run_id": digest([suite, provenance, digest(item)])[:24],
            }
            for item in corpus
        ]
    elif suite == "soak-v1":
        plan.update(plan_mode="soak_generator", generator=design["generator"], runs=[])
    else:
        plan["runs"] = finite_rows(suite, provenance, design)
    if plan["plan_mode"] != design["mode"] or (
        design["mode"] == "finite" and len(plan["runs"]) != design["planned_runs"]
    ):
        raise CellIOError(
            "CFG_SUITE_DESIGN", f"Generated plan differs from its design for {suite}"
        )
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
        if suite == "boundaries-v1":
            write_json(
                output / "initial_boundaries.json",
                {
                    "plan_hash": plan["plan_hash"],
                    "cases": [
                        {
                            k: row[k]
                            for k in (
                                "cell",
                                "run_id",
                                "initial_state_id",
                                "initial_state",
                            )
                        }
                        for row in plan["runs"]
                    ],
                },
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
