from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import fsum, sqrt
from cellio.io import digest

from .models import CellIOError, ControllerView, Quality, SignalSample, freeze, quaternion


def usable(sample, step, freshness_limit_steps=12):
    return (
        sample.quality == Quality.GOOD
        and sample.source_step is not None
        and 0 <= step - sample.source_step <= freshness_limit_steps
    )


def view_hash(view: ControllerView):
    return digest(view)


def for_controller(signals, tick, receipts=(), operation_epoch=0):
    if any(s.generation != tick.generation for s in signals.values()):
        raise CellIOError("SIGNAL_STALE", "process image belongs to a different generation")
    if any(r.receipt_step >= tick.step for r in receipts):
        raise CellIOError("CLOCK_ORDER", "controller cannot see a current or future P6 receipt")
    return ControllerView(tick, signals, tuple(receipts), operation_epoch)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _body_axes(rotation):
    w, x, y, z = quaternion(rotation)
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)),
        (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)),
        (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)),
    )


def obb_intersects_aabb(center, rotation, dimensions, zone_center, zone_dimensions):
    """Separating axis test of the two closed boxes, including edge cross products."""
    body = _body_axes(rotation)
    world = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    delta = tuple(c - d for c, d in zip(center, zone_center, strict=True))
    for axis in (*world, *body, *(_cross(a, b) for a in world for b in body)):
        projection = fsum(abs(_dot(axis, d)) * s / 2 for d, s in zip(body, dimensions, strict=True))
        projection += fsum(abs(a) * s / 2 for a, s in zip(axis, zone_dimensions, strict=True))
        if abs(_dot(delta, axis)) > projection + 1e-14:
            return False
    return True


@dataclass(frozen=True)
class SignalBinding:
    signal_id: str
    dtype: str
    read: Callable
    dwell_steps: int = 0
    unit: str = "1"
    freshness_limit_steps: int = 12


class NominalSampler:
    def __init__(self, bindings):
        self.bindings = tuple(bindings)
        if len({b.signal_id for b in self.bindings}) != len(self.bindings):
            raise CellIOError("CFG_DUPLICATE", "duplicate signal binding")
        self.reset(0)

    def reset(self, generation):
        self.generation = generation
        self._start = {}
        self._next = 0

    def sample(self, truth, tick):
        if truth.tick != tick or self.generation != tick.generation or self._next != tick.step:
            raise CellIOError("CLOCK_ORDER", "invalid or repeated nominal sample")
        readings = {}
        for b in self.bindings:
            v = b.read(truth)
            if b.dwell_steps:
                if type(v) is not bool and v is not None:
                    raise CellIOError("SIGNAL_DTYPE", b.signal_id)
                if v is True:
                    since = self._start.setdefault(b.signal_id, tick.step)
                    v = tick.step - since >= b.dwell_steps
                else:
                    self._start.pop(b.signal_id, None)
            quality = Quality.GOOD if v is not None else Quality.BAD
            readings[b.signal_id] = SignalSample(
                b.signal_id,
                b.dtype,
                v,
                quality,
                tick.step,
                self._next,
                tick.step,
                None,
                None,
                tick.generation,
                None if v is not None else "SOURCE_INVALID",
            )
        self._next += 1
        return freeze(readings)


def _zone(center, dimensions, part_id):
    def intersect(snapshot):
        part = snapshot.bodies.get(part_id)
        if part is None or not part.valid:
            return None
        return obb_intersects_aabb(
            part.position_m, part.orientation_wxyz, (0.04, 0.04, 0.04), center, dimensions
        )

    return intersect


