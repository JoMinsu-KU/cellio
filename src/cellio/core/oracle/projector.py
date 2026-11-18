"""Project immutable P0–P7 evidence into the finite-trace rule vocabulary."""

from collections import deque
from collections.abc import Mapping
from math import acos, dist

from .audit import CommandAudit, usable
from .geometry import (
    FINGERS,
    clear_fixture,
    fixture_clamped,
    fixture_loaded,
    get,
    goal_now,
    grasp,
    gripper_open,
    joint,
    measured,
    quiet,
)
from .temporal import Boundary, all3, any3


def actual_pose(truth, position):
    tcp = measured(truth, "bodies", "cell_tcp")
    if tcp is None:
        return None
    q = get(tcp, "orientation_wxyz")
    return (
        dist(get(tcp, "position_m"), position) <= 0.004
        and 2 * acos(min(1.0, abs(q[2]))) <= 0.05
    )


def grip_closed(truth):
    fingers = [measured(truth, "joints", name) for name in FINGERS]
    if any(f is None for f in fingers):
        return None
    return sum(get(f, "position") for f in fingers) <= 0.055 and all(
        abs(get(f, "velocity")) <= 0.003 for f in fingers
    )


def dwell(history, steps):
    if len(history) < steps + 1:
        return False
    return all3(list(history)[-(steps + 1) :])


def offset(truth):
    part, tcp = (measured(truth, "bodies", name) for name in ("part_000", "cell_tcp"))
    if part is None or tcp is None:
        return None
    return tuple(x - y for x, y in zip(get(part, "position_m"), get(tcp, "position_m")))


