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
    def __init__(self):
        self.latencies = defaultdict(Counter)
        self.intervals = []
        self.active_intervals = []
        self.partial = []
        self.active = True
        self.sample_ids = {}
        self.command_lags = {}
        self.fail_seen = set()
        self.abort = None
        self.abort_cause = None
        self.clear = None
        self.fault = {}
        self.controller_stage = None
        self.max_backlog = None
        self.witnesses = {}

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
            source, delivered = sample.get("source_step"), sample.get("deliver_step")
            ident = (sample.get("generation"), sample.get("transport_seq"))
            if (
                source is not None
                and delivered is not None
                and ident != self.sample_ids.get(name)
            ):
                self.latencies["observation_delay"][delivered - source] += 1
                self.sample_ids[name] = ident
        resume = {
            entry["intent"]["command_id"]
            for entry in f["deliveries"]
            if entry.get("reason") == "RESTORE_RESUME"
        }
        for entry in f["applied"]:
            if entry["status"] == "APPLIED" and entry.get("applied_step") is not None:
                lag = entry["applied_step"] - entry["issued_step"]
                self.latencies["command_delay"][lag] += 1
                if entry["command_id"] in resume:
                    self.latencies["restore_resume_delay"][lag] += 1
        for event in f["events"]:
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
                        self.abort_cause = k
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
                    self.latencies["oracle_detection_delay"][
                        k - r["first_witness_step"]
                    ] += 1
        report = f.get("fault_audit") or {}
        if "family" in report:
            self.fault.update(report)
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
            self.latencies["invalid_to_abort"][self.abort - self.abort_cause] += 1
            for num, name in (
                (".r05.", "invalid_to_hold"),
                (".r06.", "invalid_to_quiescence"),
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
