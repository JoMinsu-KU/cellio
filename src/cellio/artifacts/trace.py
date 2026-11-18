import gzip
import hashlib
import json
import os
import queue
import threading
from pathlib import Path

from cellio.io import CellIOError, atomic_write, canonical, file_hash, json_value, write_json


ZERO = "00" * 32
STOP = object()
FILES = frozenset("""
    manifest.start.json config.resolved.json environment.lock.json requirements.lock
    assets.lock.json protocol.lock.json initial_state.json initial_snapshot.json
    trace.jsonl.gz events.jsonl.gz commands.jsonl.gz faults.json requirements.json
    result.json performance.json errors.jsonl
""".split())


def trace_error(message, code="TRACE_INTEGRITY"):
    return CellIOError(code, message, exit_code=4)


def encoded(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


class AttemptWriter:
    def __init__(self, directory, *, run_id, attempt_id, episode_id, generation, config,
                 initial_state, environment, assets, protocol, requirements_lock):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=False)
        self.ids = dict(run_id=run_id, attempt_id=attempt_id, episode_id=episode_id,
                        generation=generation)
        self.previous = ZERO
        self.index = 0
        self.inbox = queue.Queue(maxsize=256)
        self.max_backlog = 0
        self.error = None
        self.finished = False
        self.started = False
        write_json(self.path/"manifest.start.json", self.ids)
        for name, value in (("config.resolved", config), ("environment.lock", environment),
                            ("assets.lock", assets), ("protocol.lock", protocol),
                            ("initial_state", initial_state)):
            write_json(self.path/(name+".json"), value)
        lock = requirements_lock
        if isinstance(lock, Path):
            lock = lock.read_bytes()
        if not isinstance(lock, (str, bytes)):
            raise TypeError("requirements_lock must contain locked requirements")
        atomic_write(self.path/"requirements.lock", lock.encode() if isinstance(lock, str) else lock)
        self.thread = threading.Thread(target=self._consume, name="cellio-trace", daemon=True)
        self.thread.start()

    def _consume(self):
        try:
            with (self.path/"trace.jsonl.gz.partial").open("xb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=1) as stream:
                    while True:
                        item = self.inbox.get()
                        if item is STOP:
                            break
                        kind, detail = item
                        if kind == "frame":
                            detail = self._frame_record(*detail)
                            kind = "final_snapshot" if detail["tick_kind"] == "FINAL" else "tick"
                        record = {**self.ids, **detail, "schema_version": "cellio.trace/1.0",
                                  "record_type": kind, "record_index": self.index,
                                  "previous_hash": self.previous}
                        self.previous = hashlib.sha256(bytes.fromhex(self.previous) + encoded(record)).hexdigest()
                        record["record_hash"] = self.previous
                        stream.write(encoded(record) + b"\n")
                        self.index += 1
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(self.path/"trace.jsonl.gz.partial", self.path/"trace.jsonl.gz")
        except Exception as exc:
            self.error = exc

    def _enqueue(self, item):
        if self.finished:
            raise trace_error("Attempt is already closed")
        if self.error is not None:
            raise trace_error(str(self.error), "TRACE_WRITE") from self.error
        try:
            self.inbox.put(item, timeout=5)
        except queue.Full as exc:
            raise trace_error("Writer queue remained full for five seconds", "TRACE_BACKPRESSURE") from exc
        self.max_backlog = max(self.max_backlog, self.inbox.qsize())

    def start(self, initial_snapshot):
        if self.started:
            raise trace_error("Duplicate attempt start")
        self.started = True
        write_json(self.path/"initial_snapshot.json", initial_snapshot)
        self._enqueue(("episode_start", {"step": None if initial_snapshot is None else 0}))

    @staticmethod
    def _frame_record(frame, fault_audit, oracle_updates, performance):
        info = json_value(frame)
        tick = info["tick"]
        receipt = info["actuation"]
        audit = json_value(fault_audit)
        if not isinstance(audit, dict):
            audit = {"reports": audit}
        audit["transitions"] = info["fault_transitions"]
        return {
            **({key: audit[key] for key in ("reused_snapshot","reused_record_step") if key in audit}
               if info["final_snapshot"] else {}),
            "tick_kind": "FINAL" if info["final_snapshot"] else "NORMAL", "step": tick["step"],
            "truth": info["truth"], "previous_advance": info["previous_advance"],
            "raw_signals": info["raw_signals"], "observed_signals": info["observed_signals"],
            "controller_called": info["controller_called"], "controller_view_hash": info["controller_view_hash"],
            "controller_output": info["controller_output"], "intents": info["intents"],
            "deliveries": info["deliveries"], "actuation": receipt,
            "applied": receipt["applied_commands"] if receipt else [],
            "actuator_targets": receipt["targets_by_actuator"] if receipt else {},
            "fault_audit": audit, "events": info["source_events"],
            "oracle_updates": json_value(oracle_updates), "performance": json_value(performance),
        }

    def record(self, frame, *, fault_audit, oracle_updates, performance):
        if not self.started:
            raise trace_error("Attempt start is missing")
        # Frames are frozen domain objects. Snapshot mutable caller-owned metadata now.
        self._enqueue(("frame", (frame, json_value(fault_audit), json_value(oracle_updates),
                                 json_value(performance))))

    def finish(self, result_dict, *, performance, faults, requirements, errors):
        result = json_value(result_dict)
        if not self.started:
            self.start(None)
        self._enqueue(("episode_end", {k: result.get(k) for k in
                                       ("completion_status", "end_step", "task_outcome", "errors")}))
        self._enqueue(STOP)
        self.finished = True
        self.thread.join(60)
        if self.thread.is_alive() or self.error is not None:
            raise trace_error(f"Trace finalization failed: {self.error}", "TRACE_FINALIZE")
        result["artifact_digest"] = self.previous
        result.update(run_id=self.ids["run_id"], attempt_id=self.ids["attempt_id"])
        write_json(self.path/"result.json", result)
        write_json(self.path/"performance.json", {**json_value(performance),
                                                  "writer_max_backlog": self.max_backlog})
        write_json(self.path/"faults.json", faults)
        write_json(self.path/"requirements.json", requirements)
        atomic_write(self.path/"errors.jsonl", b"".join(canonical(e)+b"\n" for e in errors))
        self._extract()
        listing = {f: {"size": (self.path/f).stat().st_size, "sha256": file_hash(self.path/f)}
                   for f in sorted(FILES)}
        manifest = self.path/"manifest.final.json"
        write_json(manifest, {**self.ids, "files": listing, "last_record_hash": self.previous})
        atomic_write(self.path/"COMPLETE", (file_hash(manifest)+"\n").encode("ascii"))
        return self.path

    def _extract(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            streams = {}
            for name in ("events", "commands"):
                raw = stack.enter_context((self.path/(name+".jsonl.gz.partial")).open("xb"))
                stack.callback(lambda out=raw: (out.flush(), os.fsync(out.fileno())))
                streams[name] = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0))
            for record in iter_records(self.path):
                for key, dest in (("events","events"), ("intents","commands"),
                                  ("deliveries","commands"), ("applied","commands")):
                    for item in record.get(key, ()):
                        streams[dest].write(encoded({"step": record["step"], "kind": key, "data": item})+b"\n")
        for name in ("events", "commands"):
            os.replace(self.path/(name+".jsonl.gz.partial"), self.path/(name+".jsonl.gz"))


