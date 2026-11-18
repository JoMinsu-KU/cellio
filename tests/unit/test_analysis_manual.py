import pytest

from cellio.analysis.manual import ManualObservationFault
from cellio.core.faults import FaultEngine
from cellio.core.models import Event, Quality, SignalSample, Tick
from cellio.runtime.campaign import scenario


@pytest.mark.parametrize("cell", ["cell_a", "cell_b"])
@pytest.mark.parametrize("arm", ["f1_low", "f1_high", "f2", "f3", "f5"])
def test_manual_path_matches_modular_boundary_and_packet_stream(cell, arm):
    spec = scenario(cell, arm, 1000, profile="mock_lockstep")["faults"][0]
    manual = ManualObservationFault(spec, 7200, cell)
    modular = FaultEngine(spec, 7200, cell)
    for engine in (manual, modular):
        engine.reset("e", 0)
    target = spec["target_id"]
    stage = spec["trigger"]["event_filter"]["stage_id"]
    event = Event(
        "evt", "controller.stage_enter", "e", 0, 4, 4, 0, 5, None, {"stage_id": stage}
    )
    for k in range(7 + spec["duration_steps"]):
        tick = Tick("e", 0, k)
        raw = {
            target: SignalSample(
                target, "bool", bool(k % 2), Quality.GOOD, k, k, k, None, None, 0, None
            )
        }
        observed = []
        for engine in (manual, modular):
            engine.advance(tick, [event] if k == 5 else [])
            observed.append(engine.transform_observations(raw, tick)[target])
        assert observed[0] == observed[1], (cell, arm, k)
    assert (
        manual.state.actual_start == 5
        and manual.state.actual_end == 5 + spec["duration_steps"]
    )
    assert observed[0].source_step == k and observed[0].value == bool(k % 2)
    assert not manual.manual_queue
