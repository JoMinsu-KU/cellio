from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from inspect import getsource
from math import isfinite

from cellio.io import digest

from ..models import (
    CellIOError,
    Event,
    Quality,
    SignalSample,
    Verdict,
    freeze,
    json_value,
)
from ..signals import usable
from .spec import compile_fault

RECOVERY_WINDOW_STEPS = 12


@dataclass
class FaultRuntime:
    trigger_match_step: int | None = None
    scheduled_start: int | None = None
    actual_start: int | None = None
    scheduled_end: int | None = None
    actual_end: int | None = None
    application_status: str = "ARMED"
    effect_status: str = "NOT_OBSERVED"
    restoration_status: str = "NOT_DUE"
    boundary_effect_status: str = "NOT_OBSERVED"
    physical_hold_verdict: Verdict = Verdict.NOT_APPLICABLE
    functional_reuse_status: str = "UNOBSERVED"
    coverage: str = "NOT_EXERCISED"
    first_effect_step: int | None = None
    controller_exposure_count: int = 0
    value_discrepancy_count: int = 0
    age_discrepancy_count: int = 0
    quality_discrepancy_count: int = 0
    delayed_packet_count: int = 0
    flushed_packet_count: int = 0
    reason: str | None = None
    evidence_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class CompiledFaultPlan:
    fault_id: str
    model_id: str
    realization_rule_id: str
    realization_rule_sha256: str
    resolved_resource: Mapping
    queue_limit: int
    recovery_window_steps: int


@lru_cache(maxsize=32)
def _rule_hash(engine_type, family, model):
    functions = [engine_type.observe, engine_type._realized]
    if family == "F6":
        functions.extend((engine_type._observe_hold, engine_type._record_hold_sample))
    elif family != "F4":
        functions.extend((engine_type._observe_signals, usable))
    return digest(
        {"model_id": model, "probe_sources": [getsource(f) for f in functions]}
    )


@dataclass(frozen=True)
class ScheduledObservation:
    episode_id: str
    episode_generation: int
    signal_id: str
    channel_generation: int
    enqueue_seq: int
    due_step: int
    sample: SignalSample


class ObservationDelayQueue:
    def __init__(self, capacity):
        self.capacity = capacity
        self.items = deque()

    def enqueue(self, packet):
        if len(self.items) >= self.capacity:
            raise CellIOError("QUEUE_OVERFLOW", packet.signal_id)
        if self.items and (packet.due_step, packet.enqueue_seq) <= (
            self.items[-1].due_step,
            self.items[-1].enqueue_seq,
        ):
            raise CellIOError("QUEUE_ORDER", packet.signal_id)
        self.items.append(packet)

    def due(self, tick, channel_generation):
        if not self.items or self.items[0].due_step > tick.step:
            return None
        packet = self.items.popleft()
        if (
            packet.due_step,
            packet.episode_id,
            packet.episode_generation,
            packet.channel_generation,
        ) != (tick.step, tick.episode_id, tick.generation, channel_generation):
            raise CellIOError("QUEUE_STALE", packet.signal_id)
        return packet


