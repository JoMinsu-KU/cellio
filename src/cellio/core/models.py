"""Immutable domain records. No simulator objects cross this boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from math import isfinite, sqrt
from types import MappingProxyType
from typing import TypeAlias

from cellio.io import CellIOError as DomainError
from cellio.io import json_value as json_value


class CellIOError(DomainError):
    def __init__(self, code: str, message: str):
        super().__init__(
            code, f"{code}: {message}", exit_code=2 if code.startswith("CFG_") else 4
        )


Scalar: TypeAlias = bool | int | float
Vec3: TypeAlias = tuple[float, float, float]
Quat: TypeAlias = tuple[float, float, float, float]
Target: TypeAlias = Scalar | tuple[float, ...]


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    if value is None or isinstance(value, (bool, int, str)) or is_dataclass(value):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise CellIOError("INVALID_FIELD", "expected immutable finite data")


def uint(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise CellIOError("INVALID_FIELD", f"invalid {name}: {value!r}")
    return value


def vector(value, size):
    if (
        not isinstance(value, (tuple, list))
        or len(value) != size
        or any(type(n) not in (float, int) or not isfinite(n) for n in value)
    ):
        raise CellIOError(
            "SIM_STATE_INVALID", f"expected {size} finite numeric coordinates"
        )
    return tuple(float(n) for n in value)


def quaternion(value):
    q = vector(value, 4)
    norm = sqrt(sum(n * n for n in q))
    if abs(norm - 1) > 1e-3:
        raise CellIOError("SIM_STATE_INVALID", "invalid quaternion norm")
    return tuple(n / norm for n in q) if abs(norm - 1) > 1e-6 else q


class Record:
    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, (list, tuple, Mapping)):
                object.__setattr__(self, field.name, freeze(value))
            elif isinstance(value, float) and not isfinite(value):
                raise CellIOError("INVALID_FIELD", f"nonfinite {field.name}")


class Quality(StrEnum):
    GOOD = "GOOD"
    MISSING = "MISSING"
    BAD = "BAD"


class CommandKind(StrEnum):
    POSE = "POSE"
    GRIPPER_GAP = "GRIPPER_GAP"
    JOINT_POSITION = "JOINT_POSITION"
    BOOL = "BOOL"
    HOLD = "HOLD"
    CANCEL_OPERATION = "CANCEL_OPERATION"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Tick(Record):
    episode_id: str
    generation: int
    step: int
    hz: int = 120

    def __post_init__(self):
        uint(self.generation, "generation", 2**64 - 1)
        uint(self.step, "step")
        if self.hz != 120 or type(self.hz) is not int or not self.episode_id:
            raise CellIOError(
                "CLOCK_INVALID", "expected episode identity and 120 Hz clock"
            )


@dataclass(frozen=True)
class BodyState(Record):
    entity_id: str
    frame_id: str
    position_m: Vec3 | None
    orientation_wxyz: Quat | None
    linear_velocity_mps: Vec3 | None
    angular_velocity_rps: Vec3 | None
    valid: bool

    def __post_init__(self):
        if not self.frame_id or type(self.valid) is not bool:
            raise CellIOError("SIM_STATE_INVALID", self.entity_id)
        for name in (
            "position_m",
            "orientation_wxyz",
            "linear_velocity_mps",
            "angular_velocity_rps",
        ):
            value = getattr(self, name)
            if value is None and not self.valid:
                continue
            converted = (
                quaternion(value) if name == "orientation_wxyz" else vector(value, 3)
            )
            object.__setattr__(self, name, converted)


@dataclass(frozen=True)
class JointState(Record):
    joint_id: str
    kind: str
    position: float | None
    velocity: float | None
    measured_effort: float | None
    valid: bool

    def __post_init__(self):
        if self.kind not in {"revolute", "prismatic"} or type(self.valid) is not bool:
            raise CellIOError("SIM_STATE_INVALID", self.joint_id)
        for name in ("position", "velocity", "measured_effort"):
            v = getattr(self, name)
            if v is None and (not self.valid or name == "measured_effort"):
                continue
            if type(v) not in (int, float) or not isfinite(v):
                raise CellIOError("SIM_STATE_INVALID", f"{self.joint_id}.{name}")


@dataclass(frozen=True)
class TruthSnapshot(Record):
    tick: Tick
    bodies: Mapping[str, BodyState]
    joints: Mapping[str, JointState]
    contacts: tuple[tuple[str, str], ...]
    plant_state: Mapping[str, Scalar]
    complete: bool


@dataclass(frozen=True)
class SignalSample(Record):
    signal_id: str
    dtype: str
    value: Scalar | None
    quality: Quality
    source_step: int | None
    source_seq: int | None
    publish_step: int | None
    deliver_step: int | None
    transport_seq: int | None
    generation: int
    reason: str | None

    def __post_init__(self):
        dtype = {"bool": bool, "int": int, "float": float}.get(self.dtype)
        if dtype is None:
            raise CellIOError("SIGNAL_DTYPE", self.dtype)
        quality = Quality(self.quality)
        object.__setattr__(self, "quality", quality)
        if quality == Quality.GOOD:
            if type(self.value) is not dtype or (
                dtype is float and not isfinite(self.value)
            ):
                raise CellIOError("SIGNAL_DTYPE", self.signal_id)
            if any(
                v is None
                for v in (self.source_step, self.source_seq, self.publish_step)
            ):
                raise CellIOError("SIGNAL_TIME", "GOOD packet lacks source metadata")
        elif self.value is not None:
            raise CellIOError("SIGNAL_DTYPE", "invalid sample must have a null value")
        for name in (
            "source_step",
            "source_seq",
            "publish_step",
            "deliver_step",
            "transport_seq",
            "generation",
        ):
            v = getattr(self, name)
            if v is not None:
                uint(v, name)
                if quality == Quality.MISSING and name != "generation":
                    raise CellIOError(
                        "SIGNAL_TIME", "MISSING has no timestamps or packet identity"
                    )
        if (self.deliver_step is None) != (self.transport_seq is None):
            raise CellIOError("SIGNAL_TIME", "delivery identity incomplete")

    @classmethod
    def missing(cls, signal_id, dtype, generation):
        return cls(
            signal_id,
            dtype,
            None,
            Quality.MISSING,
            None,
            None,
            None,
            None,
            None,
            generation,
            None,
        )


@dataclass(frozen=True)
class PublicCommandReceipt(Record):
    command_id: str
    operation_epoch: int
    actuator_id: str
    status: str
    receipt_step: int
    reason: str | None


@dataclass(frozen=True)
class ControllerView(Record):
    tick: Tick
    signals: Mapping[str, SignalSample]
    previous_command_receipts: tuple[PublicCommandReceipt, ...]
    active_operation_epoch: int


@dataclass(frozen=True)
class CommandIntent(Record):
    command_id: str
    sequence: int
    episode_id: str
    generation: int
    operation_epoch: int
    issued_step: int
    actuator_id: str
    kind: CommandKind
    value: Target
    job_id: int


@dataclass(frozen=True)
class AppliedCommand(Record):
    command_id: str
    actuator_id: str
    generation: int
    issued_step: int
    due_step: int
    applied_step: int | None
    status: str
    reason: str | None
    effective_value: Target | None


@dataclass(frozen=True)
class DeliveryDecision(Record):
    intent: CommandIntent
    channel_generation: int
    due_step: int
    delivery_step: int | None
    status: str
    reason: str | None


@dataclass(frozen=True)
class ActuationReceipt(Record):
    tick: Tick
    accepted: bool
    applied_commands: tuple[AppliedCommand, ...]
    targets_by_actuator: Mapping[str, Target]
    active_override_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdvanceReceipt(Record):
    from_step: int
    to_step: int
    engine_time_before_s: float
    engine_time_after_s: float
    success: bool
    rendered: bool


@dataclass(frozen=True)
class Event(Record):
    event_id: str
    event_type: str
    episode_id: str
    generation: int
    step: int
    phase: int
    local_sequence: int
    available_step: int
    entity_id: str | None
    payload: Mapping


@dataclass(frozen=True)
class FaultSpec(Record):
    fault_id: str
    family: str
    model_id: str
    target_id: str
    trigger: Mapping
    arm: Mapping
    offset_steps: int
    duration_steps: int
    parameters: Mapping
    require_exercised: bool = True


@dataclass(frozen=True)
class ControllerOutput(Record):
    new_commands: tuple[CommandIntent, ...]
    emitted_events: tuple[Mapping, ...]
    stage_id: str
    job_id: int
    active_operation_epoch: int
    mission_status: str
    reason: str | None = None


@dataclass(frozen=True)
class TickEvidenceFrame(Record):
    tick: Tick
    truth: TruthSnapshot
    raw_signals: Mapping[str, SignalSample]
    observed_signals: Mapping[str, SignalSample]
    controller_called: bool
    controller_view_hash: str | None
    controller_output: Mapping | None
    intents: tuple[CommandIntent, ...]
    deliveries: tuple[DeliveryDecision, ...]
    actuation: ActuationReceipt | None
    source_events: tuple[Mapping, ...]
    fault_transitions: tuple[Mapping, ...]
    fault_audit: Mapping
    previous_advance: AdvanceReceipt | None
    final_snapshot: bool


@dataclass(frozen=True)
class RequirementResult(Record):
    requirement_id: str
    verdict: Verdict
    first_witness_step: int | None
    last_evaluated_step: int
    obligation_count: int
    violated_count: int
    unresolved_count: int
    reason: str


@dataclass(frozen=True)
class ErrorRecord(Record):
    code: str
    severity: str
    module: str
    step: int | None
    phase: int | None
    message: str
    context: Mapping
    exception_type: str | None = None
    traceback_path: str | None = None


@dataclass(frozen=True)
class EpisodeResult(Record):
    run_id: str
    attempt_id: str
    completion_status: str
    task_outcome: str
    compliance_verdict: Verdict
    case_verdict: str
    end_step: int | None
    faults: tuple[Mapping, ...]
    requirements: tuple[RequirementResult, ...]
    errors: tuple[ErrorRecord, ...]
    artifact_digest: str | None


@dataclass(frozen=True)
class PerformanceSample(Record):
    step: int
    section: str
    wall_ns: int
    process_rss_bytes: int | None
    gpu_memory_bytes: int | None
    queue_depth: int
