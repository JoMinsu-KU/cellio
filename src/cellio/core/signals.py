from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import fsum

from cellio.io import digest

from .models import (
    CellIOError,
    ControllerView,
    Quality,
    SignalSample,
    freeze,
    quaternion,
)


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
        raise CellIOError(
            "SIGNAL_STALE", "process image belongs to a different generation"
        )
    if any(r.receipt_step >= tick.step for r in receipts):
        raise CellIOError(
            "CLOCK_ORDER", "controller cannot see a current or future P6 receipt"
        )
    return ControllerView(tick, signals, tuple(receipts), operation_epoch)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


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
        projection = fsum(
            abs(_dot(axis, d)) * s / 2 for d, s in zip(body, dimensions, strict=True)
        )
        projection += fsum(
            abs(a) * s / 2 for a, s in zip(axis, zone_dimensions, strict=True)
        )
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

    def reset_evidence(self):
        return self._next == 0 and not self._start

    def sample(self, truth, tick):
        if (
            truth.tick != tick
            or self.generation != tick.generation
            or self._next != tick.step
        ):
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


def canonical_bindings(cell_id, **options):
    from cellio.plugins import scene_plugin

    return scene_plugin(cell_id).signal_bindings(**options)


def WorldPredicates(cell_id, **options):
    from cellio.plugins import scene_plugin

    return scene_plugin(cell_id).create_world_predicates(**options)