class FaultEngine:
    def __init__(self, spec, horizon_steps=7200, cell_id="cell_a", **registry):
        self.horizon = horizon_steps
        self.spec = (
            None
            if spec is None
            else compile_fault(spec, horizon_steps, cell_id, **registry)
        )
        self.reset(None, 0)

    def reset(self, episode_id, generation):
        self.episode_id, self.generation = episode_id, generation
        self.state = FaultRuntime()
        self.channel_generation = 0
        self._next = 0
        self._sample_step = -1
        self._previous_predicate = None
        self._transport_sequence = defaultdict(int)
        self._cache = {}
        self._enqueued = 0
        self._observation_queue = ObservationDelayQueue(
            self.spec.parameters["delay_steps"] + 2
            if self.spec and self.spec.family == "F3"
            else 0
        )
        self._hold = {}
        self._captured_coordinate = None
        self._conflict_start = None
        self._last_hold_step = None
        self._previous_conflict = False
        self._inspected_step = -1
        self.transitions = ()

    @property
    def active(self):
        return self.spec is not None and self.state.application_status == "ACTIVE"

    @property
    def last_inspected_step(self):
        return self._inspected_step

    @property
    def observation_pending_count(self):
        if self.spec is None or self.spec.family != "F3":
            return None
        return len(self._observation_queue.items)

    def reset_evidence(self):
        return {
            "observation_queue_empty": not self._observation_queue.items
            and not self._cache,
            "fault_reset": not self.active
            and not self._hold
            and self.state.scheduled_start is None,
        }

    def compiled_plan(self, registry, command_queue_limit, driver_ids=()):
        if self.spec is None:
            return None
        spec = self.spec
        command = spec.family in {"F4", "F6"}
        group = "actuators" if command else "signals"
        resource = {
            "kind": group,
            "id": spec.target_id,
            "type": registry[group][spec.target_id],
        }
        if spec.family == "F6":
            resource["driver_ids"] = sorted(driver_ids)
        limit = (
            self._observation_queue.capacity
            if spec.family == "F3"
            else command_queue_limit
            if spec.family == "F4"
            else 0
        )
        return CompiledFaultPlan(
            spec.fault_id,
            spec.model_id,
            f"{spec.model_id}.realization.v1",
            _rule_hash(type(self), spec.family, spec.model_id),
            freeze(resource),
            limit,
            RECOVERY_WINDOW_STEPS,
        )

    def active_for(self, family, target=None):
        return (
            self.active
            and self.spec.family == family
            and (target is None or self.spec.target_id == target)
        )

    def ended_at(self, family, step):
        return (
            self.spec is not None
            and self.spec.family == family
            and self.state.actual_end == step
        )

    def _matches(self, tick, events, predicates):
        tr = self.spec.trigger
        kind = tr["kind"]
        if kind == "at_step":
            return tick.step == tr["at_step"]
        if kind == "controller_event":
            for e in events:
                event = json_value(e) if isinstance(e, Event) else e
                if (
                    event["episode_id"],
                    event["generation"],
                    event["available_step"],
                ) != (
                    tick.episode_id,
                    tick.generation,
                    tick.step,
                ):
                    raise CellIOError("EVENT_STALE", event["event_id"])
                if (
                    event["phase"] == 4
                    and event["step"] + 1 == tick.step
                    and event["event_type"] == tr["event_type"]
                    and all(
                        k in event["payload"]
                        and type(event["payload"][k]) is type(v)
                        and event["payload"][k] == v
                        for k, v in tr["event_filter"].items()
                    )
                ):
                    return True
            return False
        current = predicates.get(tr["predicate_id"])
        if current is not None and type(current) is not bool:
            raise CellIOError("SIM_STATE_INVALID", tr["predicate_id"])
        previous, self._previous_predicate = self._previous_predicate, current
        return current is True and (
            kind == "world_level"
            or previous is False
            or tick.step == 0
            and tr.get("fire_on_initial", False)
        )

    def advance(
        self,
        tick,
        events=(),
        predicates=None,
        driver_positions=None,
        actuator_coordinate=None,
    ):
        if self.episode_id is None:
            self.episode_id = tick.episode_id
        if (self.episode_id, self.generation, self._next) != (
            tick.episode_id,
            tick.generation,
            tick.step,
        ) or tick.step >= self.horizon:
            raise CellIOError(
                "CLOCK_ORDER", "fault scheduler expected next episode tick"
            )
        self._next += 1
        changes = []
        self.transitions = ()
        if self.spec is None:
            return self.transitions
        s = self.state
        k = tick.step
        if self.active and k == s.scheduled_end:
            self.channel_generation += 1
            while self._observation_queue.items:
                p = self._observation_queue.items.popleft()
                changes.append(
                    {
                        "kind": "RESTORE_FLUSH",
                        "step": k,
                        "enqueue_seq": p.enqueue_seq,
                        "due_step": p.due_step,
                        "source_seq": p.sample.source_seq,
                    }
                )
                s.flushed_packet_count += 1
            s.actual_end = k
            s.application_status = "ENDED"
            s.restoration_status = "UNOBSERVED"
            changes.append({"kind": "ENDED", "step": k})
        if s.trigger_match_step is None:
            match = self._matches(tick, events, predicates or {})
            if match and self.spec.arm["open_step"] <= k < self.spec.arm["close_step"]:
                s.trigger_match_step = k
                s.scheduled_start = k + self.spec.offset_steps
                s.scheduled_end = s.scheduled_start + self.spec.duration_steps
                s.application_status = "SCHEDULED"
                changes.append({"kind": "SCHEDULED", "step": k})
        if s.application_status == "SCHEDULED" and s.scheduled_start == k:
            if self.spec.family == "F6":
                if (
                    not isinstance(driver_positions, Mapping)
                    or not driver_positions
                    or any(
                        type(v) not in (int, float) or not isfinite(v)
                        for v in driver_positions.values()
                    )
                ):
                    raise CellIOError(
                        "SIM_STATE_INVALID",
                        "F6 requires measured independent driver positions",
                    )
                if type(actuator_coordinate) not in (int, float) or not isfinite(
                    actuator_coordinate
                ):
                    raise CellIOError(
                        "SIM_STATE_INVALID",
                        "F6 requires measured full gap or clamp position",
                    )
                self._hold = dict(driver_positions)
                self._captured_coordinate = actuator_coordinate
            self.channel_generation += 1
            s.actual_start, s.application_status = k, "ACTIVE"
            changes.append({"kind": "ACTIVE", "step": k})
        self.transitions = tuple(
            freeze({"fault_id": self.spec.fault_id, **e}) for e in changes
        )
        return self.transitions

    def _deliver(self, raw, k):
        if raw.quality == Quality.MISSING:
            return raw
        seq = self._transport_sequence[raw.signal_id]
        self._transport_sequence[raw.signal_id] += 1
        return replace(raw, deliver_step=k, transport_seq=seq)

    def transform_observations(self, raw, tick):
        k = tick.step
        if self._next != k + 1 or k <= self._sample_step:
            raise CellIOError(
                "CLOCK_ORDER", "observation transform requires a new scheduled tick"
            )
        self._sample_step = k
        result = {}
        for key, sample in raw.items():
            if (key, sample.generation) != (
                sample.signal_id,
                tick.generation,
            ) or sample.deliver_step is not None:
                raise CellIOError("SIGNAL_STALE", key)
            if self.active_for("F1", key) and sample.quality == Quality.GOOD:
                packet = self._deliver(
                    replace(sample, value=self.spec.parameters["value"]), k
                )
            elif self.active_for("F2", key) and k > self.state.actual_start:
                packet = self._cache[key]
            elif self.active_for("F3", key):
                due = k + self.spec.parameters["delay_steps"]
                self._observation_queue.enqueue(
                    ScheduledObservation(
                        tick.episode_id,
                        tick.generation,
                        key,
                        self.channel_generation,
                        self._enqueued,
                        due,
                        sample,
                    )
                )
                self._enqueued += 1
                pending = self._observation_queue.due(tick, self.channel_generation)
                if pending is not None:
                    packet = self._deliver(pending.sample, k)
                    self.state.delayed_packet_count += 1
                else:
                    packet = self._cache.get(key) or SignalSample.missing(
                        key, sample.dtype, tick.generation
                    )
            elif self.active_for("F5", key):
                bad = replace(
                    sample,
                    quality=Quality.BAD,
                    value=None,
                    source_step=None,
                    source_seq=None,
                    publish_step=k,
                    reason="NO_DATA",
                )
                packet = self._deliver(bad, k)
            else:
                packet = self._deliver(sample, k)
            result[key] = packet
        self._cache.update(result)
        return freeze(result)

    def actuator_overrides(self):
        if not self.active_for("F6"):
            return freeze({})
        return freeze(
            {
                self.spec.target_id: {
                    "fault_id": self.spec.fault_id,
                    "driver_positions": self._hold,
                    "velocity_target": 0.0,
                }
            }
        )

    def _realized(self, k, path):
        if self.state.first_effect_step is None:
            self.state.first_effect_step = k
            self.state.evidence_refs.append(
                {"episode_id": self.episode_id, "step": k, "field": path}
            )
        self.state.effect_status = self.state.boundary_effect_status = "REALIZED"
        self.state.coverage = "EXERCISED"

    def _observe_signals(self, k, raw, observed, called):
        key = self.spec.target_id
        a, b = raw[key], observed[key]
        if self.active:
            value = a.value != b.value
            quality = a.quality != b.quality
            age = (
                a.source_step is not None
                and b.source_step is not None
                and b.source_step < a.source_step
            )
            self.state.value_discrepancy_count += int(value)
            self.state.quality_discrepancy_count += int(quality)
            self.state.age_discrepancy_count += int(age)
            realized = {
                "F1": value and a.quality == Quality.GOOD,
                "F2": age,
                "F3": age or (usable(a, k) and not usable(b, k)),
                "F5": usable(a, k) and b.quality == Quality.BAD,
            }
            if realized[self.spec.family]:
                self._realized(k, f"observed_signals.{key}")
                self.state.controller_exposure_count += int(called)
        elif (
            self.state.actual_end is not None
            and k <= self.state.actual_end + RECOVERY_WINDOW_STEPS
        ):
            if (
                b.source_step == a.source_step == k
                and b.deliver_step == k
                and b.value == a.value
                and b.quality == a.quality
                and b.source_seq == a.source_seq
                and not self._observation_queue.items
            ):
                self.state.restoration_status = "PATH_RESTORED"

    def _observe_hold(
        self,
        k,
        receipt,
        actual_positions,
        nominal_coordinate,
        nominal_targets,
        applied_targets,
    ):
        # S_k is evidence about the *previous* command interval. Only bridge adjacent samples.
        measured = actual_positions or {}
        valid_sample = (
            bool(self._hold)
            and set(measured) == set(self._hold)
            and all(type(v) in (int, float) and isfinite(v) for v in measured.values())
        )
        if self._previous_conflict:
            if not valid_sample:
                self._conflict_start = self._last_hold_step = None
            else:
                self._record_hold_sample(k, measured)
        conflict = (
            self.active
            and nominal_coordinate is not None
            and abs(nominal_coordinate - self._captured_coordinate)
            >= self.spec.parameters["conflicting_travel_min_m"]
        )
        if conflict and self.state.physical_hold_verdict == Verdict.NOT_APPLICABLE:
            self.state.physical_hold_verdict = Verdict.UNKNOWN
        if conflict and not self._previous_conflict and valid_sample:
            self._record_hold_sample(k, measured)
        if not conflict:
            self._conflict_start = None
        self._previous_conflict = conflict
        valid_targets = (
            bool(self._hold)
            and isinstance(nominal_targets, Mapping)
            and isinstance(applied_targets, Mapping)
            and set(nominal_targets) == set(self._hold) == set(applied_targets)
            and all(
                type(v) in (int, float) and isfinite(v)
                for targets in (nominal_targets, applied_targets)
                for v in targets.values()
            )
        )
        if (
            self.active
            and receipt is not None
            and receipt.accepted
            and self.spec.fault_id in receipt.active_override_ids
            and valid_targets
            and any(abs(nominal_targets[j] - v) > 1e-6 for j, v in self._hold.items())
            and all(abs(applied_targets[j] - v) <= 1e-6 for j, v in self._hold.items())
        ):
            self._realized(k, "actuation.targets_by_actuator")
        if (
            self.state.actual_end is not None
            and k <= self.state.actual_end + RECOVERY_WINDOW_STEPS
            and receipt is not None
            and receipt.accepted
            and self.spec.fault_id not in receipt.active_override_ids
            and self.spec.target_id in receipt.targets_by_actuator
            and valid_targets
            and all(
                abs(applied_targets[j] - v) <= 1e-6 for j, v in nominal_targets.items()
            )
        ):
            self.state.restoration_status = "PATH_RESTORED"

    def _record_hold_sample(self, k, positions):
        verdict = self.state.physical_hold_verdict
        if verdict == Verdict.FAIL:
            return
        if any(
            abs(positions[j] - v) > self.spec.parameters["hold_tolerance_m"]
            for j, v in self._hold.items()
        ):
            self.state.physical_hold_verdict = Verdict.FAIL
            self.state.evidence_refs.append(
                {"episode_id": self.episode_id, "step": k, "field": "truth.joints"}
            )
            return
        if self._conflict_start is None or self._last_hold_step not in (k - 1, k):
            self._conflict_start = k
        self._last_hold_step = k
        if k - self._conflict_start >= 30:
            if verdict != Verdict.PASS:
                self.state.evidence_refs.append(
                    {"episode_id": self.episode_id, "step": k, "field": "truth.joints"}
                )
            self.state.physical_hold_verdict = Verdict.PASS
        elif verdict != Verdict.PASS:
            self.state.physical_hold_verdict = Verdict.UNKNOWN

    def observe(
        self,
        frame,
        *,
        effective_before=None,
        effective_after=None,
        actual_positions=None,
        nominal_coordinate=None,
        nominal_targets=None,
        applied_targets=None,
    ):
        """P7 probe. F4 effective IDs are post-router, F6 target values are backend readbacks."""
        if self.spec is None:
            return freeze({})
        k = frame.tick.step
        if self._inspected_step >= k:
            raise CellIOError("CLOCK_ORDER", "evidence already evaluated")
        self._inspected_step = k
        family, key, s = self.spec.family, self.spec.target_id, self.state
        if not frame.final_snapshot and family in {"F1", "F2", "F3", "F5"}:
            self._observe_signals(
                k, frame.raw_signals, frame.observed_signals, frame.controller_called
            )
        if family == "F4" and frame.actuation and frame.actuation.accepted:
            queued = [
                d
                for d in frame.deliveries
                if d.intent.actuator_id == key and d.status == "QUEUED"
            ]
            delivered = [
                d
                for d in frame.deliveries
                if d.intent.actuator_id == key and d.status == "DELIVERED"
            ]
            if (
                self.active
                and queued
                and effective_before is not None
                and effective_after is not None
            ):
                if (
                    effective_before.get(key) == effective_after.get(key)
                    and key in frame.actuation.targets_by_actuator
                ):
                    self._realized(k, "deliveries/actuation")
            s.delayed_packet_count += sum(
                d.delivery_step > d.intent.issued_step for d in delivered
            )
            s.flushed_packet_count += sum(
                d.reason == "RESTORE_FLUSH" for d in frame.deliveries
            )
            if (
                s.actual_end is not None
                and s.actual_end <= k <= s.actual_end + RECOVERY_WINDOW_STEPS
                and key in frame.actuation.targets_by_actuator
            ):
                s.restoration_status = "PATH_RESTORED"
                if delivered:
                    s.functional_reuse_status = "OBSERVED"
        if family == "F6":
            self._observe_hold(
                k,
                frame.actuation,
                actual_positions,
                nominal_coordinate,
                nominal_targets,
                applied_targets,
            )
        return self.report()

    def finalize(self, step, *, complete_horizon=True):
        if self.spec is None:
            return freeze({})
        s = self.state
        if not complete_horizon:
            if s.application_status not in {"ENDED", "ERROR"}:
                s.application_status = "CANCELLED"
                s.reason = "EPISODE_INTERRUPTED"
                if s.effect_status != "REALIZED":
                    s.effect_status, s.coverage = "UNKNOWN", "CENSORED"
        elif s.trigger_match_step is None:
            s.application_status, s.reason = "UNREACHED", "TRIGGER_UNREACHED"
        elif s.actual_start is None:
            s.reason, s.coverage = "START_CENSORED", "CENSORED"
        elif s.actual_end is None:
            s.restoration_status = "UNOBSERVED"
            if s.scheduled_end == step:
                s.actual_end, s.application_status = step, "ENDED"
            else:
                s.reason, s.coverage = "END_CENSORED", "CENSORED"
        if s.effect_status == "NOT_OBSERVED" and s.actual_start is not None:
            s.effect_status = "UNKNOWN" if s.coverage == "CENSORED" else "NOT_REALIZED"
        return self.report()

    def report(self):
        if self.spec is None:
            return freeze({})
        return freeze(
            {
                "fault_id": self.spec.fault_id,
                "family": self.spec.family,
                "target_id": self.spec.target_id,
                **json_value(self.state),
            }
        )
