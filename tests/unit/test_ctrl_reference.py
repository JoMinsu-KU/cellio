from cellio.controllers import ReferenceController
from cellio.core.models import ControllerView, Quality, SignalSample, Tick


def view(step, signals=None, stale=None):
    data = {
        "robot.at_waypoint": False,
        "robot.completed_command_id": -1,
        "robot.motion_failed": False,
        "gripper.open": True,
        "pickup.present": False,
        "belt.stopped": False,
        "gripper.closed": False,
        "destination.present": False,
    }
    data.update(signals or {})
    samples = {}
    for name, value in data.items():
        dtype = "int" if name.endswith("command_id") else "bool"
        source = 0 if stale == name else step
        samples[name] = (
            SignalSample.missing(name, dtype, 0)
            if value is None
            else SignalSample(
                name,
                dtype,
                value,
                Quality.GOOD,
                source,
                source,
                step,
                step,
                step,
                0,
                None,
            )
        )
    return ControllerView(Tick("ep", 0, step), samples, (), 0)


def run(controller, step, signals=None, stale=None):
    inputs = view(step, signals, stale)
    return controller.tick(inputs, inputs.tick)


def new(variant="reference"):
    controller = ReferenceController("cell_a", variant)
    controller.reset({"attempt_id": "attempt"}, view(0))
    return controller


def test_entry_timing_matching_id_and_replay():
    for _ in range(2):
        controller = new()
        initial = run(controller, 0)
        assert initial == run(controller, 0)
        assert not initial.new_commands
        assert initial.emitted_events[0]["payload"]["stage_id"] == "initialize"
        entry = run(controller, 4)
        robot = next(c for c in entry.new_commands if c.actuator_id == "actuator.robot")
        assert robot.command_id == "attempt:cmd:2"
        assert (
            run(
                controller,
                8,
                {"robot.at_waypoint": True, "robot.completed_command_id": 0},
            ).stage_id
            == "initialize"
        )
        reached = run(
            controller, 12, {"robot.at_waypoint": True, "robot.completed_command_id": 2}
        )
        assert reached.stage_id == "wait_part" and not reached.new_commands
        assert run(controller, 16).new_commands[0].actuator_id == "actuator.belt"


def test_bad_input_abort_and_targeted_abort_omission():
    for variant, kinds in (
        ("reference", ["CANCEL_OPERATION", "HOLD", "HOLD", "BOOL"]),
        ("C_M5", []),
    ):
        controller = new(variant)
        run(controller, 0)
        output = run(controller, 4, {"gripper.open": None})
        assert output.stage_id == "aborted"
        assert [x.kind for x in output.new_commands] == kinds
        assert output.active_operation_epoch == (1 if kinds else 0)
        assert not run(controller, 8).new_commands


def test_stale_sample_and_boundary():
    for variant, expected in (("reference", "aborted"), ("C_M6", "initialize")):
        controller = new(variant)
        for k in (0, 4, 8, 12):
            assert run(controller, k, stale="gripper.open").stage_id == "initialize"
        assert run(controller, 16, stale="gripper.open").stage_id == expected


def test_final_deadline_observation_precedes_timeout():
    for match, final in ((True, "wait_part"), (False, "aborted")):
        c = new()
        for k in range(0, 1200, 4):
            run(c, k)
        result = run(
            c, 1200, {"robot.at_waypoint": match, "robot.completed_command_id": 2}
        )
        assert result.stage_id == final


def test_single_mutant_policy_differences_on_same_public_view():
    # Start each probe by restoring the same already-entered control state.
    for variant, expected in (("reference", "aborted"), ("C_M1", "lift")):
        c = new(variant)
        run(c, 0)
        c.index, c.pending, c.response_start = 5, False, 0
        assert run(c, 4, {"gripper.closed": None}).stage_id == expected
    for variant, expected in (("reference", "wait_stop"), ("C_M3", "approach")):
        c = new(variant)
        run(c, 0)
        c.index = 2
        result = run(c, 4, {"belt.stopped": True})
        assert result.stage_id == expected
    for variant, expected in (("reference", "aborted"), ("C_M2", "grasp")):
        c = new(variant)
        run(c, 0)
        c.index, c.pending, c.response_start = 5, False, 0
        for k in range(4, 241, 4):
            result = run(c, k)
        assert result.stage_id == expected


def test_mutant_4_uses_previous_applied_receipt():
    from cellio.core.models import PublicCommandReceipt

    for variant, expected in (("reference", "grasp"), ("C_M4", "lift")):
        c = new(variant)
        run(c, 0)
        c.index, c.robot_sequence = 5, 99
        issued = run(
            c, 4, {"robot.at_waypoint": True, "robot.completed_command_id": 99}
        )
        cmd = issued.new_commands[0]
        inputs = view(8)
        receipt = PublicCommandReceipt(
            cmd.command_id, 0, "actuator.gripper", "APPLIED", 4, None
        )
        out = c.tick(
            ControllerView(inputs.tick, inputs.signals, (receipt,), 0), inputs.tick
        )
        assert out.stage_id == expected


def test_cell_b_completes_each_stage_in_order_with_fresh_matching_public_inputs():
    from cellio.controllers.stages_b import CELL_B

    controller = ReferenceController("cell_b")
    controller.reset({"attempt_id": "b"}, view(0))
    transitions = []
    poses = []
    completed = -1
    for k in range(0, 1600, 4):
        extra = {
            s: True
            for s in (
                "robot.at_waypoint",
                "gripper.closed",
                "fixture.present",
                "robot.clear_fixture",
                "clamp.open",
                "clamp.closed",
                "process.done",
                "destination.present",
            )
        }
        extra["robot.completed_command_id"] = completed
        output = run(controller, k, extra)
        transitions.extend(
            e["payload"]["stage_id"]
            for e in output.emitted_events
            if e["event_type"] == "controller.stage_enter"
        )
        for command in output.new_commands:
            if command.kind == "POSE":
                poses.append(command.value[:3])
                completed = command.sequence
        if output.mission_status == "DONE":
            break
    assert transitions == [s.name for s in CELL_B] + ["done"]
    assert (0.507, 0.12, 0.815) in poses and (0.500, 0.12, 0.815) in poses
