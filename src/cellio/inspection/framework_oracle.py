"""Evaluate Cell C measurements carried in the common episode evidence frames."""

from collections.abc import Mapping

from cellio.core.models import RequirementResult, Verdict

from .replay import (
    BINS,
    EVENTS,
    PARTS,
    _dimension_bin,
    _number,
    _observed_bin,
    _vector,
    replay_witness,
)

MEASURE = "cell_c.r01.measurement"
OWNER = "cell_c.r02.result_owner"
FOLLOW = "cell_c.r03.command_followed"
DEST = "cell_c.r04.destination"
SEQUENCE = "cell_c.r14.sequence"
RULES = (MEASURE, OWNER, FOLLOW, DEST, SEQUENCE)
ROUTE = "inspection.route_ok"
TAG = "inspection.source_p2"
ORDER = tuple(
    (part, name)
    for part in PARTS
    for name in (EVENTS + ("external_takeaway",) if part == "P1" else EVENTS)
)
TRANSFER_STATIONS = {1: ("infeed",), 2: ("fixture",), 4: ("fixture",), 5: tuple(BINS)}


def requirement_presets(horizon):
    return tuple(
        {
            "id": key,
            "revision": 1,
            "category": "task" if key == SEQUENCE else "compliance",
            "horizon_steps": horizon,
        }
        for key in RULES
    )


def pose_state(output):
    values = dict(
        zip(
            ("position_x_m", "position_y_m", "position_z_m"),
            _vector(output["position_m"], 3),
        )
    )
    values.update(
        zip(
            ("orientation_w", "orientation_x", "orientation_y", "orientation_z"),
            _vector(output["orientation_wxyz"], 4),
        )
    )
    values["linear_speed_m_s"] = _number(output["linear_speed_m_s"])
    values["angular_speed_rad_s"] = _number(output["angular_speed_rad_s"])
    return values


def output_pose(state):
    return {
        "position_m": [state[f"position_{axis}_m"] for axis in "xyz"],
        "orientation_wxyz": [state[f"orientation_{axis}"] for axis in "wxyz"],
        "linear_speed_m_s": state["linear_speed_m_s"],
        "angular_speed_rad_s": state["angular_speed_rad_s"],
    }


def witness_observations(witness):
    """Map existing 30 Hz native event times to their corresponding 120 Hz boundaries."""
    replay_witness(
        witness
    )  # Check provenance, ordering, ranges and internal consistency.
    pieces = {piece["part"]: piece for piece in witness["parts"]}
    for event in witness["events"]:
        state = {k: event[k] for k in ("event", "part", "station") if k in event}
        piece = pieces[event["part"]]
        if event["event"] == "inspection":
            state.update(
                {
                    k: piece[k]
                    for k in (
                        "probe_height_mm",
                        "geometry_height_mm",
                        "result_part_id",
                        "commanded_bin",
                    )
                }
            )
        elif event["event"] == "output_settled":
            state.update(pose_state(piece["physical_output"]))
        yield event["frame"] * 4, state


def _get(item, key):
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def _decision(frame, state):
    if "result_part_id" in state or "commanded_bin" in state:
        # Archived observations preserve the original, independently recorded decision.
        return state.get("result_part_id"), state.get("commanded_bin")
    receipt = _get(frame, "actuation")
    applied = {
        _get(command, "actuator_id"): _get(command, "effective_value")
        for command in (_get(receipt, "applied_commands") or ())
        if _get(command, "status") == "APPLIED"
    }
    owner = ("P2" if applied[TAG] else "P1") if type(applied.get(TAG)) is bool else None
    route = (
        ("OK" if applied[ROUTE] else "NG") if type(applied.get(ROUTE)) is bool else None
    )
    return owner, route


