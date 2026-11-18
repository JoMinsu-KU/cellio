"""Named operator contracts. Physical readbacks here are independent, hand-authored fixtures."""

from dataclasses import replace

import pytest

from cellio.core.commands import CommandRouter
from cellio.core.faults import MODEL_IDS, FaultEngine, ObservationDelayQueue, ScheduledObservation
from cellio.core.models import (
    ActuationReceipt,
    CellIOError,
    CommandIntent,
    CommandKind,
    Quality,
    SignalSample,
    Tick,
    TickEvidenceFrame,
    TruthSnapshot,
    Verdict,
)


FAMILIES = tuple(MODEL_IDS)
SIGNAL = "gripper.closed"
OTHER = "belt.stopped"
GRIPPER = "actuator.gripper"


def plan(family, start=2, duration=4, delay=1, value=False, horizon=240, target=None):
    parameters = {
        "F1": {"value": value},
        "F2": {},
        "F3": {"delay_steps": delay},
        "F4": {"delay_steps": delay},
        "F5": {},
        "F6": {"hold_tolerance_m": 0.0005, "conflicting_travel_min_m": 0.010},
    }
    return {
        "family": family,
        "model_id": MODEL_IDS[family],
        "fault_id": "primary",
        "target_id": target or (GRIPPER if family in ("F4", "F6") else SIGNAL),
        "duration_steps": duration,
        "offset_steps": 0,
        "arm": {"open_step": 0, "close_step": horizon},
        "trigger": {"kind": "at_step", "at_step": start},
        "parameters": parameters[family],
    }


def sample(signal, k, value=True, generation=0):
    return SignalSample(signal, "bool", value, Quality.GOOD, k, k, k, None, None, generation, None)


def intent(
    k,
    sequence,
    value=0.00,
    generation=0,
    epoch=0,
    kind=CommandKind.GRIPPER_GAP,
    endpoint=GRIPPER,
    episode="episode",
):
    return CommandIntent(
        f"attempt:cmd:{sequence}", sequence, episode, generation, epoch, k, endpoint, kind, value, 0
    )


class Fixture:
    def __init__(self, spec, horizon=240, generation=0, cell="cell_a", queue_limit=256):
        self.fault = FaultEngine(spec, horizon, cell)
        self.fault.reset("episode", generation)
        self.router = CommandRouter(cell, "attempt", "episode", generation, queue_limit=queue_limit)
        self.generation = generation
        self.cell = cell

    def tick(
        self,
        k,
        *,
        commands=(),
        raw=None,
        position=0.04,
        coordinate=0.08,
        requested=0.08,
        events=(),
        predicates=None,
        committed=True,
        nominal_target=0.02,
    ):
        tick = Tick("episode", self.generation, k)
        driver = "finger_driver" if self.cell == "cell_a" else "clamp_driver"
        actual = {driver: position}
        transitions = self.fault.advance(tick, events, predicates, actual, coordinate)
        raw = (
            {
                SIGNAL: sample(SIGNAL, k, True, self.generation),
                OTHER: sample(OTHER, k, True, self.generation),
            }
            if raw is None
            else raw
        )
        assigned = self.fault.transform_observations(raw, tick)
        before = {key: i.command_id for key, i in self.router.effective.items()}
        deliveries = self.router.resolve(commands, self.fault, tick)
        after = {key: i.command_id for key, i in self.router.effective.items()}
        overrides = self.fault.actuator_overrides()
        target = GRIPPER if self.cell == "cell_a" else "actuator.clamp"
        final = (
            dict(overrides[target]["driver_positions"]) if overrides else {driver: nominal_target}
        )
        receipt = ActuationReceipt(
            tick, committed, (), {target: tuple(final.values())}, ("primary",) if overrides else ()
        )
        truth = TruthSnapshot(tick, {}, {}, (), {}, True)
        frame = TickEvidenceFrame(
            tick,
            truth,
            raw,
            assigned,
            k % 4 == 0,
            None,
            None,
            commands,
            deliveries,
            receipt,
            events,
            transitions,
            {},
            None,
            False,
        )
        self.fault.observe(
            frame,
            effective_before=before,
            effective_after=after,
            actual_positions=actual,
            nominal_coordinate=requested,
            nominal_targets={driver: nominal_target},
            applied_targets=final,
        )
        return frame


