from dataclasses import replace
from fractions import Fraction
from math import cos, pi, sin

import pytest

from cellio.core.clock import Phase, PhaseGuard, StepClock, steps_from_seconds
from cellio.core.commands import CommandRouter
from cellio.core.events import EventBus
from cellio.core.faults import FaultEngine
from cellio.core.models import (
    ActuationReceipt,
    AdvanceReceipt,
    AppliedCommand,
    BodyState,
    CellIOError,
    CommandIntent,
    CommandKind,
    Quality,
    SignalSample,
    Tick,
    TruthSnapshot,
    json_value,
)
from cellio.core.signals import (
    NominalSampler,
    SignalBinding,
    WorldPredicates,
    canonical_bindings,
    for_controller,
    obb_intersects_aabb,
    usable,
    view_hash,
)
from cellio.io import digest


def tick(k, generation=0):
    return Tick("episode", generation, k)


def truth(k, plant=None, bodies=None):
    return TruthSnapshot(tick(k), bodies or {}, {}, (), plant or {}, True)


def receipt(k, rendered=False):
    return AdvanceReceipt(k, k + 1, 60 + k / 120, 60 + (k + 1) / 120, True, rendered)


def command(k, seq, value=0.02):
    return CommandIntent(
        f"attempt:cmd:{seq}",
        seq,
        "episode",
        0,
        0,
        k,
        "actuator.gripper",
        CommandKind.GRIPPER_GAP,
        value,
        0,
    )


def test_T_CLK_01_02_exact_time_and_control():
    clock = StepClock("episode", 0, 120)
    control = []
    for k in range(120):
        assert clock.at(k).step == k
        if clock.control_due:
            control.append(k)
        clock.commit(receipt(k))
    assert clock.step * clock.dt == Fraction(1) and control == list(range(0, 120, 4))
    assert steps_from_seconds("0.025") == 3 and steps_from_seconds("0.02500001") == 4


def test_T_CLK_03_controller_and_world_event_availability():
    bus = EventBus()
    events = bus.commit_controller(
        tick(0), [{"event_type": "controller.stage_enter", "payload": {"stage_id": "grasp"}}]
    )
    assert bus.pop_ready_for_fault_trigger(0) == () and events[0].step == 0
    assert not bus.world_edges(tick(0), {"world.part_dropped": False})
    world = bus.world_edges(tick(1), {"world.part_dropped": True})
    pending = bus.pop_ready_for_fault_trigger(1)
    merged = bus.merge_unique(world, pending)
    assert [e.step for e in merged] == [1, 0] and all(e.available_step == 1 for e in merged)
    assert not bus.pop_ready_for_fault_trigger(1)
    with pytest.raises(CellIOError, match="EVENT_DUPLICATE"):
        bus.merge_unique(world, world)


def test_T_CLK_04_05_final_disallows_more_advance():
    clock = StepClock("episode", 0, 1)
    clock.commit(receipt(0))
    assert not clock.control_due
    with pytest.raises(CellIOError, match="SIM_ADVANCE_FAILED"):
        clock.commit(receipt(1))
    with pytest.raises(CellIOError, match="SIM_ADVANCE_FAILED"):
        StepClock("episode", 0, 2).commit(replace(receipt(0), success=False))
    with pytest.raises(CellIOError, match="SIM_ADVANCE_FAILED"):
        StepClock("episode", 0, 2).commit(receipt(1))


@pytest.mark.parametrize("render", [False, True])
def test_T_CLK_06_identical_phases_with_render(render):
    guard, clock = PhaseGuard(), StepClock("episode", 0, 5)
    for k in range(5):
        for phase in Phase:
            guard.enter(k, phase)
        clock.commit(receipt(k, render))
    assert clock.step == 5
    with pytest.raises(CellIOError, match="CLOCK_PHASE"):
        guard.enter(4, Phase.ADVANCE)
    clock = StepClock("episode", 0, 5)
    with pytest.raises(CellIOError, match="CLOCK_DRIFT"):
        clock.commit(replace(receipt(0), engine_time_after_s=61))
    assert clock.step == 0