class InspectionOracle:
    def __init__(self, horizon):
        self.horizon = horizon

    def reset(self, requirements, generation):
        if tuple(r["id"] for r in requirements) != RULES or tuple(
            requirements
        ) != requirement_presets(self.horizon):
            raise ValueError("Unsupported Cell C inspection requirement revision")
        self.generation = generation
        self.parts = {}
        self.events = []
        self.failed_at = {}
        self.count = dict.fromkeys(RULES, 0)
        self.failed_count = dict.fromkeys(RULES, 0)
        self.completed_at = {}
        self.last = -1

    def reset_evidence(self):
        return not self.events and self.last == -1

    def evidence_summary(self):
        return {"parts": [self.parts[p] for p in PARTS if p in self.parts]}

    def _check(self, rule, step, passed):
        self.count[rule] += 1
        if not passed:
            self.failed_at.setdefault(rule, step)
            self.failed_count[rule] += 1
        needed = 1 if rule == SEQUENCE else 2
        if self.count[rule] == needed:
            self.completed_at[rule] = step

    def _result(self, rule, complete=False):
        count = self.count[rule]
        needed = 1 if rule == SEQUENCE else 2
        if rule in self.failed_at:
            verdict, at, reason = (
                Verdict.FAIL,
                self.failed_at[rule],
                "evidence violates requirement",
            )
        elif count == needed:
            verdict, at, reason = (
                Verdict.PASS,
                self.completed_at[rule],
                "all required observations satisfied",
            )
        elif complete and rule == SEQUENCE:
            verdict, at, reason = (
                Verdict.FAIL,
                self.last,
                "two-part sequence incomplete at horizon",
            )
            return RequirementResult(rule, verdict, at, self.last, 1, 1, 0, reason)
        else:
            verdict, at, reason = (
                Verdict.UNKNOWN,
                None,
                "waiting for required observations",
            )
        return RequirementResult(
            rule,
            verdict,
            at,
            self.last,
            count,
            self.failed_count[rule],
            max(0, needed - count),
            reason,
        )

    def observe(self, frame):
        tick = _get(frame, "tick")
        step = _get(tick, "step")
        if (
            type(step) is not int
            or step <= self.last
            or _get(tick, "generation") != self.generation
        ):
            raise ValueError("Invalid Cell C observation clock or generation")
        self.last = step
        state = _get(_get(frame, "truth"), "plant_state")
        if not state.get("event"):
            return ()
        name, part = state["event"], state["part"]
        idx = len(self.events)
        self.events.append((part, name))
        cycle_index = idx if idx < len(EVENTS) + 1 else idx - len(EVENTS) - 1
        stations = TRANSFER_STATIONS.get(cycle_index)
        if (
            idx >= len(ORDER)
            or (part, name) != ORDER[idx]
            or (stations and state.get("station") not in stations)
        ):
            self.failed_at.setdefault(SEQUENCE, step)
            self.count[SEQUENCE] = self.failed_count[SEQUENCE] = 1
        if part not in PARTS:
            return tuple(self._result(r) for r in RULES)
        row = self.parts.setdefault(part, {"part": part, "violations": []})
        if name == "inspection":
            height = _number(state["geometry_height_mm"])
            probe = _number(state["probe_height_mm"])
            expected = _dimension_bin(height)
            owner, route = _decision(frame, state)
            agrees = _dimension_bin(probe) == expected and abs(height - probe) <= 0.020
            row.update(
                geometry_height_mm=height,
                probe_height_mm=probe,
                expected_bin=expected,
                result_part_id=owner,
                commanded_bin=route,
                fresh=owner == part,
            )
            self._check(MEASURE, step, agrees)
            self._check(OWNER, step, owner == part)
            if not agrees:
                row["violations"].append("MEASUREMENT_DISAGREEMENT")
            if owner != part:
                row["violations"].append("STALE_RESULT")
        elif name == "release" and state.get("station") in BINS:
            row["release_station"] = state["station"]
        elif name == "output_settled":
            actual = _observed_bin(output_pose(state))
            followed = actual == row.get("commanded_bin") == row.get("release_station")
            correct = actual == row.get("expected_bin")
            row.update(observed_bin=actual, correct_destination=correct)
            self._check(FOLLOW, step, followed)
            self._check(DEST, step, correct)
            if not followed:
                row["violations"].append("OUTPUT_MISSED_COMMAND")
            if not correct:
                row["violations"].append("WRONG_DESTINATION")
        if len(self.events) == len(ORDER) and SEQUENCE not in self.failed_at:
            self._check(SEQUENCE, step, self.events == list(ORDER))
        return tuple(self._result(r) for r in RULES)

    def finalize(self, final_tick, complete_horizon):
        return tuple(self._result(rule, complete_horizon) for rule in RULES)