def iter_records(path):
    with gzip.open(Path(path)/"trace.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def frames(path):
    for record in iter_records(path):
        if record["record_type"] in ("tick", "final_snapshot"):
            yield {**record, "tick": record["truth"]["tick"], "source_events": record["events"],
                   "fault_transitions": record["fault_audit"]["transitions"],
                   "final_snapshot": record["tick_kind"] == "FINAL"}


def verify_artifact(directory):
    path = Path(directory)
    try:
        return _verify(path)
    except CellIOError:
        raise
    except (OSError, KeyError, TypeError, ValueError, EOFError) as exc:
        raise trace_error(str(exc)) from exc


def _verify(path):
    manifest_path = path/"manifest.final.json"
    if (path/"COMPLETE").read_bytes() != (file_hash(manifest_path)+"\n").encode("ascii"):
        raise trace_error("Manifest digest differs from COMPLETE")
    manifest = read_json(manifest_path)
    if set(manifest["files"]) != FILES:
        raise trace_error("Manifest file inventory is incomplete or unknown")
    for name, meta in manifest["files"].items():
        f = path/name
        if f.resolve().parent != path.resolve() or f.is_symlink():
            raise trace_error(f"Artifact path escapes attempt: {name}")
        if f.stat().st_size != meta["size"] or file_hash(f) != meta["sha256"]:
            raise trace_error(f"File checksum mismatch: {name}")
    result = read_json(path/"result.json")
    horizon = read_json(path/"config.resolved.json")["episode"]["horizon_steps"]
    ids = read_json(path/"manifest.start.json")
    previous, count, ticks, final_step, ended = ZERO, 0, 0, None, False
    issued = set()
    for record in iter_records(path):
        stated = record.pop("record_hash")
        wanted = hashlib.sha256(bytes.fromhex(previous)+encoded(record)).hexdigest()
        if (record["previous_hash"] != previous or stated != wanted or record["record_index"] != count
                or any(record[k] != v for k,v in ids.items())
                or record["schema_version"] != "cellio.trace/1.0"):
            raise trace_error(f"Invalid record identity or chain at {count}")
        kind = record["record_type"]
        if ended or ((kind == "episode_start") != (count == 0)):
            raise trace_error("Invalid stream boundary")
        if kind in ("tick", "final_snapshot"):
            k = record["step"]
            tick = record["truth"]["tick"]
            advance = record["previous_advance"]
            if (tick["step"] != k or tick["generation"] != ids["generation"]
                    or tick["episode_id"] != ids["episode_id"] or final_step is not None):
                raise trace_error("Inconsistent snapshot identity")
            if k > 0 and (not advance or not advance["success"] or
                          (advance["from_step"], advance["to_step"]) != (k-1,k)):
                raise trace_error("Missing successful physics advance")
            if k == 0 and advance is not None:
                raise trace_error("Unexpected initial advance")
            if kind == "tick":
                if k != ticks or record["tick_kind"] != "NORMAL":
                    raise trace_error("Missing or out-of-order decision tick")
                ticks += 1
            else:
                final_step = k
                if (record["tick_kind"] != "FINAL" or record["actuation"] is not None
                        or record["controller_called"] or record["raw_signals"] or record["observed_signals"]):
                    raise trace_error("Invalid final snapshot actions")
            if bool(record["controller_view_hash"]) != bool(record["controller_called"]):
                raise trace_error("Controller input digest missing or unexpected")
            for intent in record["intents"]:
                cid = intent["command_id"]
                if cid in issued or intent["issued_step"] != k:
                    raise trace_error("Invalid issued command identity")
                issued.add(cid)
            if any(d["intent"]["command_id"] not in issued for d in record["deliveries"]):
                raise trace_error("Delivery references missing command")
            applied = record["applied"]
            receipt = record["actuation"]
            if receipt and (applied != receipt["applied_commands"] or record["actuator_targets"] != receipt["targets_by_actuator"]):
                raise trace_error("Actuation projection mismatch")
            if any(a["command_id"] not in issued for a in applied):
                raise trace_error("Application references missing command")
        elif kind == "episode_end":
            ended = True
            if any(record[k] != result.get(k) for k in ("completion_status","end_step","task_outcome")):
                raise trace_error("Trace result differs from result.json")
        elif kind != "episode_start":
            raise trace_error("Unknown record type")
        previous = stated
        count += 1
    if (not ended or previous != result["artifact_digest"] or previous != manifest["last_record_hash"]
            or result["run_id"] != ids["run_id"] or result["attempt_id"] != ids["attempt_id"]):
        raise trace_error("Missing closure or result digest mismatch")
    if result["completion_status"] == "COMPLETED" and (ticks,final_step,result["end_step"]) != (horizon,horizon,horizon):
        raise trace_error("Completed attempt lacks full horizon")
    if result["completion_status"] not in {"COMPLETED","INFRA_ERROR","CANCELLED"}:
        raise trace_error("Unknown completion status")
    return {"valid": True, "run_id": ids["run_id"], "attempt_id": ids["attempt_id"],
            "completion_status": result["completion_status"], "records": count,
            "ticks": ticks, "end_step": result["end_step"], "artifact_digest": previous}