class Projector:
    def __init__(self, profile, generation):
        self.p = profile
        self.generation = generation
        self.audit = CommandAudit(generation)
        self.belt_history = deque(maxlen=25)
        self.open_history = deque(maxlen=5)
        self.goal_history = deque(maxlen=61)
        self.goal_ever = False
        self.process_since = None
        self.process_result = False
        self.process_span = None
        self.seen_source = self.seen_loaded = self.seen_clamped = False
        self.process_enabled = self.unloaded = False
        self.pose_name = None
        self.holding = False
        self.lift_step = self.grasp_offset = None
        self.abort_epoch = None
        self.stopped = set()
        self.has_cancel = False
        self.response_runs = {}
        self.active_commands = {}
        self.pending_commands = {}
        self.fault_end = None
        self.fault_data = {}
        self.channel_generation = 0
        self.reset_initial = None

    def _action(self, intent):
        kind, value = get(intent, "kind"), get(intent, "value")
        if kind == "POSE":
            goal = tuple(value)
            if len(goal) != 7 or tuple(goal[3:]) not in (
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, -1.0, 0.0),
            ):
                return "other", None
            name = next((n for n, p in self.p.points.items() if p == goal[:3]), None)
            if name is None:
                return "other", None
            if self.holding:
                return ("lift" if self.lift_step is None else "carry"), name
            if name == "pickup_hover" and self.pose_name in (None, "park"):
                return "approach", name
            if name == "fixture_unload_hover" and self.pose_name == "park":
                return "unload", name
            return "motion", name
        if kind == "GRIPPER_GAP":
            return (
                "pick" if value == 0.0 else "release" if value == 0.080 else "other"
            ), None
        if kind == "JOINT_POSITION":
            return (
                "clamp_close"
                if value == -0.065
                else "clamp_open"
                if value == 0.0
                else "other"
            ), None
        if kind == "BOOL" and get(intent, "actuator_id") == "actuator.process":
            return ("process_on" if value else "process_off"), None
        return (
            "known" if kind in ("HOLD", "CANCEL_OPERATION", "BOOL") else "other"
        ), None

    def _signals(self, action):
        pose = ("robot.at_waypoint", "robot.completed_command_id")
        if action in ("pick", "release"):
            return pose
        return {
            "approach": ("pickup.present", "belt.stopped"),
            "lift": ("gripper.closed",),
            "carry": ("gripper.closed", *pose),
            "clamp_close": ("fixture.present", "robot.clear_fixture"),
            "process_on": ("fixture.present", "clamp.closed", "robot.clear_fixture"),
            "unload": ("clamp.open",),
            "done": ("destination.present", "gripper.open"),
        }.get(action, ())

    def _response(self, truth, cmd):
        kind = get(cmd, "kind")
        v = get(cmd, "value")
        if kind == "GRIPPER_GAP":
            return (grip_closed(truth), 12) if v == 0.0 else (gripper_open(truth), 4)
        if kind == "JOINT_POSITION":
            return (
                (
                    joint(truth, "clamp", -float("inf"), -0.058, 0.003)
                    if v < 0
                    else joint(truth, "clamp", -0.002, float("inf"), 0.003)
                ),
                4,
            )
        return actual_pose(truth, tuple(v[:3])), 12

    def project(self, frame):
        tick = get(frame, "tick")
        k = get(tick, "step")
        truth = get(frame, "truth")
        t = get(truth, "tick")
        signals = get(frame, "observed_signals", {})
        state = get(truth, "plant_state", {})
        deliveries = get(frame, "deliveries", ())
        receipt = get(frame, "actuation")
        previous_commands = self.active_commands.copy()
        facts, events = {}, []

        def emit(name, cmd=None, **details):
            job = get(cmd, "job_id", 0)
            epoch = get(cmd, "operation_epoch", 0)
            item = {
                "event_type": name,
                "event_id": f"{name}:{k}:{len(events)}",
                "job_id": job,
                "operation_epoch": epoch,
                **details,
            }
            events.append(item)
            return item

        def freshness(action, cmd=None):
            needed = self._signals(action)
            if needed:
                usable_now = all3(
                    usable(
                        signals.get(s), "int" if s.endswith("command_id") else "bool", k
                    )
                    for s in needed
                )
                emit("command.freshness", cmd, input_fresh=usable_now)

        same = (
            get(t, "step") == k
            and get(tick, "generation") == get(t, "generation") == self.generation
            and get(tick, "episode_id") == get(t, "episode_id")
        )
        required_bodies = (
            "part_000",
            "hand",
            "cell_tcp",
            "finger_left",
            "finger_right",
            *(("moving_jaw",) if self.p.fixture else ()),
        )
        required_joints = (
            *FINGERS,
            *(f"panda_joint{i}" for i in range(1, 8)),
            *(("clamp",) if self.p.fixture else ()),
        )
        valid = all(measured(truth, "bodies", n) is not None for n in required_bodies)
        valid &= all(measured(truth, "joints", n) is not None for n in required_joints)
        facts["state_valid"] = (
            (True if valid and get(truth, "complete") else None) if same else False
        )
        speed = state.get("belt.surface_speed_mps")
        self.belt_history.append(None if speed is None else abs(speed) <= 0.001)
        facts["belt_stationary"] = self.belt_history[-1]
        self.open_history.append(
            joint(truth, "clamp", -0.002, float("inf"), 0.003)
            if self.p.fixture
            else False
        )
        facts["belt_dwell"] = dwell(self.belt_history, 24)
        facts["clamp_open_dwell"] = dwell(self.open_history, 4)
        facts["actual_grasp_contact"] = grasp(truth)
        clear = clear_fixture(truth) if self.p.fixture else None
        facts["fixture_clear"] = clear
        loaded = fixture_loaded(truth) if self.p.fixture else None
        clamped = fixture_clamped(truth) if self.p.fixture else None
        facts["clamp_ready"] = all3((loaded, clear))
        facts["physical_quiescent"] = quiet(truth, self.p.belt, self.p.fixture)
        facts["registered_actions"] = True
        facts["process_active"] = self.process_enabled
        facts["process_valid"] = True
        live_process = all3((clamped, clear))
        if (
            self.process_enabled
            and self.process_since is not None
            and k - self.process_since <= 120
        ):
            self.process_span = all3((self.process_span, live_process))
            facts["process_valid"] = self.process_span
            if k - self.process_since == 120:
                self.process_result = self.process_span
        process_requirement = (
            all3((self.process_result, self.unloaded)) if self.p.fixture else True
        )
        facts["process_history_valid"] = self.process_result if self.p.fixture else None
        facts["goal_sample"] = all3(
            (goal_now(truth, self.p.output_center), process_requirement)
        )
        self.goal_history.append(facts["goal_sample"])
        self.goal_ever = any3((self.goal_ever, dwell(self.goal_history, 60)))
        facts["declared_goal"] = self.goal_ever
        actual_offset = offset(truth)
        if (
            self.holding
            and self.lift_step is None
            and self.grasp_offset is None
            and facts["actual_grasp_contact"] is True
        ):
            self.grasp_offset = actual_offset
        facts["carry_active"] = self.lift_step is not None and k - self.lift_step >= 24
        facts["carry_valid"] = (
            None
            if self.grasp_offset is None or actual_offset is None
            else dist(self.grasp_offset, actual_offset) <= 0.018
        )
        for e in get(frame, "source_events", ()):
            name = get(e, "event_type")
            payload = get(e, "payload", {})
            if name == "controller.abort":
                self.abort_epoch = payload.get(
                    "cancelled_epoch", payload.get("operation_epoch", self.audit.epoch)
                )
                self.stopped.clear()
                self.has_cancel = False
                self.lift_step = None
                emit(
                    "operation.abort",
                    operation_epoch=self.abort_epoch,
                    job_id=payload.get("job_id", 0),
                )
                emit(
                    "command.closed",
                    operation_epoch=self.abort_epoch,
                    job_id=payload.get("job_id", 0),
                )
            elif name == "controller.done":
                freshness("done")
                emit("operation.done")
            elif name == "reset.compare":
                emit("reset.compare")
                checks = payload.get("checks", {})
                self.reset_initial = all3(checks.values()) if checks else None
        current_generation = all(
            get(s, "generation") == self.generation for s in signals.values()
        )
        current_generation &= all(
            get(c, "generation") == self.generation for c in get(frame, "intents", ())
        )
        current_generation &= all(
            get(c, "generation") == self.generation
            for c in get(receipt, "applied_commands", ())
            if get(c, "status") == "APPLIED"
        )
        facts["reset_clean"] = all3((self.reset_initial, same, current_generation))

        for cmd in get(frame, "intents", ()):
            valid_order = self.audit.issue(cmd)
            emit("command.audit", cmd, command_order=valid_order)
            action, name = self._action(cmd)
            # Open in initialize establishes the initial gripper state.
            if action != "release" or self.pose_name is not None:
                freshness(action, cmd)
            kind = get(cmd, "kind")
            limit = {"GRIPPER_GAP": 240, "JOINT_POSITION": 360, "POSE": 960}.get(kind)
            if limit is not None:
                cid = get(cmd, "command_id")
                emit("command.response", cmd, upper_steps=limit, command_id=cid)
                self.response_runs[cid] = (cmd, None, None, k + limit)
            if action == "other":
                facts["registered_actions"] = None
        for decision in deliveries:
            intent = get(decision, "intent")
            ident = get(intent, "command_id")
            status = get(decision, "status")
            if status == "QUEUED":
                self.pending_commands[ident] = get(intent, "operation_epoch")
            elif status in (
                "DELIVERED",
                "CANCELLED",
                "STALE",
                "SUPERSEDED",
                "REJECTED",
            ):
                self.pending_commands.pop(ident, None)
        applied = get(receipt, "applied_commands", ())
        for r in applied:
            ident = get(r, "command_id")
            cmd = self.audit.issued.get(ident)
            verdict = self.audit.apply(cmd, r)
            emit("command.audit", cmd, command_order=verdict)
            if cmd is None or get(r, "status") != "APPLIED":
                continue
            action, name = self._action(cmd)
            endpoint, kind = get(cmd, "actuator_id"), get(cmd, "kind")
            value = get(cmd, "value")
            if kind == "CANCEL_OPERATION":
                self.active_commands.clear()
                emit(
                    "command.closed",
                    operation_epoch=value,
                    job_id=get(cmd, "job_id", 0),
                )
            else:
                self.active_commands[endpoint] = ident
            if (
                self.abort_epoch is not None
                and get(cmd, "operation_epoch") == self.abort_epoch + 1
            ):
                if kind == "CANCEL_OPERATION" and value == self.abort_epoch:
                    self.has_cancel = True
                    self.stopped.add(endpoint)
                if kind == "HOLD" or kind == "BOOL" and value is False:
                    self.stopped.add(endpoint)
            if action == "pick":
                pick = self.p.points.get(self.pose_name)
                pos = get(measured(truth, "bodies", "part_000"), "position_m")
                aligned = (
                    None
                    if pos is None or pick is None
                    else all(
                        center - tol <= actual <= center + tol
                        for center, actual, tol in zip(pick, pos, (0.006, 0.006, 0.010))
                    )
                )
                pose_ok = actual_pose(truth, pick) if pick is not None else False
                registered_pick = self.pose_name in (
                    "pickup_grasp",
                    "source_grasp",
                    "fixture_unload_grasp",
                )
                emit(
                    "action.pick",
                    cmd,
                    pick_ready=all3((registered_pick, aligned, pose_ok)),
                )
                self.holding = True
                self.grasp_offset = None
                if self.pose_name == "source_grasp":
                    self.seen_source = all3((aligned, pose_ok))
            elif action == "release":
                self.holding = False
                self.lift_step = None
                if self.pose_name == "fixture_load_place":
                    self.seen_loaded = True
            elif action == "lift":
                emit("action.lift", cmd)
                self.lift_step = k
            elif action == "approach":
                emit("action.approach", cmd)
            elif action == "clamp_close":
                emit("action.clamp_close", cmd)
                self.seen_clamped = all3(
                    (self.seen_source, self.seen_loaded, facts["clamp_ready"])
                )
            elif action == "unload":
                emit("action.unload", cmd)
                self.unloaded = all3((facts["clamp_open_dwell"], self.process_result))
            elif action == "process_on" and not self.process_enabled:
                self.process_enabled = True
                self.process_since = k
                self.process_span = all3((self.seen_clamped, live_process))
                facts["process_active"], facts["process_valid"] = (
                    True,
                    self.process_span,
                )
            elif action == "process_off" and self.process_enabled:
                if k - self.process_since < 120:
                    facts["process_active"], facts["process_valid"] = True, False
                    self.process_result = False
                self.process_enabled = False
            if name is not None:
                self.pose_name = name
        facts["all_targets_held"] = (
            self.has_cancel
            and self.stopped.issuperset(self.p.stop_endpoints)
            and self.abort_epoch not in self.pending_commands.values()
            and get(frame, "fault_audit", {}).get("pending_command_count", 0) == 0
        )
        responses = {}
        for cid, (cmd, definite, possible, deadline) in list(
            self.response_runs.items()
        ):
            if k > deadline:
                del self.response_runs[cid]
                continue
            if k <= get(cmd, "issued_step"):
                continue
            active_cmd = previous_commands.get(get(cmd, "actuator_id")) == cid
            measured_ok, span = self._response(truth, cmd)
            current = all3((active_cmd, measured_ok))
            definite = (
                (k if definite is None else definite) if current is True else None
            )
            possible = (
                (k if possible is None else possible) if current is not False else None
            )
            responses[cid] = (
                True
                if definite is not None and k - definite >= span
                else None
                if possible is not None and k - possible >= span
                else False
            )
            self.response_runs[cid] = (cmd, definite, possible, deadline)
        facts["responses"] = responses
        self._restore(k, frame, facts, emit)
        return Boundary(k, facts, events, frame)

    def _restore(self, k, frame, facts, emit):
        report = get(frame, "fault_audit", {})
        # Profiles may wrap the single fault's probe in an id-keyed mapping.
        if "family" not in report:
            report = next(
                (
                    v
                    for v in report.values()
                    if isinstance(v, Mapping) and "family" in v
                ),
                {},
            )
        for change in get(frame, "fault_transitions", ()):
            kind = get(change, "kind")
            if kind in ("ACTIVE", "ENDED"):
                self.channel_generation += 1
            if (
                kind == "ENDED"
                and get(report, "first_effect_step") is not None
                and not get(frame, "final_snapshot")
            ):
                self.fault_end = k
                self.fault_data = report
                emit("path.ended")
        facts["path_restored"] = None
        facts["no_stale_tail"] = None
        if self.fault_end is None:
            return
        target, family = (
            get(self.fault_data, "target_id"),
            get(self.fault_data, "family"),
        )
        if get(frame, "final_snapshot"):
            facts["no_stale_tail"] = True
            return
        if family in ("F1", "F2", "F3", "F5"):
            a = get(frame, "raw_signals", {}).get(target)
            b = get(frame, "observed_signals", {}).get(target)
            if a is not None and b is not None:
                same = all(
                    get(a, n) == get(b, n)
                    for n in (
                        "source_seq",
                        "source_step",
                        "value",
                        "quality",
                        "generation",
                    )
                )
                facts["path_restored"] = (
                    same and get(b, "source_step") == get(b, "deliver_step") == k
                )
                facts["no_stale_tail"] = (
                    same and get(b, "generation") == self.generation
                )
            return
        receipt = get(frame, "actuation")
        if receipt is None or not get(receipt, "accepted"):
            return
        no_old = all(
            get(d, "channel_generation") == self.channel_generation
            for d in get(frame, "deliveries", ())
            if get(d, "status") == "DELIVERED"
            and get(get(d, "intent"), "actuator_id") == target
        )
        no_override = get(self.fault_data, "fault_id") not in get(
            receipt, "active_override_ids", ()
        )
        facts["no_stale_tail"] = no_old and no_override
        facts["path_restored"] = facts["no_stale_tail"] and target in get(
            receipt, "targets_by_actuator", {}
        )
