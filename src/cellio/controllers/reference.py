"""Deterministic state machine operating exclusively on ControllerView."""

from cellio.core.models import CellIOError, CommandIntent, CommandKind, ControllerOutput
from .stages import A_POINTS, CELL_A


KINDS = {"robot": CommandKind.POSE, "gripper": CommandKind.GRIPPER_GAP,
         "clamp": CommandKind.JOINT_POSITION, "process": CommandKind.BOOL, "belt": CommandKind.BOOL}
MOTION = ("robot.at_waypoint", "robot.completed_command_id", "robot.motion_failed")


class ReferenceController:
    def __init__(self, scene_id="cell_a", variant="reference"):
        self.scene_id = scene_id
        variant = {f"mutant.c_m{i}": f"C_M{i}" for i in range(1,7)}.get(variant,variant)
        self.variant = "reference" if variant in ("reference", "C_REF") else variant
        if self.variant not in ("reference", *(f"C_M{i}" for i in range(1, 7))):
            raise CellIOError("CFG_CONTROLLER", str(variant))
        if scene_id == "cell_a":
            self.stages, self.points = CELL_A, A_POINTS
        elif scene_id == "cell_b":
            from .stages_b import B_POINTS, CELL_B
            self.stages, self.points = CELL_B, B_POINTS
        else:
            raise CellIOError("CFG_SCENE", str(scene_id))

    def reset(self, config, initial_view):
        tick = initial_view.tick
        self.attempt_id = config.get("attempt_id", tick.episode_id)
        self.episode = (tick.episode_id, tick.generation)
        self.sequence = self.epoch = self.index = self.substage = 0
        self.entered = self.ready_step = self.response_start = 0
        self.pending = True
        self.started = False
        self.terminal = self.reason = self.dwell_start = None
        self.robot_sequence = self.clear_start = None
        self.endpoint_commands = {}
        self.applied = set()
        self.last_input = self.last_output = None

    @property
    def stage_name(self):
        return self.terminal.lower() if self.terminal else self.stages[self.index].name

    def _event(self, name, **fields):
        self.events.append({"event_type": "controller." + name, "payload": {
            "stage_id": self.stage_name, "job_id": 0, "operation_epoch": self.epoch, **fields}})

    def _command(self, endpoint, value, kind=None):
        sequence = self.sequence
        self.sequence += 1
        actuator = "control.operation" if endpoint == "operation" else "actuator." + endpoint
        command = CommandIntent(f"{self.attempt_id}:cmd:{sequence}", sequence, self.now.episode_id,
                                self.now.generation, self.epoch, self.now.step, actuator,
                                kind or KINDS[endpoint], value, 0)
        self.commands.append(command)
        self.endpoint_commands[endpoint] = command
        if command.kind == CommandKind.POSE:
            self.robot_sequence = sequence

    def _stop(self, cancel):
        if cancel:
            old_epoch = self.epoch
            self.epoch += 1
            self._command("operation", old_epoch, CommandKind.CANCEL_OPERATION)
        for endpoint in (("robot", "gripper") if self.scene_id == "cell_a" else ("robot", "gripper", "clamp")):
            self._command(endpoint, (), CommandKind.HOLD)
        self._command("belt" if self.scene_id == "cell_a" else "process", False)

    def _abort(self, reason):
        self.terminal, self.reason, self.pending = "ABORTED", reason, False
        self._event("abort", reason=reason, cancelled_epoch=self.epoch)
        self._event("stage_enter")
        if self.variant != "C_M5":
            self._stop(True)

    def _next(self):
        self.index += 1
        self.entered = self.now.step
        self.ready_step = self.entered + 4
        self.pending = True
        self.substage = 0
        self.clear_start = self.dwell_start = None
        if self.index == len(self.stages):
            self.terminal = "DONE"
            self._event("done")
        self._event("stage_enter")

    def _value(self, signal):
        sample = self.view.signals.get(signal)
        dtype = "int" if signal == "robot.completed_command_id" else "bool"
        if sample is None or sample.quality != "GOOD" or sample.value is None:
            if self.variant == "C_M1" and dtype == "bool":
                return True
            raise ValueError("INVALID_INPUT:" + signal)
        expected = int if dtype == "int" else bool
        if sample.dtype != dtype or type(sample.value) is not expected or sample.source_step is None:
            raise ValueError("INVALID_INPUT:" + signal)
        age = self.now.step - sample.source_step
        if age < 0 or (age > 12 and self.variant != "C_M6"):
            raise ValueError("STALE_INPUT:" + signal)
        return sample.value

    def _matched(self):
        return (self._value("robot.at_waypoint")
                and self._value("robot.completed_command_id") == self.robot_sequence)

    def _admit(self, stage):
        values = {name: self._value(name) for name in stage.admission}
        if self.variant == "C_M4":
            for endpoint in ("gripper", "clamp"):
                command = self.endpoint_commands.get(endpoint)
                if command is not None:
                    for suffix in ("open", "closed"):
                        name = endpoint + "." + suffix
                        if name in values:
                            values[name] = command.command_id in self.applied
        ready = all(value is True for key, value in values.items() if key != "robot.completed_command_id")
        if "robot.completed_command_id" in values:
            ready &= values["robot.completed_command_id"] == self.robot_sequence
        return ready

    def _emit_entry(self, stage):
        if self.substage == 0:
            for endpoint, value in stage.actions:
                self._command(endpoint, value)
        if stage.poses:
            self._command("robot", (*self.points[stage.poses[self.substage]], 0., 0., 1., 0.))
        self.pending = False
        self.response_start = self.now.step
        if stage.name in ("wait_open", "wait_clamp"):
            self.response_start = self.endpoint_commands["clamp"].issued_step

    def _reached(self, stage):
        signals = {name: self._value(name) for name in stage.signals}
        for endpoint in ("gripper", "clamp"):
            if self.variant == "C_M4" and endpoint in self.endpoint_commands:
                for name in (endpoint + ".open", endpoint + ".closed"):
                    if name in signals and stage.name != "verify_output":
                        signals[name] = self.endpoint_commands[endpoint].command_id in self.applied
        if stage.name == "fixture_retreat" and self.variant == "C_M3":
            return signals["robot.clear_fixture"]
        if stage.poses:
            if not self._matched():
                return False
            if self.substage + 1 < len(stage.poses):
                self.substage += 1
                self.pending = True
                self.ready_step = self.now.step + 4
                self._event("substage_enter", waypoint=stage.poses[self.substage])
                return False
            if stage.name == "fixture_retreat" and self.clear_start is None:
                self.clear_start = self.now.step
        if not all(signals.values()):
            self.dwell_start = None
            return False
        if stage.name == "wait_stop":
            if self.variant == "C_M3":
                return True
            if self.now.step - self.response_start < 40 and self.dwell_start is None:
                self.dwell_start = self.now.step
        if self.dwell_start is None:
            self.dwell_start = self.now.step
        return (self.now.step - self.dwell_start >= stage.dwell and
                (stage.name != "wait_stop" or self.now.step - self.response_start >= 40))

    def _deadline(self, stage):
        if self.clear_start is not None:
            return self.clear_start + 360
        if stage.name in ("initialize", "wait_stop", "wait_part", "verify_output") or self.pending:
            return self.entered + stage.timeout
        return self.response_start + stage.timeout

    def _advance(self):
        if not self.started:
            if self.now.step != 0:
                raise CellIOError("CLOCK_ORDER", "first control call must be at step zero")
            self.started = True
            self.ready_step = 4
            self._event("stage_enter")
            return
        if self.terminal:
            if self.terminal == "DONE" and self.pending:
                self._stop(False)
                self.pending = False
            return
        stage = self.stages[self.index]
        if stage.poses:
            if self._value("robot.motion_failed"):
                self._abort("MOTION_FAILED")
                return
            # Validate both fields even when at_waypoint currently reads false.
            for signal in MOTION[:2]:
                self._value(signal)
        for signal in stage.signals:
            self._value(signal)
        if self.pending:
            if self.now.step < self.ready_step:
                return
            if not self._admit(stage):
                if self.now.step >= self._deadline(stage) and self.variant != "C_M2":
                    self._abort("ADMISSION_TIMEOUT")
                return
            self._emit_entry(stage)
        if self._reached(stage):
            self._next()
        elif not self.pending and self.now.step >= self._deadline(stage) and self.variant != "C_M2":
            self._abort("RESPONSE_TIMEOUT")

    def tick(self, view, tick):
        if view.tick != tick or (tick.episode_id, tick.generation) != self.episode or tick.step % 4:
            raise CellIOError("CLOCK_ORDER", "controller view/tick mismatch")
        if self.last_input is not None:
            if view == self.last_input:
                return self.last_output
            if tick.step != self.last_input.tick.step + 4:
                raise CellIOError("CLOCK_ORDER", "controller tick missing or repeated")
        self.now, self.view = tick, view
        self.commands, self.events = [], []
        self.applied.update(r.command_id for r in view.previous_command_receipts
                            if r.status == "APPLIED" and r.operation_epoch == self.epoch)
        try:
            self._advance()
        except ValueError as error:
            if not str(error).startswith(("STALE_INPUT:", "INVALID_INPUT:")):
                raise
            self._abort(str(error))
        self.last_input = view
        self.last_output = ControllerOutput(tuple(self.commands), tuple(self.events), self.stage_name,
                                           0, self.epoch, self.terminal or "RUNNING", self.reason)
        return self.last_output

    def close(self):
        self.last_input = self.last_output = self.view = None