def base_fault(trigger, arm, offset):
    return dict(
        fault_id="f",
        family="F1",
        model_id="boolean_stuck_at_v1",
        target_id="belt.stopped",
        trigger=trigger,
        arm=arm,
        offset_steps=offset,
        duration_steps=2,
        parameters={"value": False},
    )


def test_matched_controller_event_latches_after_arm_closes():
    bus = EventBus()
    spec = base_fault(
        {
            "kind": "controller_event",
            "event_type": "controller.stage_enter",
            "event_filter": {"stage_id": "grasp"},
        },
        {"open_step": 1, "close_step": 2},
        2,
    )
    engine = FaultEngine(spec, 6)
    for k in range(6):
        engine.advance(tick(k), bus.pop_ready_for_fault_trigger(k))
        if k == 0:
            bus.commit_controller(
                tick(k),
                [{"event_type": "controller.stage_enter", "payload": {"stage_id": "grasp"}}],
            )
        assert engine.active == (3 <= k < 5)
    assert engine.state.trigger_match_step == 1


@pytest.mark.parametrize("initial,start", [(True, 0), (False, 3)])
def test_world_rising_edge(initial, start):
    spec = base_fault(
        {"kind": "world_rising", "predicate_id": "world.part_dropped", "fire_on_initial": initial},
        {"open_step": 0, "close_step": 5},
        0,
    )
    engine = FaultEngine(spec, 6)
    for k, value in enumerate([True, True, False, True, True, True]):
        engine.advance(tick(k), predicates={"world.part_dropped": value})
    assert engine.state.actual_start == start


def test_scheduled_after_horizon_is_censored():
    spec = base_fault({"kind": "at_step", "at_step": 2}, {"open_step": 0, "close_step": 4}, 2)
    engine = FaultEngine(spec, 4)
    for k in range(4):
        engine.advance(tick(k))
    r = engine.finalize(4)
    assert (r["application_status"], r["coverage"], r["reason"]) == (
        "SCHEDULED",
        "CENSORED",
        "START_CENSORED",
    )


def packet(k=0):
    return SignalSample("example", "bool", False, Quality.GOOD, k, k, k, None, None, 0, None)


def test_T_SIG_01_typed_scalar_and_null():
    with pytest.raises(CellIOError, match="SIGNAL_DTYPE"):
        replace(packet(), dtype="int")
    with pytest.raises(CellIOError, match="SIGNAL_DTYPE"):
        replace(packet(), dtype="float", value=float("nan"))
    assert SignalSample.missing("example", "bool", 0).quality == Quality.MISSING


def test_T_SIG_02_03_source_age_and_cached_identity():
    p = replace(packet(3), deliver_step=10, transport_seq=0)
    assert usable(p, 15) and not usable(p, 16)
    a, b = for_controller({"example": p}, tick(12)), for_controller({"example": p}, tick(16))
    assert a.signals["example"] == b.signals["example"] and a.signals["example"].deliver_step == 10


def test_T_SIG_04_deep_snapshot_immutability():
    s = {"int": 1}
    t = truth(0, s)
    s["int"] = 8
    assert t.plant_state["int"] == 1
    with pytest.raises(TypeError):
        t.plant_state["int"] = 4
    center = [1.0, 2.0, 3.0]
    b = BodyState("part", "world", center, [1, 0, 0, 0], [0, 0, 0], [0, 0, 0], True)
    center[0] = 9
    assert b.position_m == (1.0, 2.0, 3.0)
    assert BodyState("part", "world", None, None, None, None, False).position_m is None


