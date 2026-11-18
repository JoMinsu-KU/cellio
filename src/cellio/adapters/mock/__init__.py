"""Deterministic R0 port fixture. Analytic actuator states are labelled as mock evidence."""

from math import cos, dist, sin

from cellio.core.models import (
    ActuationReceipt,
    AdvanceReceipt,
    AppliedCommand,
    BodyState,
    CellIOError,
    CommandKind,
    JointState,
    TruthSnapshot,
)

FINGERS = ("panda_finger_joint1", "panda_finger_joint2")
HOME = (0.0, -0.785398163, 0.0, -2.356194490, 0.0, 1.570796327, 0.785398163)
DOWN = (0.0, 0.0, 1.0, 0.0)
ZERO = (0.0, 0.0, 0.0)


def slew(actual, desired, increment):
    return actual + max(-increment, min(increment, desired - actual))


class MockPlant:
    """A bounded kinematic stand-in for transport, arm, and I/O sequence tests.

    The cube moves on the analytic belt. The mock does not manufacture contacts,
    carry the cube with the gripper, or generate physical grip/clamp success.
    """

    def __init__(self, **options):
        self.provenance = {"backend": "mock", "physics_evidence": False}
        self.loaded = False

    def load(self, scene_config):
        self.cell = scene_config.get("cell_id", scene_config.get("scene_id"))
        if self.cell not in {"cell_a", "cell_b"}:
            raise CellIOError("CFG_SCENE", str(self.cell))
        variant = scene_config.get("variant_id", "canonical")
        if variant == "conveyor_speed_plus_10pct" and self.cell == "cell_a":
            self.belt_setpoint = 0.066
        elif variant == "canonical":
            self.belt_setpoint = 0.060
        else:
            raise CellIOError("CFG_SCENE", f"Unsupported {self.cell} variant {variant}")
        self.provenance["scene_variant"] = variant
        self.warmup_steps = scene_config.get("warmup_steps", 240)
        self.loaded = True

    def reset(self, initial_state, generation):
        if not self.loaded:
            raise CellIOError("SIM_STATE_INVALID", "mock scene not loaded")
        if hasattr(self, "generation") and generation <= self.generation:
            raise CellIOError("STALE_GENERATION", "mock reset generation must increase")
        self.generation, self.step = generation, 0
        self.tcp = self.tcp_goal = (0.30, 0.0, 1.10)
        position = (0.45, -0.41 if self.cell == "cell_a" else -0.24, 0.81)
        self.part = (
            position[0] + initial_state.get("x_offset_m", 0.0),
            position[1] + initial_state.get("y_offset_m", 0.0),
            position[2],
        )
        yaw = initial_state.get("yaw_rad", 0.0)
        self.part_q = (cos(yaw / 2), 0.0, 0.0, sin(yaw / 2))
        self.part_velocity = self.tcp_velocity = ZERO
        self.gap = self.gap_goal = 0.08
        self.clamp = self.clamp_goal = 0.0
        self.finger_velocity = self.clamp_velocity = 0.0
        self.belt = 0.0
        self.belt_run = self.process = False
        self.process_counter = 0
        self.completed_id, self.pose_id, self.reached_since = -1, None, None
        self.hold_active = False
        self.ready_to_advance = False
        self.targets = {}
        self._actuation_evidence = {}
        self.geometry_manifest = {"mock": True, "finger_driver_ids": [FINGERS[0]]}

    def reset_evidence(self, truth):
        return {
            "new_generation": truth.tick.generation == self.generation
            and self.step == 0,
            "motion_empty": self.pose_id is None
            and self.completed_id == -1
            and self.tcp_goal == self.tcp,
            "overrides_clear": not self._actuation_evidence and not self.targets,
            "belt_off": self.belt == 0 and not self.belt_run,
            "process_clear": not self.process and self.process_counter == 0,
        }

    def snapshot(self, tick):
        if (tick.step, tick.generation) != (self.step, self.generation):
            raise CellIOError("SIM_STATE_INVALID", "mock snapshot identity mismatch")

        def body(name, p, v, rot=DOWN):
            return BodyState(name, "world", p, rot, v, ZERO, True)

        x, y, z = self.tcp
        bodies = {
            "part_000": body("part_000", self.part, self.part_velocity, self.part_q),
            "cell_tcp": body("cell_tcp", self.tcp, self.tcp_velocity),
            "hand": body("hand", (x, y, z + 0.1034), self.tcp_velocity),
        }
        for name, direction in (("finger_left", -1), ("finger_right", 1)):
            bodies[name] = body(
                name, (x, y + direction * self.gap / 2, z), self.tcp_velocity
            )
        joints = {
            f"panda_joint{i}": JointState(
                f"panda_joint{i}", "revolute", q, 0.0, None, True
            )
            for i, q in enumerate(HOME, 1)
        }
        joints.update(
            {
                n: JointState(
                    n, "prismatic", self.gap / 2, self.finger_velocity, None, True
                )
                for n in FINGERS
            }
        )
        scalar = {
            "gripper.gap_m": self.gap,
            "gripper.opening_min_m": self.gap / 2,
            "gripper.max_speed_mps": abs(self.finger_velocity),
            "belt.surface_speed_mps": self.belt,
            "robot.completed_command_id": self.completed_id,
            "robot.at_waypoint": self.pose_id is not None
            and self.completed_id == self.pose_id,
            "robot.motion_failed": False,
            "process.done": self.process and self.process_counter >= 120,
        }
        bounds_outside = []
        for name, r in (
            ("hand", 0.025),
            ("finger_left", 0.005),
            ("finger_right", 0.005),
        ):
            lo = tuple(c - r for c in bodies[name].position_m)
            hi = tuple(c + r for c in bodies[name].position_m)
            for i, axis in enumerate("xyz"):
                scalar[f"{name}.bounds.min_{axis}"] = lo[i]
                scalar[f"{name}.bounds.max_{axis}"] = hi[i]
            bounds_outside.append(
                any(
                    h < a or l > b
                    for l, h, a, b in zip(
                        lo, hi, (0.39, 0.02, 0.79), (0.66, 0.22, 1.0), strict=True
                    )
                )
            )
        scalar["robot.clear_fixture"] = all(bounds_outside)
        if self.cell == "cell_b":
            joints["clamp"] = JointState(
                "clamp", "prismatic", self.clamp, self.clamp_velocity, None, True
            )
            bodies["moving_jaw"] = body(
                "moving_jaw",
                (0.590 + self.clamp, 0.12, 0.82),
                (self.clamp_velocity, 0.0, 0.0),
                (1.0, 0.0, 0.0, 0.0),
            )
        return TruthSnapshot(tick, bodies, joints, (), scalar, True)

    def driver_positions(self, target, truth):
        joint = FINGERS[0] if target == "actuator.gripper" else "clamp"
        return {joint: truth.joints[joint].position}

    def coordinate(self, target, truth):
        return (
            truth.plant_state["gripper.gap_m"]
            if target == "actuator.gripper"
            else truth.joints["clamp"].position
        )

    def actuation_evidence(self, target):
        return self._actuation_evidence.get(target, {})

    def apply(self, commands, overrides, tick):
        if self.ready_to_advance or (tick.step, tick.generation) != (
            self.step,
            self.generation,
        ):
            raise CellIOError(
                "SIM_ACTUATION_FAILED", "double or out-of-order application"
            )
        applied = []
        for d in commands:
            if d.status != "DELIVERED":
                continue
            i = d.intent
            if i.kind == CommandKind.CANCEL_OPERATION:
                self.pose_id, self.reached_since = None, None
                self.tcp_goal = self.tcp
            elif i.actuator_id == "actuator.robot":
                self.tcp_goal = self.tcp if i.kind == CommandKind.HOLD else i.value[:3]
                self.pose_id = None if i.kind == CommandKind.HOLD else i.sequence
                self.reached_since = None
            elif i.actuator_id == "actuator.gripper":
                self.gap_goal = self.gap if i.kind == CommandKind.HOLD else i.value
            elif i.actuator_id == "actuator.clamp":
                self.clamp_goal = self.clamp if i.kind == CommandKind.HOLD else i.value
            elif i.actuator_id == "actuator.belt":
                self.belt_run = i.value
            elif i.actuator_id == "actuator.process":
                if not i.value:
                    self.process_counter = 0
                self.process = i.value
            applied.append(
                AppliedCommand(
                    i.command_id,
                    i.actuator_id,
                    tick.generation,
                    i.issued_step,
                    d.due_step,
                    tick.step,
                    "APPLIED",
                    d.reason,
                    i.value,
                )
            )
        self.targets = {"actuator.robot": (*self.tcp_goal, *DOWN)}
        self._actuation_evidence = {}
        positions = [
            (
                "actuator.gripper",
                FINGERS[0],
                self.gap / 2,
                self.gap_goal / 2,
                self.gap_goal,
            )
        ]
        if self.cell == "cell_b":
            positions.append(
                (
                    "actuator.clamp",
                    "clamp",
                    self.clamp,
                    self.clamp_goal,
                    self.clamp_goal,
                )
            )
        for target, joint, position, desired, coordinate in positions:
            nominal = slew(position, desired, 0.04 / 120)
            final = (
                overrides.get(target, {})
                .get("driver_positions", {})
                .get(joint, nominal)
            )
            self.targets[target] = final
            self._actuation_evidence[target] = {
                "nominal_coordinate": coordinate,
                "nominal_targets": {joint: nominal},
                "applied_targets": {joint: final},
            }
        if self.cell == "cell_a":
            self.targets["actuator.belt"] = self.belt_run
        else:
            self.targets["actuator.process"] = self.process
        self.ready_to_advance = True
        return ActuationReceipt(
            tick,
            True,
            tuple(applied),
            self.targets,
            tuple(o["fault_id"] for o in overrides.values()),
        )

    def advance_one_step(self, render=False):
        if not self.ready_to_advance:
            raise CellIOError("SIM_ADVANCE_FAILED", "advance without a committed input")
        k = self.step
        finger = self.targets["actuator.gripper"]
        self.finger_velocity = (finger - self.gap / 2) * 120
        self.gap = 2 * finger
        if self.cell == "cell_b":
            clamp = self.targets["actuator.clamp"]
            self.clamp_velocity = (clamp - self.clamp) * 120
            self.clamp = clamp
            self.process_counter += int(self.process)
        else:
            self.belt = slew(
                self.belt, self.belt_setpoint if self.belt_run else 0.0, 1.0 / 120
            )
            self.part_velocity = (0.0, self.belt, 0.0)
            self.part = (self.part[0], self.part[1] + self.belt / 120, self.part[2])
        distance = dist(self.tcp, self.tcp_goal)
        fraction = min(1.0, 0.15 / 120 / distance) if distance else 0.0
        delta = tuple(
            (b - a) * fraction for a, b in zip(self.tcp, self.tcp_goal, strict=True)
        )
        self.tcp = tuple(a + d for a, d in zip(self.tcp, delta, strict=True))
        self.tcp_velocity = tuple(d * 120 for d in delta)
        if self.pose_id is not None and dist(self.tcp, self.tcp_goal) <= 0.004:
            if self.reached_since is None:
                self.reached_since = k + 1
            if k + 1 - self.reached_since >= 12:
                self.completed_id = self.pose_id
        self.step += 1
        self.ready_to_advance = False
        return AdvanceReceipt(
            k,
            k + 1,
            (self.warmup_steps + k) / 120,
            (self.warmup_steps + k + 1) / 120,
            True,
            render,
        )

    def close(self):
        self.loaded = False
