"""Classify planned rows, first attempts, retried attempts, and missing evidence."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cellio.io import CellIOError, digest, file_hash
from cellio.runtime.campaign import read_plan, soak_row

from .measurements import Measurements
from .replay import read, read_attempt
from .statistics import block_bootstrap, ratio


def lines(path):
    if path.is_file():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


@dataclass
class Trial:
    row: dict
    started: bool = False
    status: str = "NOT_STARTED"
    attempt_id: str | None = None
    path: Path | None = None
    eval: dict = field(default_factory=dict)
    fault: dict = field(default_factory=dict)
    performance: dict = field(default_factory=dict)
    measurement: Measurements | None = None
    error: str | None = None

    @property
    def outcome(self):
        if self.status == "COMPLETED" and self.eval:
            return self.eval["task_outcome"] if self.verified_decision else "UNKNOWN"
        if self.status == "NOT_STARTED":
            return "NOT_STARTED"
        if self.status in ("RUNNING", "CANCELLED", "PARTIAL"):
            return "UNKNOWN"
        return "ERROR"

    @property
    def verified_decision(self):
        return self.error is None and (
            self.eval.get("online_offline_equal") is True
            or self.row.get("benchmark_mode") == "B0"
            and self.eval.get("online_offline_equal") is None
        )

    @property
    def evaluable(self):
        return (
            self.status == "COMPLETED"
            and self.verified_decision
            and self.eval.get("task_outcome") not in ("UNKNOWN", None)
            and self.eval.get("compliance_verdict") in ("PASS", "FAIL")
        )

    def flags(self):
        planned = bool(self.row["config"].get("faults"))
        f = self.fault
        end = f.get("actual_end")
        realized = planned and f.get("first_effect_step") is not None
        restored = (
            realized
            and end is not None
            and end < self.row["config"]["episode"]["horizon_steps"]
            and self.measurement is not None
            and self.measurement.clear is not None
        )
        return {
            "N_plan": 1,
            "N_start": int(self.started),
            "N_eval": int(self.evaluable),
            "N_faultplan": int(planned),
            "N_trigger": int(planned and f.get("trigger_match_step") is not None),
            "N_activate": int(planned and f.get("actual_start") is not None),
            "N_realized": int(realized),
            "N_validfault": int(realized and self.evaluable),
            "N_clear": int(restored),
            "N_recovery_eligible": int(
                realized
                and end is not None
                and end < self.row["config"]["episode"]["horizon_steps"]
            ),
            "N_error": int(
                self.status
                in ("INFRA_ERROR", "INTEGRITY_ERROR", "FAILED", "CRASHED", "HUNG")
            ),
            "N_success": int(self.outcome == "SUCCESS"),
        }


def safe_child(parent, *names):
    for name in names:
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or any(c in name for c in ("/", "\\", ":"))
        ):
            raise CellIOError("RUN_ID_INVALID", str(name))
    p = parent.joinpath(*names).resolve()
    if not p.is_relative_to(parent.resolve()):
        raise CellIOError("RUN_PATH_ESCAPE", str(p))
    return p


TERMINAL = {"COMPLETED", "CANCELLED", "INFRA_ERROR", "SKIPPED_PRECONDITION"}
LIFECYCLE = (
    "PLANNED",
    "PREFLIGHT",
    "BOOTING",
    "LOADING",
    "WARMUP",
    "READY",
    "RUNNING",
    "FINALIZING",
    "CLOSED",
    "IDLE",
    "EXITED",
)


def attempt_histories(entries, planned):
    histories = defaultdict(list)
    owners = {}
    for e in entries:
        run = e["run_id"]
        ident = e["attempt_id"]
        index = e["attempt_index"]
        if run not in planned:
            raise CellIOError("RUN_LEDGER_EXTRA", run)
        if (
            type(index) is not int
            or index < 0
            or not isinstance(ident, str)
            or not ident
        ):
            raise CellIOError("RUN_LEDGER_CONFLICT", "Invalid attempt identity/index")
        if owners.setdefault(ident, (run, index)) != (run, index):
            raise CellIOError("RUN_LEDGER_CONFLICT", "Reused attempt identity")
        history = histories[run]
        if index == len(history):
            if e["state"] != "PLANNED":
                raise CellIOError(
                    "RUN_LEDGER_CONFLICT", "Attempt must begin at PLANNED"
                )
            if index and history[-1][-1]["state"] not in TERMINAL:
                raise CellIOError(
                    "RUN_LEDGER_CONFLICT", "Retry started before its parent ended"
                )
            history.append([])
        if index != len(history) - 1:
            raise CellIOError(
                "RUN_LEDGER_CONFLICT", "Attempt index gap or out-of-order transition"
            )
        records = history[index]
        parent = history[index - 1][0]["attempt_id"] if index else None
        if (
            e.get("parent_attempt_id") != parent
            or (index and not e.get("retry_reason"))
            or (
                records
                and (
                    records[0]["attempt_id"] != ident
                    or records[0].get("retry_reason") != e.get("retry_reason")
                )
            )
        ):
            raise CellIOError(
                "RUN_LEDGER_CONFLICT", "Inconsistent attempt parent, ID or retry reason"
            )
        state = e["state"]
        if state not in TERMINAL and state not in LIFECYCLE:
            raise CellIOError(
                "RUN_LEDGER_CONFLICT", "Unknown ledger state " + str(state)
            )
        if records:
            last = records[-1]["state"]
            if last in TERMINAL:
                raise CellIOError(
                    "RUN_LEDGER_CONFLICT", "Transition after terminal state"
                )
            # The supervisor logs process launch (BOOTING) before sampling episode
            # heartbeats. The episode may still be in PLANNED or PREFLIGHT then.
            process_launch = (
                len(records) == 2 and last == "BOOTING" and "pid" in records[-1]
            )
            if (
                state not in TERMINAL
                and not process_launch
                and LIFECYCLE.index(state) < LIFECYCLE.index(last)
            ):
                raise CellIOError(
                    "RUN_LEDGER_CONFLICT", "Episode lifecycle moved backwards"
                )
        records.append(e)
    return histories


def verify_plan_inputs(path, row, provenance):
    source = read(path / "environment.lock.json").get("cellio_source_sha256")
    if source is not None and source != provenance.get("code_hash"):
        raise CellIOError(
            "RUN_PLAN_HASH", "Installed CellIO source differs from campaign provenance"
        )
    if digest(read(path / "config.resolved.json")) != row["config_hash"]:
        raise CellIOError(
            "RUN_PLAN_HASH", "artifact configuration differs from frozen row"
        )
    if read(path / "initial_state.json") != row["initial_state"]:
        raise CellIOError(
            "RUN_PLAN_HASH", "artifact initial state differs from frozen row"
        )
    for key, name in (
        ("environment_hash", "environment.lock.json"),
        ("assets_hash", "assets.lock.json"),
        ("protocol_hash", "protocol.lock.json"),
    ):
        if digest(read(path / name)) != provenance[key]:
            raise CellIOError(
                "RUN_PLAN_HASH", name + " differs from campaign provenance"
            )
    if (
        "requirements_hash" in provenance
        and file_hash(path / "requirements.lock") != provenance["requirements_hash"]
    ):
        raise CellIOError(
            "RUN_PLAN_HASH", "requirements.lock differs from campaign provenance"
        )


def read_trial(directory, row, records, provenance):
    trial = Trial(row)
    if not records:
        return trial
    ident = records[0]["attempt_id"]
    path = safe_child(directory, row["run_id"], ident)
    trial.path, trial.attempt_id = path, ident
    trial.started = any(s["state"] == "RUNNING" for s in records)
    terminal = records[-1]
    ledger_status = terminal["state"]
    trial.status = ledger_status
    if not (path / "COMPLETE").exists():
        if ledger_status in ("COMPLETED", "CANCELLED"):
            trial.status, trial.error = (
                "INTEGRITY_ERROR",
                "final ledger state without COMPLETE",
            )
        elif ledger_status == "INFRA_ERROR":
            trial.error = (
                terminal.get("error")
                or terminal.get("reason")
                or "infrastructure error without sealed artifact"
            )
        elif ledger_status not in ("RUNNING", "SKIPPED_PRECONDITION"):
            trial.status = "PARTIAL"
        return trial
    trial.measurement = Measurements(row.get("cell"))
    try:
        trial.eval = read_attempt(path, on_frame=trial.measurement.observe)
        if trial.eval["run_id"] != row["run_id"] or trial.eval["attempt_id"] != ident:
            raise CellIOError(
                "RUN_LEDGER_CONFLICT", "artifact identity differs from plan/ledger"
            )
        verify_plan_inputs(path, row, provenance)
        artifact_status = trial.eval["completion_status"]
        if ledger_status == "INFRA_ERROR":
            trial.error = (
                terminal.get("error")
                or terminal.get("reason")
                or "supervisor recorded infrastructure error"
            )
        elif ledger_status not in TERMINAL:
            trial.status, trial.error = (
                "PARTIAL",
                "awaiting terminal supervisor transition",
            )
        elif ledger_status != artifact_status:
            trial.status, trial.error = (
                "INTEGRITY_ERROR",
                f"ledger {ledger_status} differs from artifact {artifact_status}",
            )
        lifecycle = read(path / "result.json").get("lifecycle", [])
        trial.started |= any(state.get("state") == "RUNNING" for state in lifecycle)
        trial.measurement.finish(row["config"]["episode"]["horizon_steps"], trial.eval)
        trial.fault = trial.measurement.fault.copy()
        stored = read(path / "faults.json")
        if isinstance(stored, dict):
            stored = stored.get("faults", [stored] if "family" in stored else [])
        if stored:
            trial.fault.update(stored[0])
        trial.performance = read(path / "performance.json")
        trial.performance["trace_bytes"] = (path / "trace.jsonl.gz").stat().st_size
        mismatch = trial.eval["online_offline_equal"] is False
        missing = (
            trial.eval["online_offline_equal"] is None
            and row.get("benchmark_mode") != "B0"
        )
        if mismatch or (missing and trial.status == "COMPLETED"):
            issue = "ONLINE_OFFLINE_MISMATCH" if mismatch else "ONLINE_RESULTS_MISSING"
            trial.error = (trial.error + "; " if trial.error else "") + issue
    except (OSError, ValueError, KeyError, TypeError) as exc:
        trial.status, trial.error, trial.eval, trial.fault = (
            "INTEGRITY_ERROR",
            str(exc),
            {},
            {},
        )
    return trial


def load_campaign(directory):
    directory = Path(directory)
    plan = read_plan(directory / "campaign.plan.json")
    rows = list(plan["runs"])
    if plan.get("plan_mode") == "soak_generator":
        for i, value in enumerate(lines(directory / "generator.rows.jsonl")):
            appended = {k: v for k, v in value.items() if k != "row_hash"}
            expected = soak_row(plan["provenance"], i, plan.get("suite_design"))
            if value.get("row_hash") != digest(appended) or appended != expected:
                raise CellIOError(
                    "RUN_PLAN_HASH", f"Soak row {i} differs from the frozen generator"
                )
            rows.append(appended)
    planned = {row["run_id"] for row in rows}
    if len(planned) != len(rows):
        raise CellIOError("RUN_PLAN_DUPLICATE", "duplicate planned run")
    histories = attempt_histories(lines(directory / "ledger.jsonl"), planned)
    trials, retries = [], []
    for row in rows:
        records = histories[row["run_id"]]
        trials.append(
            read_trial(
                directory, row, records[0] if records else (), plan["provenance"]
            )
        )
        for index, retry in enumerate(records[1:], start=1):
            trial = read_trial(directory, row, retry, plan["provenance"])
            retries.append(
                {
                    "run_id": row["run_id"],
                    "attempt_id": trial.attempt_id,
                    "attempt_index": index,
                    "parent_attempt_id": retry[0]["parent_attempt_id"],
                    "retry_reason": retry[0]["retry_reason"],
                    "states": [s["state"] for s in retry],
                    "status": trial.status,
                    "integrity": trial.eval.get("source_digest"),
                    "artifact_status": trial.eval.get("completion_status"),
                    "error": trial.error,
                }
            )
    return plan, trials, retries


RATES = {
    "start_rate": ("N_start", "N_plan"),
    "evaluable_rate": ("N_eval", "N_plan"),
    "trigger_rate": ("N_trigger", "N_faultplan"),
    "activation_rate": ("N_activate", "N_trigger"),
    "realization_given_activation": ("N_realized", "N_activate"),
    "realization_per_plan": ("N_realized", "N_faultplan"),
    "valid_fault_rate": ("N_validfault", "N_faultplan"),
    "infrastructure_error_rate": ("N_error", "N_plan"),
    "path_recovery_rate": ("N_clear", "N_recovery_eligible"),
    "task_success_rate": ("N_success", "N_plan"),
}


def group_metrics(trials, bootstrap=False):
    counts = Counter()
    blocks = defaultdict(Counter)
    for t in trials:
        flags = t.flags()
        counts.update(flags)
        blocks[t.row["initial_state_id"]].update(flags)
    ids = [t.attempt_id for t in trials if t.attempt_id and t.eval]
    missing = Counter(t.error or t.status for t in trials if not t.evaluable)
    rates = {}
    for key, (n, d) in RATES.items():
        excluded = Counter()
        for t in trials:
            if not t.flags()[d]:
                excluded[t.error or (d + " eligibility not met")] += 1
        rates[key] = ratio(counts[n], counts[d], ids, dict(excluded))
        if bootstrap:
            rates[key]["bootstrap"] = block_bootstrap(
                [(v[n], v[d]) for v in blocks.values()]
            )
    outcome = Counter(t.outcome for t in trials)
    compliance = Counter(
        t.eval.get("compliance_verdict", "UNAVAILABLE")
        if t.status == "COMPLETED" and t.verified_decision
        else "UNAVAILABLE"
        for t in trials
    )
    return {
        "counts": dict(counts),
        "rates": rates,
        "non_evaluable_reasons": dict(missing),
        "task_distribution": {
            k: ratio(outcome[k], len(trials), ids)
            for k in (
                "SUCCESS",
                "CONTROLLED_ABORT",
                "FAILED",
                "UNKNOWN",
                "ERROR",
                "NOT_STARTED",
            )
        },
        "compliance_distribution": {
            k: ratio(compliance[k], len(trials), ids)
            for k in ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "UNAVAILABLE")
        },
    }