def cases(suffix):
    values = []
    for f in FAMILIES:
        variants = (False, True) if f == "F1" else (1, 24, 120) if f in ("F3", "F4") else (None,)
        values.extend(pytest.param(f, v, id=f"T-{f}-{suffix:02}-{v}") for v in variants)
    return values


def build(f, v, start=2, duration=4, horizon=240):
    return Fixture(
        plan(
            f,
            start,
            duration,
            delay=v if f in ("F3", "F4") else 1,
            value=v if f == "F1" else False,
            horizon=horizon,
        ),
        horizon,
    )


@pytest.mark.parametrize("start", [0, 2])
@pytest.mark.parametrize("f,v", cases(1))
def test_start_boundary(f, v, start):
    fx = build(f, v, start, 4)
    for k in range(start + 1):
        raw = {SIGNAL: sample(SIGNAL, k, not v), OTHER: sample(OTHER, k)} if f == "F1" else None
        frame = fx.tick(k, raw=raw, commands=(intent(k, 0),) if f == "F4" and k == start else ())
        assert fx.fault.active == (k == start)
        if k < start:
            assert frame.observed_signals[SIGNAL].source_step == k
            continue
        assert fx.fault.state.actual_start == start
        observed = frame.observed_signals[SIGNAL]
        if f == "F1":
            assert observed.value is v and frame.raw_signals[SIGNAL].value is not v
            assert fx.fault.state.effect_status == "REALIZED"
        elif f == "F2":
            assert observed.value is True and observed.source_step == start
        elif f == "F3":
            assert observed.source_step == (None if start == 0 else start - 1)
        elif f == "F4":
            assert [d.status for d in frame.deliveries] == [
                "QUEUED"
            ] and GRIPPER not in fx.router.effective
        elif f == "F5":
            assert (
                observed.quality == Quality.BAD
                and observed.value is None
                and observed.source_seq is None
            )
        else:
            assert fx.fault.actuator_overrides()[GRIPPER]["driver_positions"] == {
                "finger_driver": 0.04
            }


@pytest.mark.parametrize("f,v", cases(2))
def test_end_exclusive(f, v):
    fx = build(f, v)
    frames = []
    for k in range(7):
        frames.append(fx.tick(k, commands=(intent(k, 0),) if f == "F4" and k == 5 else ()))
        assert fx.fault.active == (2 <= k < 6)
    assert frames[6].observed_signals[SIGNAL].source_step == 6
    assert frames[6].observed_signals[SIGNAL].quality == Quality.GOOD
    assert frames[6].actuation.active_override_ids == ()
    assert fx.fault.state.actual_end == 6
    assert fx.fault.state.restoration_status == "PATH_RESTORED"
    if f == "F4":
        assert [
            (d.intent.sequence, d.delivery_step)
            for d in frames[6].deliveries
            if d.status == "DELIVERED"
        ] == [(0, 6)]
    if f == "F2":
        assert frames[5].observed_signals[SIGNAL].source_step == 2


@pytest.mark.parametrize("f,v", cases(3))
def test_single_tick(f, v):
    fx = build(f, v, start=1, duration=1)
    raw_value = v if f == "F1" else True
    for k in range(3):
        frame = fx.tick(k, raw={SIGNAL: sample(SIGNAL, k, raw_value)}, nominal_target=0.04)
        assert fx.fault.active == (k == 1)
    state = fx.fault.finalize(3)
    if f in ("F1", "F2", "F4", "F6"):
        assert state["effect_status"] == "NOT_REALIZED"
    else:
        assert state["effect_status"] == "REALIZED" and state["controller_exposure_count"] == 0
    assert frame.observed_signals[SIGNAL].deliver_step == 2


