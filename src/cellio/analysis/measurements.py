"""Streaming per-attempt statistics. Step latencies retain exact integer histograms."""

from collections import Counter, defaultdict
from math import ceil, floor

from cellio.core.oracle.audit import usable

from .statistics import METRIC_REVISION


def histogram_summary(hist, ids=(), unit="step"):
    count = sum(hist.values())

    def quantile(p):
        if not count:
            return None
        h = (count - 1) * p
        low = high = None
        n = 0
        for value, freq in sorted(hist.items()):
            n += freq
            if low is None and n > floor(h):
                low = value
            if n > ceil(h):
                high = value
                break
        return low + (h - floor(h)) * (high - low)

    return {
        "numerator": sum(value * n for value, n in hist.items()),
        "denominator": count,
        "median": quantile(0.5),
        "p95": quantile(0.95),
        "p99": quantile(0.99),
        "max": max(hist, default=None),
        "unit": unit,
        "metric_revision": METRIC_REVISION,
        "source_artifact_ids": list(ids),
        "exclusions_with_reason": {},
    }


class Measurements:
    def __init__(self, cell_id=None):
        self.latencies = defaultdict(Counter)
        self.intervals = []
        self.active_intervals = []
        self.partial = []
        self.active = True
        self.sample_ids = {}
        self.command_lags = {}
        self.fail_seen = set()
        self.first_detection_lag = {}
        self.abort = None
        self.abort_cause = None
        self.cause_kind = "invalid"
        self.invalid_since = {}
        self.stage_start = self.stage = self.motion = self.motion_applied = (
            self.clear_start
        ) = None
        self.issued = {}
        self.stage_table = {}
        if cell_id in ("cell_a", "cell_b"):
            from cellio.controllers.stages import CELL_A
            from cellio.controllers.stages_b import CELL_B

            self.stage_table = {
                s.name: s for s in (CELL_A if cell_id == "cell_a" else CELL_B)
            }
        self.clear = None
        self.fault = {}
        self.controller_stage = None
        self.max_backlog = None
        self.witnesses = {}
        self.reset_events = []
        self.outstanding_fault = None

    def observe(self, f, updates):
        k = f["step"]
        controller = f.get("controller_output") or {}
        perf = f.get("performance") or {}
        prior = perf.get("previous_tick")
        if prior:
            self._cost(
                prior["step"], prior["critical_path_ns"], prior.get("controller_active")
            )
        for name, sample in f["observed_signals"].items():
            if f.get("controller_called"):
                if usable(sample, sample["dtype"], k) is False:
                    self.invalid_since.setdefault(name, k)
                else:
                    self.invalid_since.pop(name, None)
            source, delivered = sample.get("source_step"), sample.get("deliver_step")
            ident = (sample.get("generation"), sample.get("transport_seq"))
            if (
                source is not None
                and delivered is not None
                and ident != self.sample_ids.get(name)
            ):
                self.latencies["observation_delay"][delivered - source] += 1
                self.sample_ids[name] = ident
        for cmd in f["intents"]:
            if cmd["kind"] in ("POSE", "GRIPPER_GAP", "BOOL", "JOINT_POSITION"):
                self.issued[cmd["actuator_id"]] = cmd["issued_step"]
            if cmd["kind"] == "POSE":
                self.motion, self.motion_applied = cmd, None
        resume = {
            entry["intent"]["command_id"]
            for entry in f["deliveries"]
            if entry.get("reason") == "RESTORE_RESUME"
        }
        for entry in f["applied"]:
            if entry["status"] == "APPLIED" and entry.get("applied_step") is not None:
                if self.motion and entry["command_id"] == self.motion["command_id"]:
                    self.motion_applied = entry["applied_step"]
                lag = entry["applied_step"] - entry["issued_step"]
                self.latencies["command_delay"][lag] += 1
                if entry["command_id"] in resume:
                    self.latencies["restore_resume_delay"][lag] += 1
        if (
            self.stage == "fixture_retreat"
            and self.motion
            and self.clear_start is None
            and f.get("controller_called")
        ):
            observed = f["observed_signals"]
            at_goal = observed.get("robot.at_waypoint", {})
            token = observed.get("robot.completed_command_id", {})
            if (
                tuple(self.motion["value"][:3]) == (0.30, 0.0, 1.10)
                and usable(at_goal, "bool", k)
                and at_goal["value"] is True
                and usable(token, "int", k)
                and token["value"] == self.motion["sequence"]
            ):
                self.clear_start = k
        for event in f["events"]:
            if event["event_type"] == "controller.stage_enter":
                self.stage = event["payload"].get("stage_id")
                self.stage_start = k
                self.clear_start = self.motion_applied = self.motion = None
                self.invalid_since.clear()
            if event["event_type"] == "reset.compare":
                self.reset_events.append((k, event.get("payload", {})))
            if event["event_type"] == "controller.abort" and self.abort is None:
                self.abort = k
                text = (
                    event.get("payload", {}).get("reason")
                    or controller.get("reason")
                    or ""
                )
                if text.startswith(("INVALID_INPUT:", "STALE_INPUT:")):
                    signal = text.split(":", 1)[1]
                    sample = f["observed_signals"].get(signal)
                    if sample and usable(sample, sample["dtype"], k) is False:
                        self.abort_cause = self.invalid_since.get(signal)
                elif text in ("ADMISSION_TIMEOUT", "RESPONSE_TIMEOUT"):
                    self.cause_kind = "deadline"
                    self.abort_cause = self.deadline(text)
        online = f.get("oracle_updates") or ()
        if isinstance(online, dict):
            online = online.get("requirements", online.values())
        for r in online:
            if not isinstance(r, dict):
                continue
            rid = r["requirement_id"]
            if r["verdict"] == "FAIL" and rid not in self.fail_seen:
                self.fail_seen.add(rid)
                if r["first_witness_step"] is not None:
                    lag = k - r["first_witness_step"]
                    self.first_detection_lag[rid] = lag
                    self.latencies["oracle_detection_delay"][lag] += 1
        report = f.get("fault_audit") or {}
        if "family" in report:
            self.fault.update(report)
            if not f["final_snapshot"]:
                target = report["target_id"]
                family = report["family"]
                observed = f["observed_signals"].get(target)
                receipt = f.get("actuation") or {}
                captured = report.get("actual_start")
                pending = report.get(
                    "observation_pending_count"
                    if family == "F3"
                    else "pending_command_count"
                )
                if report.get("application_status") != "ACTIVE":
                    self.outstanding_fault = False
                elif family in ("F3", "F4"):
                    self.outstanding_fault = (
                        pending > 0 if type(pending) is int else None
                    )
                elif family == "F6":
                    self.outstanding_fault = (
                        report.get("fault_id") in receipt.get("active_override_ids", ())
                        if receipt
                        else None
                    )
                elif family == "F2":
                    self.outstanding_fault = (
                        observed.get("source_step") == captured if observed else None
                    )
                else:
                    self.outstanding_fault = observed is not None
        for r in updates:
            key = r.requirement_id
            if r.verdict == "PASS" and r.first_witness_step is not None:
                self.witnesses.setdefault(key, r.first_witness_step)
                if ".r15." in key and self.clear is None:
                    self.clear = r.first_witness_step
        if f["final_snapshot"]:
            return
        if perf.get("queue_depth") is not None:
            self.max_backlog = max(self.max_backlog or 0, perf["queue_depth"])
        if controller:
            self.controller_stage = controller.get(
                "mission_status", self.controller_stage
            )
        if type(perf.get("critical_path_ns")) is int and prior is None:
            self._cost(
                k,
                perf["critical_path_ns"],
                self.controller_stage not in ("DONE", "ABORTED"),
            )

    def deadline(self, reason):
        stage = self.stage_table.get(self.stage)
        if not stage or self.stage_start is None:
            return None
        if reason == "ADMISSION_TIMEOUT" or self.stage in (
            "initialize",
            "wait_part",
            "wait_stop",
            "verify_output",
        ):
            origin = self.stage_start
        elif self.clear_start is not None:
            return self.clear_start + 360
        elif stage.poses:
            origin = self.motion_applied
        else:
            endpoint = (
                "clamp"
                if self.stage in ("wait_clamp", "wait_open")
                else next((name for name, _ in stage.actions), None)
            )
            origin = self.issued.get("actuator." + endpoint) if endpoint else None
        return origin + stage.timeout if origin is not None else None

    def _cost(self, k, ns, active):
        if k % 4 == 0:
            self.partial = []
            self.active = active is True
        self.partial.append(ns)
        if k % 4 == 3 and len(self.partial) == 4:
            cost = sum(self.partial) / 1e6
            self.intervals.append(cost)
            if self.active:
                self.active_intervals.append(cost)

    def finish(self, horizon, evaluation):
        fault = self.fault
        for key, actual, scheduled in (
            ("start_error", "actual_start", "scheduled_start"),
            ("end_error", "actual_end", "scheduled_end"),
        ):
            if fault.get(actual) is not None and fault.get(scheduled) is not None:
                self.latencies[key][fault[actual] - fault[scheduled]] += 1
        end = fault.get("actual_end")
        if end is not None and end < horizon and self.clear is not None:
            self.latencies["path_restore_delay"][self.clear - end] += 1
        if self.abort_cause is not None:
            self.latencies[self.cause_kind + "_to_abort"][
                self.abort - self.abort_cause
            ] += 1
            for num, name in (
                (".r05.", self.cause_kind + "_to_hold"),
                (".r06.", self.cause_kind + "_to_quiescence"),
            ):
                step = next((v for k, v in self.witnesses.items() if num in k), None)
                if step is not None:
                    self.latencies[name][step - self.abort_cause] += 1
        result = next(
            (r for r in evaluation["requirements"] if ".r14." in r["requirement_id"]),
            None,
        )
        if result and result["verdict"] == "PASS":
            self.latencies["task_completion"][result["first_witness_step"]] += 1
