"""Researcher-authored, scene-specific comparison scripts for observation faults.

The reference lifecycle/effect recorder is shared. The applied observation edit,
delayed packet storage and manual stage schedule below are independent of the
modular fault operators. This comparison covers sensor-side F1, F2, F3 and F5.
"""

from collections import deque
from dataclasses import replace

from cellio.core.faults import FaultEngine
from cellio.core.models import CellIOError, Quality, SignalSample, freeze


class ManualObservationFault(FaultEngine):
    @property
    def observation_pending_count(self):
        return len(self.manual_queue)

    def __init__(self, spec, horizon, cell):
        if not spec or spec["family"] not in ("F1", "F2", "F3", "F5"):
            raise CellIOError(
                "CFG_MANUAL_BASELINE",
                "manual scripts cover F1/F2/F3/F5 observation faults",
            )
        self.cell = cell
        super().__init__(spec, horizon, cell)
        family = self.spec.family
        if cell == "cell_a":
            self.target = "belt.stopped" if family == "F1" else "gripper.closed"
            self.stage = "wait_stop" if family == "F1" else "grasp"
            self.duration = 480
        else:
            self.target = "robot.clear_fixture" if family == "F1" else "clamp.closed"
            self.stage = "fixture_retreat" if family == "F1" else "clamp_close"
            self.duration = 1200 if family == "F1" else 480
        if (
            self.spec.target_id != self.target
            or self.spec.duration_steps != self.duration
            or self.spec.offset_steps != 0
            or self.spec.arm != {"open_step": 0, "close_step": 4800}
            or self.spec.trigger
            != {
                "kind": "controller_event",
                "event_type": "controller.stage_enter",
                "event_filter": {"stage_id": self.stage},
            }
            or (family == "F3" and self.spec.parameters["delay_steps"] != 24)
        ):
            raise CellIOError(
                "CFG_MANUAL_BASELINE",
                "manual script requires canonical stage, duration and target",
            )

    def reset(self, episode_id, generation):
        super().reset(episode_id, generation)
        self.manual_queue = deque()

    def reset_evidence(self):
        values = super().reset_evidence()
        values["observation_queue_empty"] &= not self.manual_queue
        return values

    def advance(
        self,
        tick,
        events=(),
        predicates=None,
        driver_positions=None,
        actuator_coordinate=None,
    ):
        k = tick.step
        if self.episode_id is None:
            self.episode_id = tick.episode_id
        if (
            k != self._next
            or tick.episode_id != self.episode_id
            or tick.generation != self.generation
        ):
            raise CellIOError(
                "CLOCK_ORDER", "manual script expects adjacent same-generation ticks"
            )
        self._next += 1
        changes = []
        s = self.state
        if self.active and k == s.scheduled_end:
            self.channel_generation += 1
            while self.manual_queue:
                due, index, packet = self.manual_queue.popleft()
                changes.append(
                    {
                        "kind": "RESTORE_FLUSH",
                        "enqueue_seq": index,
                        "due_step": due,
                        "source_seq": packet.source_seq,
                    }
                )
                s.flushed_packet_count += 1
            s.actual_end = k
            s.application_status = "ENDED"
            s.restoration_status = "UNOBSERVED"
            changes.append({"kind": "ENDED"})
        if (
            s.actual_start is None
            and k < 4800
            and any(
                e.event_type == "controller.stage_enter"
                and e.available_step == k
                and e.step + 1 == k
                and e.generation == tick.generation
                and e.episode_id == tick.episode_id
                and e.payload.get("stage_id") == self.stage
                for e in events
            )
        ):
            s.trigger_match_step = s.scheduled_start = s.actual_start = k
            s.scheduled_end = k + self.duration
            s.application_status = "ACTIVE"
            self.channel_generation += 1
            changes.extend(({"kind": "SCHEDULED"}, {"kind": "ACTIVE"}))
        self.transitions = tuple(
            freeze({"fault_id": self.spec.fault_id, "step": k, **e}) for e in changes
        )
        return self.transitions

    def transform_observations(self, raw, tick):
        k = tick.step
        if self._sample_step >= k or self._next != k + 1:
            raise CellIOError("CLOCK_ORDER", "invalid manual observation phase")
        self._sample_step = k
        observed = {}
        for key, sample in raw.items():
            if key != self.target or not self.active:
                observed[key] = self._deliver(sample, k)
                continue
            family = self.spec.family
            if family == "F1":
                changed = (
                    replace(sample, value=self.spec.parameters["value"])
                    if sample.quality == Quality.GOOD
                    else sample
                )
                observed[key] = self._deliver(changed, k)
            elif family == "F2":
                observed[key] = (
                    self._deliver(sample, k)
                    if k == self.state.actual_start
                    else self._cache[key]
                )
            elif family == "F3":
                self.manual_queue.append((k + 24, self._enqueued, sample))
                self._enqueued += 1
                if self.manual_queue[0][0] == k:
                    observed[key] = self._deliver(self.manual_queue.popleft()[2], k)
                    self.state.delayed_packet_count += 1
                else:
                    observed[key] = self._cache.get(
                        key, SignalSample.missing(key, sample.dtype, tick.generation)
                    )
            else:
                dropped = replace(
                    sample,
                    quality=Quality.BAD,
                    value=None,
                    source_step=None,
                    source_seq=None,
                    publish_step=k,
                    reason="NO_DATA",
                )
                observed[key] = self._deliver(dropped, k)
        self._cache.update(observed)
        return freeze(observed)