@pytest.mark.parametrize("f,v", cases(4))
def test_untargeted_channel_identical(f, v):
    fx = build(f, v)
    clean = Fixture(None)
    for k in range(8):
        args = (
            {"commands": (intent(k, 0, True, kind=CommandKind.BOOL, endpoint="actuator.belt"),)}
            if k == 3
            else {}
        )
        frame, expected = fx.tick(k, **args), clean.tick(k, **args)
        assert frame.observed_signals[OTHER] == expected.observed_signals[OTHER]
        assert fx.router.effective.get("actuator.belt") == clean.router.effective.get(
            "actuator.belt"
        )
        assert frame.raw_signals == expected.raw_signals
        with pytest.raises(TypeError):
            frame.raw_signals[OTHER] = None


@pytest.mark.parametrize("f,v", cases(5))
def test_invalid_target_parameter_before_run(f, v):
    spec = plan(f)
    for wrong in (
        {"target_id": "truth.part"},
        {"parameters": {"unsupported": True}},
        {"duration_steps": True},
    ):
        with pytest.raises(CellIOError, match="CFG_"):
            FaultEngine(spec | wrong, 240)
    if f == "F1":
        with pytest.raises(CellIOError, match="CFG_FAULT_TARGET"):
            FaultEngine(spec | {"target_id": "gripper.gap_m"}, 240)
    if f in ("F3", "F4"):
        with pytest.raises(CellIOError, match="CFG_FAULT_PARAMETER"):
            FaultEngine(spec | {"parameters": {"delay_steps": True}}, 240)


@pytest.mark.parametrize("f,v", cases(6))
def test_generation_reset_clears_channels(f, v):
    fx = build(f, v)
    for k in range(4):
        fx.tick(k, commands=(intent(k, 0),) if f == "F4" and k == 3 else ())
    assert fx.fault.active
    fx.fault.reset("next", 1)
    tick = Tick("next", 1, 0)
    fx.fault.advance(tick)
    raw = {SIGNAL: sample(SIGNAL, 0, False, 1)}
    packet = fx.fault.transform_observations(raw, tick)[SIGNAL]
    assert (packet.value, packet.transport_seq, packet.generation) == (False, 0, 1)
    assert fx.fault.actuator_overrides() == {} and fx.fault.state.actual_start is None
    router = CommandRouter("cell_a", "next-attempt", "next", 1)
    ds = router.resolve([intent(0, 0)], fx.fault, tick)
    assert ds[0].status == "STALE" and not router.effective


@pytest.mark.parametrize("f,v", cases(7))
def test_unreached(f, v):
    fx = Fixture(plan(f, start=99, horizon=8), 8)
    for k in range(8):
        fx.tick(k)
    state = fx.fault.finalize(8)
    assert (state["application_status"], state["coverage"], state["actual_start"]) == (
        "UNREACHED",
        "NOT_EXERCISED",
        None,
    )


@pytest.mark.parametrize("f,v", cases(8))
def test_horizon_has_no_restoration(f, v):
    fx = build(f, v, start=2, duration=4, horizon=6)
    for k in range(6):
        fx.tick(k)
    state = fx.fault.finalize(6)
    assert (state["scheduled_end"], state["actual_end"], state["restoration_status"]) == (
        6,
        6,
        "UNOBSERVED",
    )
    with pytest.raises(CellIOError, match="CLOCK_ORDER"):
        fx.tick(6)


def test_T_F2_09_invalid_freeze():
    fx = Fixture(plan("F2", start=0, duration=2))
    first = fx.tick(
        0,
        raw={
            SIGNAL: replace(
                sample(SIGNAL, 0), quality=Quality.BAD, value=None, reason="SOURCE_INVALID"
            )
        },
    )
    second = fx.tick(1)
    assert second.observed_signals[SIGNAL] == first.observed_signals[SIGNAL]
    assert second.observed_signals[SIGNAL].quality == Quality.BAD


