from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import IntEnum
from fractions import Fraction
from math import isclose, isfinite

from .models import AdvanceReceipt, CellIOError, Tick, uint


def steps_from_seconds(decimal_string: str) -> int:
    try:
        seconds = Decimal(decimal_string)
        if type(decimal_string) is not str or not seconds.is_finite() or seconds < 0:
            raise ValueError()
        return int((seconds * 120).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CellIOError("CFG_CLOCK", "expected a finite nonnegative decimal string") from exc


class Phase(IntEnum):
    GUARD = 0
    SNAPSHOT = 1
    SCHEDULE = 2
    OBSERVE = 3
    CONTROL = 4
    COMMAND = 5
    ACTUATE = 6
    EVALUATE = 7
    RECORD = 8
    ADVANCE = 9


class PhaseGuard:
    def __init__(self):
        self._next_step = 0
        self._next_phase = Phase.GUARD

    def enter(self, step, phase):
        if step != self._next_step or phase != self._next_phase:
            raise CellIOError("CLOCK_PHASE", f"expected ({self._next_step}, {self._next_phase})")
        if phase == Phase.ADVANCE:
            self._next_step += 1
            self._next_phase = Phase.GUARD
        else:
            self._next_phase = Phase(phase + 1)


class StepClock:
    dt = Fraction(1, 120)

    def __init__(self, episode_id, generation, horizon_steps=7200):
        self.episode_id = episode_id
        self.generation = generation
        self.horizon = uint(horizon_steps, "horizon")
        self.step = 0
        self.engine_offset = None
        self.at(0)

    def at(self, step=None):
        if step is not None and step != self.step:
            raise CellIOError("CLOCK_ORDER", f"expected step {self.step}, got {step}")
        return Tick(self.episode_id, self.generation, self.step)

    @property
    def control_due(self):
        return self.step < self.horizon and self.step % 4 == 0

    def commit(self, receipt: AdvanceReceipt):
        if (
            not receipt.success
            or self.step >= self.horizon
            or (receipt.from_step, receipt.to_step) != (self.step, self.step + 1)
        ):
            raise CellIOError("SIM_ADVANCE_FAILED", "missing or out-of-order physics advance")
        offset = receipt.engine_time_before_s if self.engine_offset is None else self.engine_offset
        for index, time in (
            (self.step, receipt.engine_time_before_s),
            (self.step + 1, receipt.engine_time_after_s),
        ):
            if not isfinite(time) or not isclose(
                time, offset + index / 120, rel_tol=0, abs_tol=1e-5
            ):
                raise CellIOError("CLOCK_DRIFT", f"physics time mismatch at {index}")
        self.engine_offset = offset
        self.step += 1
