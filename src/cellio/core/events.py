from .models import CellIOError, Event


class EventBus:
    def __init__(self):
        self._pending = {}
        self._previous = {}
        self._seen = set()
        self.history = []

    def reset(self):
        self.__init__()

    def _event(self, tick, phase, sequence, event_type, payload, entity_id=None):
        event_id = f"{tick.episode_id}:{tick.generation}:{tick.step}:{phase}:{sequence}"
        if event_id in self._seen:
            raise CellIOError("EVENT_DUPLICATE", event_id)
        self._seen.add(event_id)
        event = Event(
            event_id,
            event_type,
            tick.episode_id,
            tick.generation,
            tick.step,
            phase,
            sequence,
            tick.step + int(phase == 4),
            entity_id,
            payload,
        )
        self.history.append(event)
        return event

    def world_edges(self, tick, levels, *, fire_on_initial=()):
        events = []
        for key in sorted(levels):
            current = levels[key]
            if current is not None and type(current) is not bool:
                raise CellIOError("SIM_STATE_INVALID", key)
            previous = self._previous.get(key)
            if current and (
                previous is False or tick.step == 0 and key in fire_on_initial
            ):
                events.append(
                    self._event(tick, 2, len(events), key, {"predicate_id": key})
                )
            self._previous[key] = current
        return tuple(events)

    def commit_controller(self, tick, emissions):
        events = tuple(
            self._event(
                tick, 4, i, e["event_type"], e.get("payload", {}), e.get("entity_id")
            )
            for i, e in enumerate(emissions)
        )
        if events:
            self._pending.setdefault(tick.step + 1, []).extend(events)
        return events

    def pop_ready_for_fault_trigger(self, step):
        if any(k < step for k in self._pending):
            raise CellIOError("CLOCK_ORDER", "missed controller event delivery")
        return tuple(self._pending.pop(step, ()))

    @staticmethod
    def merge_unique(world, controller):
        events = (*sorted(world, key=lambda e: e.event_id), *controller)
        if len({e.event_id for e in events}) != len(events):
            raise CellIOError("EVENT_DUPLICATE", "duplicate trigger input")
        return events