def test_T_F2_10_frozen_identity_and_age():
    fx = Fixture(plan("F2", start=0, duration=5))
    packets = [fx.tick(k).observed_signals[SIGNAL] for k in range(6)]
    assert len(set(packets[:5])) == 1
    assert packets[5].transport_seq == 1 and packets[5].deliver_step == 5
    s = fx.fault.state
    assert (
        s.first_effect_step,
        s.value_discrepancy_count,
        s.age_discrepancy_count,
        s.controller_exposure_count,
    ) == (1, 0, 4, 1)


@pytest.mark.parametrize("start", [0, 4])
def test_T_F3_09_fifo_examples(start):
    fx = Fixture(plan("F3", start=start, duration=6, delay=3))
    frames = [fx.tick(k) for k in range(start + 7)]
    expected = [None] * 3 if start == 0 else [start - 1] * 3
    assert [f.observed_signals[SIGNAL].source_step for f in frames[start:]] == [
        *expected,
        start,
        start + 1,
        start + 2,
        start + 6,
    ]
    if start == 0:
        p = frames[0].observed_signals[SIGNAL]
        assert p == SignalSample.missing(SIGNAL, "bool", 0)
    else:
        assert frames[3].observed_signals[SIGNAL] == frames[6].observed_signals[SIGNAL]


def test_T_F3_10_long_delay_flush():
    fx = Fixture(plan("F3", start=0, duration=3, delay=120))
    frames = [fx.tick(k) for k in range(4)]
    assert all(f.observed_signals[SIGNAL].quality == Quality.MISSING for f in frames[:3])
    assert fx.fault.state.flushed_packet_count == 3 and fx.fault.state.delayed_packet_count == 0
    assert [
        e["source_seq"] for e in frames[-1].fault_transitions if e["kind"] == "RESTORE_FLUSH"
    ] == [0, 1, 2]


def test_T_F3_11_packet_due_on_E_discarded():
    fx = Fixture(plan("F3", start=4, duration=6, delay=3))
    frames = [fx.tick(k) for k in range(11)]
    assert [
        e["due_step"] for e in frames[10].fault_transitions if e["kind"] == "RESTORE_FLUSH"
    ] == [10, 11, 12]
    assert frames[10].observed_signals[SIGNAL].source_step == 10


def test_T_F3_12_queue_overflow_and_staleness():
    queue = ObservationDelayQueue(1)
    p = ScheduledObservation("episode", 0, SIGNAL, 1, 0, 1, sample(SIGNAL, 0))
    queue.enqueue(p)
    with pytest.raises(CellIOError, match="QUEUE_OVERFLOW"):
        queue.enqueue(replace(p, enqueue_seq=1))
    with pytest.raises(CellIOError, match="QUEUE_STALE"):
        queue.due(Tick("episode", 0, 1), 2)


def delivered(frame):
    return [d for d in frame.deliveries if d.status == "DELIVERED"]


def test_T_F4_09_due_and_old_goal():
    fx = Fixture(plan("F4", start=4, duration=6, delay=3))
    for k in range(8):
        i = {0: intent(0, 0, 0.08), 4: intent(4, 1, 0.00)}.get(k)
        frame = fx.tick(k, commands=(i,) if i else ())
        assert fx.router.effective[GRIPPER].sequence == int(k >= 7)
        if k == 4:
            assert (
                fx.router.desired[GRIPPER].sequence == 1 and frame.deliveries[0].status == "QUEUED"
            )
    assert delivered(frame)[0].due_step == delivered(frame)[0].delivery_step == 7
    assert fx.fault.state.first_effect_step == 4