def test_T_SIG_05_obb_sat_separates_rotated_slender_body_from_aabb_corner():
    q = (cos(pi / 8), 0.0, 0.0, sin(pi / 8))
    assert obb_intersects_aabb((0, 0, 0), q, (1.0, 0.1, 0.1), (0.3, 0.3, 0), (0.02, 0.02, 0.02))
    assert not obb_intersects_aabb(
        (0, 0, 0), q, (1.0, 0.1, 0.1), (0.3, -0.3, 0), (0.02, 0.02, 0.02)
    )


def test_T_SIG_06_dwell_uses_boundaries_and_resets():
    b = SignalBinding("a", "bool", lambda t: t.plant_state["raw"], 2)
    sampler = NominalSampler([b])
    assert [
        sampler.sample(truth(k, {"raw": v}), tick(k))["a"].value
        for k, v in enumerate([True, True, True, False, True, True, True])
    ] == [False, False, True, False, False, False, True]
    sampler.reset(1)
    t = TruthSnapshot(tick(0, 1), {}, {}, (), {"raw": True}, True)
    s = sampler.sample(t, t.tick)["a"]
    assert (s.value, s.source_seq, s.generation) == (False, 0, 1)
    with pytest.raises(CellIOError, match="CLOCK_ORDER"):
        sampler.sample(t, t.tick)


def test_T_SIG_07_hash_represents_exact_visible_content():
    p = replace(packet(), deliver_step=0, transport_seq=0)
    view = for_controller({"example": p}, tick(4))
    assert view_hash(view) == digest(json_value(view))
    assert view_hash(view) != view_hash(
        for_controller({"example": replace(p, value=True)}, tick(4))
    )
    assert not {"truth", "faults", "raw_signals"}.intersection(json_value(view))


def test_world_predicates_use_physical_coordinates_and_dwell():
    p = BodyState(
        "part_000", "world", (0.507, 0.12, 0.810), (1, 0, 0, 0), (0, 0, 0), (0, 0, 0), True
    )
    world = WorldPredicates("cell_b")
    state = {"gripper.gap_m": 0.04, "gripper.max_speed_mps": 0.0}
    readings = [world.sample(truth(k, state, {"part_000": p}), tick(k)) for k in range(13)]
    assert readings[-1]["world.fixture_loaded"] and readings[-1]["world.gripper_closed"]
    assert not readings[-2]["world.gripper_closed"] and readings[-1]["world.clamp_closed"] is None
    p = replace(p, position_m=(0.50, 0.12, 0.650))
    reading = world.sample(truth(13, state, {"part_000": p}), tick(13))
    assert reading["world.part_dropped"] and not reading["world.fixture_loaded"]
    assert len(canonical_bindings("cell_a")) == 9 and len(canonical_bindings("cell_b")) == 13


def test_T_CMD_01_duplicate_conflict_and_out_of_order():
    router = CommandRouter("cell_a", "attempt", "episode", 0)
    faults = FaultEngine(None)
    cmd = command(0, 0)
    assert router.resolve([cmd], faults, tick(0))[0].status == "DELIVERED"
    assert router.resolve([cmd], faults, tick(1))[0].status == "DUPLICATE"
    with pytest.raises(CellIOError, match="CTRL_COMMAND_CONFLICT"):
        router.resolve([replace(cmd, value=0.03)], faults, tick(2))


def test_T_CMD_02_stale_generation_and_epoch_never_applied():
    router = CommandRouter("cell_a", "attempt", "episode", 0)
    faults = FaultEngine(None)
    stale = replace(command(0, 0), generation=1)
    assert router.resolve([stale], faults, tick(0))[0].status == "STALE"
    stale = replace(command(1, 0), operation_epoch=1)
    assert router.resolve([stale], faults, tick(1))[0].status == "STALE"
    assert not router.effective