def canonical_bindings(cell_id, *, part_id="part_000", clamp_joint="clamp"):
    """Scene readbacks use measured joint gaps/speeds and a measured TCP completion sequence."""
    if cell_id not in {"cell_a", "cell_b"}:
        raise CellIOError("CFG_SCENE", cell_id)

    def metric(key):
        return lambda snapshot: snapshot.plant_state.get(key)

    def grip(closed):
        def read(snapshot):
            key = "gripper.gap_m" if closed else "gripper.opening_min_m"
            pos = snapshot.plant_state.get(key)
            speed = snapshot.plant_state.get("gripper.max_speed_mps")
            if pos is None or speed is None:
                return None
            return (pos <= 0.055 if closed else pos >= 0.037) and abs(speed) <= 0.003

        return read

    def clamp(closed):
        def read(snapshot):
            joint = snapshot.joints.get(clamp_joint)
            if joint is None or not joint.valid:
                return None
            return (joint.position <= -0.058 if closed else joint.position >= -0.002) and abs(
                joint.velocity
            ) <= 0.003

        return read

    def belt_stopped(snapshot):
        speed = snapshot.plant_state.get("belt.surface_speed_mps")
        return None if speed is None else abs(speed) <= 0.001

    bindings = [
        SignalBinding("robot.at_waypoint", "bool", metric("robot.at_waypoint")),
        SignalBinding("robot.motion_failed", "bool", metric("robot.motion_failed")),
        SignalBinding("robot.completed_command_id", "int", metric("robot.completed_command_id")),
        SignalBinding("gripper.open", "bool", grip(False), 4),
        SignalBinding("gripper.closed", "bool", grip(True), 12),
        SignalBinding("gripper.gap_m", "float", metric("gripper.gap_m"), unit="m"),
    ]
    output = (0.45, 0.25, 0.815) if cell_id == "cell_a" else (0.35, 0.30, 0.815)
    bindings.append(
        SignalBinding("destination.present", "bool", _zone(output, (0.10, 0.10, 0.060), part_id))
    )
    if cell_id == "cell_a":
        bindings.extend(
            (
                SignalBinding(
                    "pickup.present",
                    "bool",
                    _zone((0.45, -0.099, 0.81), (0.10, 0.002, 0.04), part_id),
                ),
                SignalBinding("belt.stopped", "bool", belt_stopped),
            )
        )
    else:
        bindings.extend(
            (
                SignalBinding(
                    "source.present",
                    "bool",
                    _zone((0.45, -0.24, 0.815), (0.10, 0.10, 0.060), part_id),
                ),
                SignalBinding(
                    "fixture.present",
                    "bool",
                    _zone((0.507, 0.12, 0.815), (0.056, 0.056, 0.060), part_id),
                ),
                SignalBinding("clamp.open", "bool", clamp(False), 4),
                SignalBinding("clamp.closed", "bool", clamp(True), 4),
                SignalBinding("robot.clear_fixture", "bool", metric("robot.clear_fixture")),
                SignalBinding("process.done", "bool", metric("process.done")),
            )
        )
    return tuple(bindings)


class WorldPredicates:
    """Evaluate registered trigger predicates in P2 from S_k, before observation faults."""

    def __init__(self, cell_id, *, part_id="part_000", clamp_joint="clamp"):
        self.cell_id, self.part_id = cell_id, part_id
        selected = {"gripper.closed", "clamp.closed", "pickup.present"}
        bindings = canonical_bindings(cell_id, part_id=part_id, clamp_joint=clamp_joint)
        self._sampler = NominalSampler(b for b in bindings if b.signal_id in selected)

    def reset(self, generation):
        self._sampler.reset(generation)

    def sample(self, truth, tick):
        readings = self._sampler.sample(truth, tick)
        result = {f"world.{key.replace('.','_')}": s.value for key, s in readings.items()}
        part = truth.bodies.get(self.part_id)
        valid = part is not None and part.valid
        result["world.part_dropped"] = part.position_m[2] < 0.700 if valid else None
        if self.cell_id == "cell_b":
            if not valid:
                result["world.fixture_loaded"] = None
            else:
                x, y, z = part.position_m
                axes = _body_axes(part.orientation_wxyz)
                half_width_x = sum(abs(axis[0]) * 0.02 for axis in axes)
                result["world.fixture_loaded"] = (
                    0.501 <= x <= 0.513
                    and 0.114 <= y <= 0.126
                    and 0.804 <= z <= 0.816
                    and sqrt(_dot(part.linear_velocity_mps, part.linear_velocity_mps)) <= 0.01
                    and x - half_width_x >= 0.481
                    and x + half_width_x <= 0.550
                )
        return freeze(result)
