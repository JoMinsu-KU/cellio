"""Own worker processes, their deadlines and the durable attempt ledger."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from cellio.artifacts import verify_artifact
from cellio.environment import data_root, lock_context
from cellio.io import CellIOError, digest, write_json
from .campaign import append_line, exclusive, read_plan, soak_row


TERMINAL = {"COMPLETED", "CANCELLED", "INFRA_ERROR", "SKIPPED_PRECONDITION"}


def lines(path):
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def attempt(directory, row, ctx, index=0, parent=None, reason=None):
    ident = str(uuid4())
    return {
        "run_id": row["run_id"],
        "attempt_id": ident,
        "attempt_index": index,
        "parent_attempt_id": parent,
        "retry_reason": reason,
    }


def transition(directory, attempt, state, **fields):
    append_line(
        directory / "ledger.jsonl",
        {**attempt, "state": state, "unix_ns": time.time_ns(), **fields},
    )


def job(directory, row, entry, ctx):
    cfg = row["config"]
    backend = "mock" if cfg["profile"] == "mock_lockstep" else "isaac62"
    options = {}
    if backend == "isaac62":
        root = data_root() / "assets"
        options = {
            "headless": not row.get("gui", False),
            "asset_root": str(root),
            "robot_asset": str(root / ctx["assets"]["entry"]),
        }
        calibration = data_root() / "locks" / "robot_profile.lock.json"
        if calibration.exists():
            options["robot_profile"] = json.loads(
                calibration.read_text(encoding="utf-8")
            )
    return {
        **ctx,
        "config": cfg,
        "directory": str((directory / row["run_id"] / entry["attempt_id"]).resolve()),
        "run_id": row["run_id"],
        "attempt_id": entry["attempt_id"],
        "initial_state": row["initial_state"],
        "backend": backend,
        "benchmark_mode": row.get("benchmark_mode"),
        "test_control": row.get("test_control"),
        "plant_options": options,
    }


class WorkerProcess:
    def __init__(self, directory, descriptor):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.heartbeat = directory / "heartbeat.json"
        self.results = directory / "results.jsonl"
        self.inbox = directory / "inbox.jsonl"
        self.cancel = directory / "cancel"
        self.result_path = directory / "result.json"
        if descriptor.get("inbox_path") is True:
            self.inbox.touch()
            descriptor = {**descriptor, "inbox_path": str(self.inbox)}
        envelope = {
            **descriptor,
            "heartbeat_path": str(self.heartbeat),
            "results_path": str(self.results),
            "result_path": str(self.result_path),
            "cancel_path": str(self.cancel),
        }
        path = directory / "job.json"
        write_json(path, envelope)
        self.started = time.monotonic()
        self.last_progress = self.started
        self.last_seen = None
        self.running_at = None
        self.last_state = None
        self.state_at = self.started
        with (directory / "worker.log").open("wb") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "cellio.runtime.worker", str(path)],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        write_json(
            directory / "process.json",
            {"pid": self.proc.pid, "start_ns": time.time_ns()},
        )

    def poll(self):
        if self.proc.poll() is not None:
            return None
        now = time.monotonic()
        beat = {}
        if self.heartbeat.is_file():
            beat = json.loads(self.heartbeat.read_text(encoding="utf-8"))
        state = beat.get("state", "BOOTING")
        key = (beat.get("attempt_id"), state, beat.get("step"))
        if self.last_seen != key:
            self.last_progress = now
            self.last_seen = key
        if self.last_state != state:
            self.state_at = now
            self.running_at = now if state == "RUNNING" else None
            self.last_state = state
        if state == "RUNNING" and now - self.last_progress > 60:
            raise CellIOError(
                "SIM_STALL", "No physics progress for 60 seconds", exit_code=4
            )
        if self.running_at and now - self.running_at > 1800:
            raise CellIOError(
                "RUN_WALL_TIMEOUT", "RUNNING exceeded 1800 seconds", exit_code=4
            )
        limits = {"BOOTING": 900, "FINALIZING": 60, "CLOSED": 20}
        if state in limits and now - self.state_at > limits[state]:
            raise CellIOError(
                "ENV_BOOT_TIMEOUT" if state == "BOOTING" else "RUN_WORKER_TIMEOUT",
                f"{state} exceeded {limits[state]} seconds",
                exit_code=4,
            )
        return beat

    def stop(self):
        if self.proc.poll() is None:
            self.cancel.touch()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def run_group(directory, rows, entries, ctx):
    jobs = [job(directory, r, e, ctx) for r, e in zip(rows, entries, strict=True)]
    descriptor = {"episode": jobs[0]} if len(jobs) == 1 else {"episodes": jobs}
    worker = WorkerProcess(
        directory / ".workers" / entries[0]["attempt_id"], descriptor
    )
    for e in entries:
        transition(directory, e, "BOOTING", pid=worker.proc.pid)
    last = {}
    error = None
    try:
        while worker.proc.poll() is None:
            beat = worker.poll()
            if beat:
                for e in entries:
                    state = beat.get("state")
                    if (
                        e["attempt_id"] == beat.get("attempt_id")
                        and state
                        and state != last.get(e["attempt_id"])
                    ):
                        last[e["attempt_id"]] = state
                        transition(directory, e, state, step=beat.get("step"))
            time.sleep(0.2)
    except (CellIOError, KeyboardInterrupt) as exc:
        error = getattr(exc, "code", "CANCELLED")
        worker.stop()
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        worker.stop()
        results = []
        summaries = lines(worker.results) if worker.results.is_file() else []
        if len(jobs) == 1 and worker.result_path.is_file():
            summaries.append(json.loads(worker.result_path.read_text(encoding="utf-8")))
        by_attempt = {s.get("attempt_id"): s for s in summaries}
        for j, e in zip(jobs, entries, strict=True):
            status = by_attempt.get(e["attempt_id"], {})
            try:
                proof = verify_artifact(j["directory"])
                state = proof["completion_status"]
                if state == "COMPLETED" and (
                    error or worker.proc.returncode not in (0, None)
                ):
                    state = "INFRA_ERROR"
                    proof = {
                        **proof,
                        "completion_status": state,
                        "error": error or "WORKER_EXIT_ERROR",
                    }
                results.append(proof)
            except (CellIOError, OSError) as exc:
                state = "INFRA_ERROR"
                results.append({"completion_status": state, "error": status.get("reason") or str(exc),
                                "artifact_error": str(exc)})
            transition(
                directory,
                e,
                state,
                worker_exit=worker.proc.returncode,
                error=error or results[-1].get("error"),
                **{key: status[key] for key in ("end_step", "last_valid_snapshot",
                                                "last_successful_advance", "reason") if key in status},
            )
    return results


def history(directory):
    attempts = {}
    for item in lines(directory / "ledger.jsonl"):
        attempts[item["attempt_id"]] = item
    by_run = {}
    for e in attempts.values():
        by_run.setdefault(e["run_id"], []).append(e)
    return by_run


def _orphan_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            status = ctypes.c_ulong()
            return bool(
                kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(status))
                and status.value == 259
            )
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reconcile(directory):
    ledger = lines(directory / "ledger.jsonl")
    pids = {e["attempt_id"]: e["pid"] for e in ledger if e.get("pid")}
    for records in history(directory).values():
        for e in records:
            if e["state"] in TERMINAL:
                continue
            if _orphan_alive(pids.get(e["attempt_id"])):
                raise CellIOError(
                    "RUN_PLAN_LOCKED",
                    "An existing worker process is still alive",
                    exit_code=4,
                )
            keys = (
                "run_id",
                "attempt_id",
                "attempt_index",
                "retry_reason",
                "parent_attempt_id",
            )
            entry = {k: e.get(k) for k in keys}
            try:
                state = verify_artifact(directory / e["run_id"] / e["attempt_id"])[
                    "completion_status"
                ]
            except CellIOError:
                state = "INFRA_ERROR"
            transition(
                directory,
                entry,
                state,
                error="WORKER_LOST" if state == "INFRA_ERROR" else None,
            )


def execute_plan(path, *, resume=False, retry_failed=False):
    path = Path(path).resolve()
    directory = path.parent
    plan = read_plan(path)
    if plan["suite_id"] == "oracle-v1":
        from cellio.acceptance import checkout, evaluate_gold

        cases = json.loads(
            (checkout() / "fixtures/oracle/gold.json").read_text(encoding="utf-8")
        )
        if [digest(c) for c in cases] != [row["case_hash"] for row in plan["runs"]]:
            raise CellIOError(
                "RUN_PLAN_CHANGED", "Frozen oracle cases have changed", exit_code=4
            )
        with exclusive(directory):
            proof = evaluate_gold()
            write_json(directory / "oracle.results.json", proof)
        if not proof["passed"]:
            raise CellIOError(
                "ORACLE_MISMATCH", "A frozen expected result differs", exit_code=5
            )
        return {
            "cases": len(cases),
            "passed": proof["passed"],
            "corpus_sha256": proof["corpus_sha256"],
        }
    ctx, identity = lock_context(
        "isaac62_lockstep",
        measured=plan["suite_id"] not in {"commission-v1", "pilot-v1", "boundaries-v1"},
    )
    if identity != plan["provenance"]:
        raise CellIOError(
            "RUN_PLAN_CHANGED", "Current locks differ from the frozen plan", exit_code=3
        )
    if plan["suite_id"] in {"canonical-v1", "overhead-v1", "reset-v1", "soak-v1"}:
        estimate = ctx["protocol"].get("pilot_bytes_per_step")
        if not isinstance(estimate, (int, float)) or estimate <= 0:
            raise CellIOError(
                "ENV_DISK_BUDGET", "Missing ten-pilot storage measurement", exit_code=3
            )
        horizon_sum = sum(r["config"]["episode"]["horizon_steps"] for r in plan["runs"])
        if plan["plan_mode"] == "soak_generator":
            horizon_sum = (
                plan["generator"]["minimum_completed"] + plan["generator"]["warmup"]
            ) * 7200
        needed = 1.5 * estimate * horizon_sum + 10 * 1024**3
        if shutil.disk_usage(directory).free < needed:
            raise CellIOError(
                "ENV_DISK_SPACE",
                f"Plan requires at least {needed / 1024**3:.1f} GiB free",
                exit_code=3,
            )
    gpu_lock = data_root() / "gpu-worker"
    gpu_lock.mkdir(parents=True, exist_ok=True)
    with exclusive(gpu_lock), exclusive(directory):
        if (directory / "ledger.jsonl").exists() and not resume:
            raise CellIOError(
                "RUN_RESUME_REQUIRED", "Use campaign resume for an existing ledger"
            )
        reconcile(directory)
        if plan["plan_mode"] == "soak_generator":
            return execute_soak(directory, plan, ctx)
        seen = history(directory)
        groups = []
        for row in plan["runs"]:
            if row.get("chain_slot") == "post_reset":
                groups[-1].append(row)
            else:
                groups.append([row])
        for rows in groups:
            prior = [seen.get(r["run_id"], []) for r in rows]
            if any(prior):
                if not retry_failed or all(
                    p and p[-1]["state"] == "COMPLETED" for p in prior
                ):
                    continue
            entries = []
            for r, p in zip(rows, prior, strict=True):
                e = attempt(
                    directory,
                    r,
                    ctx,
                    len(p),
                    p[-1]["attempt_id"] if p else None,
                    "requested" if p else None,
                )
                transition(directory, e, "PLANNED")
                entries.append(e)
            outcome = run_group(directory, rows, entries, ctx)
            if any(p["completion_status"] == "INFRA_ERROR" for p in outcome):
                raise CellIOError(
                    "RUN_ATTEMPT_FAILED",
                    f"Review the evidence under {directory}",
                    exit_code=4,
                )
    return {
        "suite_id": plan["suite_id"],
        "runs": len(plan["runs"]),
        "directory": str(directory),
    }


def execute_soak(directory, plan, ctx):
    # A restarted process starts a new soak; results of separate workers cannot be pooled.
    if history(directory):
        raise CellIOError(
            "SOAK_INTERRUPTED",
            "An interrupted soak requires a fresh campaign directory",
            exit_code=4,
        )
    worker = WorkerProcess(directory / ".workers" / str(uuid4()), {"inbox_path": True})
    n = 0
    start = None
    completed = 0
    rule = plan["generator"]
    try:
        while (
            completed < rule["minimum_completed"]
            or start is None
            or time.monotonic() - start < rule["minimum_seconds"]
        ):
            if n == rule["warmup"]:
                start = time.monotonic()
            row = soak_row(plan["provenance"], n)
            append_line(
                directory / "generator.rows.jsonl", {**row, "row_hash": digest(row)}
            )
            entry = attempt(directory, row, ctx)
            transition(directory, entry, "PLANNED")
            transition(directory, entry, "BOOTING", pid=worker.proc.pid)
            j = job(directory, row, entry, ctx)
            append_line(worker.inbox, {"episode": j})
            while len(lines(worker.results)) <= n:
                if worker.proc.poll() is not None:
                    raise CellIOError(
                        "WORKER_LOST",
                        f"Soak worker exited {worker.proc.returncode}",
                        exit_code=4,
                    )
                if (
                    start is not None
                    and time.monotonic() - start >= rule["limit_seconds"]
                ):
                    raise CellIOError(
                        "SOAK_INCOMPLETE", "Soak exceeded protection limit", exit_code=4
                    )
                worker.poll()
                time.sleep(0.2)
            result = verify_artifact(j["directory"])
            transition(directory, entry, result["completion_status"])
            if result["completion_status"] != "COMPLETED":
                raise CellIOError("SOAK_FAILED", "A soak episode failed", exit_code=4)
            if start is not None:
                completed += 1
            n += 1
        append_line(worker.inbox, {"stop": True})
        worker.proc.wait(timeout=60)
        if worker.proc.returncode:
            raise CellIOError("SOAK_FAILED", "Soak worker failed to close", exit_code=4)
    finally:
        worker.stop()
    summary = {
        "completed": completed,
        "warmup": rule["warmup"],
        "wall_seconds": time.monotonic() - start,
    }
    write_json(directory / "soak.result.json", summary)
    return summary