def test_T_CMD_03_superseded_due_and_immediate():
    spec = dict(
        fault_id="f4",
        family="F4",
        model_id="fixed_delay_flush_resume_v1",
        target_id="actuator.gripper",
        trigger={"kind": "at_step", "at_step": 0},
        arm={"open_step": 0, "close_step": 5},
        offset_steps=0,
        duration_steps=4,
        parameters={"delay_steps": 2},
    )
    faults, router = FaultEngine(spec, 5), CommandRouter("cell_a", "attempt", "episode", 0)
    for k in range(3):
        faults.advance(tick(k))
        pending = (
            (command(0, 0),)
            if k == 0
            else (replace(command(2, 1), kind=CommandKind.HOLD, value=()),) if k == 2 else ()
        )
        ds = router.resolve(pending, faults, tick(k))
    assert [(d.intent.sequence, d.status) for d in ds] == [(0, "SUPERSEDED"), (1, "DELIVERED")]
    assert router.effective["actuator.gripper"].kind == CommandKind.HOLD


def test_T_CMD_04_05_cancel_precedes_new_work_and_is_idempotent():
    router = CommandRouter("cell_a", "attempt", "episode", 0)
    f = FaultEngine(None)
    cancel = replace(
        command(0, 0),
        operation_epoch=1,
        kind=CommandKind.CANCEL_OPERATION,
        actuator_id="control.operation",
        value=0,
    )
    with pytest.raises(CellIOError, match="CTRL_OPERATION_EPOCH"):
        router.resolve([cancel, replace(command(0, 1), operation_epoch=1)], f, tick(0))
    assert router.epoch == 0 and not router.effective
    hold = replace(command(0, 1), kind=CommandKind.HOLD, value=(), operation_epoch=1)
    assert [d.intent for d in router.resolve([cancel, hold], f, tick(0))] == [cancel, hold]
    assert router.resolve([cancel], f, tick(1))[0].status == "DUPLICATE" and router.epoch == 1


@pytest.mark.parametrize(
    "cmd",
    [
        replace(command(0, 0), value=0.081),
        replace(command(0, 0), value=True),
        replace(
            command(0, 0),
            kind=CommandKind.POSE,
            actuator_id="actuator.robot",
            value=(0.4, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0),
        ),
        replace(
            command(0, 0),
            kind=CommandKind.POSE,
            actuator_id="actuator.robot",
            value=(0.4, 0.1, 1.0, 0.0, 0.0, 0.0, 0.0),
        ),
        replace(command(0, 0), kind=CommandKind.BOOL, actuator_id="actuator.belt", value=1),
    ],
)
def test_T_CMD_06_type_and_range(cmd):
    with pytest.raises(CellIOError, match="CTRL_COMMAND_RANGE"):
        CommandRouter("cell_a", "attempt", "episode", 0).resolve([cmd], FaultEngine(None), tick(0))


def test_T_CMD_07_08_backend_failure_and_public_receipts():
    router = CommandRouter("cell_a", "attempt", "episode", 0)
    i = command(0, 0)
    router.resolve([i], FaultEngine(None), tick(0))
    rejected = AppliedCommand(
        i.command_id, i.actuator_id, 0, 0, 0, None, "BACKEND_ERROR", "private", None
    )
    with pytest.raises(CellIOError, match="SIM_ACTUATION_FAILED"):
        router.acknowledge(ActuationReceipt(tick(0), False, (rejected,), {}, ()))
    assert router.public_receipts(tick(0)) == ()
    accepted, error = router.public_receipts(tick(4))
    assert (accepted.status, error.status, error.reason) == ("ACCEPTED", "REJECTED", "UNAVAILABLE")
    router = CommandRouter("cell_a", "attempt", "episode", 0)
    router.resolve([i], FaultEngine(None), tick(0))
    applied = replace(rejected, applied_step=0, status="APPLIED", reason="RESTORE_RESUME")
    router.acknowledge(ActuationReceipt(tick(0), True, (applied,), {i.actuator_id: 0.02}, ()))
    public = router.public_receipts(tick(4))
    assert public[-1].status == "APPLIED" and public[-1].reason is None
    assert set(json_value(public[-1])) == {
        "command_id",
        "operation_epoch",
        "actuator_id",
        "status",
        "receipt_step",
        "reason",
    }