def test_T_F4_10_no_retroactive_delay():
    fx = Fixture(plan("F4", start=1, duration=2))
    frames = [fx.tick(k, commands=(intent(0, 0),) if k == 0 else ()) for k in range(5)]
    assert len(delivered(frames[0])) == 1 and not any(f.deliveries for f in frames[1:])
    assert fx.fault.finalize(5)["effect_status"] == "NOT_REALIZED"


@pytest.mark.parametrize("new_at_E", [False, True])
def test_T_F4_11_latest_restored_once(new_at_E):
    fx = Fixture(plan("F4", start=4, duration=6, delay=3))
    inputs = {4: intent(4, 0, 0.04), 8: intent(8, 1, 0.02)}
    if new_at_E:
        inputs[10] = intent(10, 2, 0.01)
    frames = [fx.tick(k, commands=(inputs[k],) if k in inputs else ()) for k in range(13)]
    values = [
        (d.intent.sequence, d.delivery_step, d.delivery_step - d.intent.issued_step)
        for f in frames
        for d in delivered(f)
    ]
    assert values == [(0, 7, 3), (2, 10, 0) if new_at_E else (1, 10, 2)]
    if not new_at_E:
        assert delivered(frames[10])[0].reason == "RESTORE_RESUME"


@pytest.mark.parametrize(
    "cancel_at", [pytest.param(7, id="T-F4-12"), pytest.param(10, id="T-F4-13")]
)
def test_F4_cancel_precedes_poll_or_restore(cancel_at):
    fx = Fixture(plan("F4", start=4, duration=6, delay=3))
    issued = 4 if cancel_at == 7 else 8
    for k in range(12):
        if k == issued:
            cmds = (intent(k, 0),)
        elif k == cancel_at:
            cmds = (
                intent(
                    k,
                    1,
                    0,
                    epoch=1,
                    kind=CommandKind.CANCEL_OPERATION,
                    endpoint="control.operation",
                ),
                intent(k, 2, (), epoch=1, kind=CommandKind.HOLD),
            )
        else:
            cmds = ()
        frame = fx.tick(k, commands=cmds)
        if k == cancel_at:
            assert [d.intent.sequence for d in delivered(frame)] == [1, 2]
            assert any(d.intent.sequence == 0 and d.status == "CANCELLED" for d in frame.deliveries)
    assert fx.router.effective[GRIPPER].kind == CommandKind.HOLD


def test_T_F4_14_queue_overflow_and_duplicate():
    fx = Fixture(plan("F4", start=0, duration=4, delay=3), queue_limit=1)
    original = intent(0, 0)
    frame = fx.tick(0, commands=(original, original))
    assert sorted(d.status for d in frame.deliveries) == ["DUPLICATE", "QUEUED"]
    with pytest.raises(CellIOError, match="QUEUE_OVERFLOW"):
        fx.tick(1, commands=(intent(1, 1),))


def test_T_F6_09_contact_deflection_fails_despite_boundary_commit():
    fx = Fixture(plan("F6", start=0, duration=60))
    for k in range(40):
        fx.tick(k, requested=0.0, position=0.038 if k == 35 else 0.04)
    s = fx.fault.state
    assert (s.effect_status, s.physical_hold_verdict) == ("REALIZED", Verdict.FAIL)


@pytest.mark.parametrize(
    "target,cell,coordinate,requested",
    [(GRIPPER, "cell_a", 0.08, 0.065), ("actuator.clamp", "cell_b", 0.0, -0.02)],
)
def test_T_F6_10_31_samples_mimic_coordinate(target, cell, coordinate, requested):
    fx = Fixture(plan("F6", start=0, duration=60, target=target), cell=cell)
    for k in range(31):
        fx.tick(k, coordinate=coordinate, requested=requested)
        assert len(fx.fault.actuator_overrides()[target]["driver_positions"]) == 1
        assert fx.fault.state.physical_hold_verdict == (
            Verdict.PASS if k == 30 else Verdict.UNKNOWN
        )
