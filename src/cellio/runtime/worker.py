"""Spawn target for isolated attempts and sequential hard-reload workers."""

import argparse
import ctypes
import json
import os
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread

from cellio.io import CellIOError, canonical, file_hash, write_json

from .episode import make_plant, run_episode


def _rss_bytes():
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                *(
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak_ws",
                        "ws",
                        "peak_paged",
                        "paged",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ),
            ]

        data = Counters()
        data.cb = ctypes.sizeof(data)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.argtypes = []
        process.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        if query(process(), ctypes.byref(data), data.cb):
            return data.ws
        return None
    try:
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf(
            "SC_PAGE_SIZE"
        )
    except (OSError, ValueError, IndexError):
        return None


class ResourceSampler:
    def __init__(self):
        self.pid = os.getpid()
        self.lock = Lock()
        self.stop = Event()
        self.nv = None
        self.handle = None
        self.dxgi = None
        self.device_memory = None
        self.epoch = 0
        if os.name == "nt":
            from .wddm import ProcessVideoMemory

            try:
                self.dxgi = ProcessVideoMemory()
            except (OSError, AttributeError):
                pass
        try:
            import pynvml as nv

            nv.nvmlInit()
            self.nv, self.handle = nv, nv.nvmlDeviceGetHandleByIndex(0)
        except (ImportError, OSError, RuntimeError):
            pass
        except Exception:
            # WDDM and GPU-free CI may not expose a per-process memory accounting API.
            self.nv, self.handle = None, None
        if self.handle is None and os.name == "nt":
            from .wddm import DeviceMemory

            try:
                self.device_memory = DeviceMemory()
            except (OSError, AttributeError):
                pass
        self.reset()
        self.thread = Thread(
            target=self._sample_loop, name="cellio-resource-sampler", daemon=True
        )
        self.thread.start()

    def reset(self):
        with self.lock:
            self.epoch += 1
            self.peak_rss = self.peak_vram = self.latest_rss = self.latest_vram = None
            self.latest_device = None
            self.peak_device = None
            self.vram_source = "process_measurement_unavailable"
            self.samples = 0

    def _sample_loop(self):
        while not self.stop.wait(1):
            self._sample()

    def _sample(self):
        with self.lock:
            epoch = self.epoch
        rss = _rss_bytes()
        vram = device = None
        if self.device_memory is not None:
            device = self.device_memory.read()
        method = None
        if self.dxgi is not None:
            vram = self.dxgi.read()
            if vram is not None:
                method = "dxgi_process_local"
        if self.handle is not None:
            try:
                device = self.nv.nvmlDeviceGetMemoryInfo(self.handle).used
                rows = self.nv.nvmlDeviceGetComputeRunningProcesses(self.handle)
                own = [
                    p.usedGpuMemory
                    for p in rows
                    if p.pid == self.pid
                    and isinstance(p.usedGpuMemory, int)
                    and 0 <= p.usedGpuMemory <= device
                ]
                if own and vram is None:
                    vram = max(own)
                    method = "nvml_process"
            except Exception:
                pass
        with self.lock:
            if self.epoch != epoch:
                return
            self.latest_rss, self.latest_vram, self.latest_device = rss, vram, device
            if rss is not None:
                self.peak_rss = max(self.peak_rss or 0, rss)
            if vram is not None:
                self.peak_vram = max(self.peak_vram or 0, vram)
                self.vram_source = method
            if device is not None:
                self.peak_device = max(self.peak_device or 0, device)
            self.samples += 1

    def read(self):
        self._sample()
        with self.lock:
            return {
                "peak_rss_bytes": self.peak_rss,
                "peak_vram_bytes": self.peak_vram,
                "end_rss_bytes": self.latest_rss,
                "end_vram_bytes": self.latest_vram,
                "end_gpu_device_used_bytes": self.latest_device,
                "peak_gpu_device_used_bytes": self.peak_device,
                "device_memory_measurement": "nvml_v1_used_including_reserved"
                if self.latest_device is not None
                else "device_measurement_unavailable",
                "memory_samples": self.samples,
                "vram_measurement": self.vram_source,
                "vram_adapter": self.dxgi.name if self.dxgi is not None else None,
            }

    def close(self):
        self.stop.set()
        self.thread.join()
        if self.dxgi:
            self.dxgi.close()
        if self.nv:
            self.nv.nvmlShutdown()
        if self.device_memory:
            self.device_memory.close()


class Heartbeat:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = Lock()
        self.stop = Event()
        self.status = {
            "pid": os.getpid(),
            "state": "BOOTING",
            "step": None,
            "run_id": None,
            "attempt_id": None,
            "episode_index": None,
            "generation": None,
            "last_progress_ns": time.monotonic_ns(),
        }
        self._write()
        self.thread = Thread(
            target=self._background, name="cellio-heartbeat", daemon=True
        )
        self.thread.start()

    def _write(self):
        with self.lock:
            write_json(
                self.path,
                {
                    **self.status,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "monotonic_ns": time.monotonic_ns(),
                },
            )

    def _background(self):
        while not self.stop.wait(2):
            self._write()

    def bind(self, episode, index, generation):
        with self.lock:
            self.status.update(
                run_id=episode["run_id"],
                attempt_id=episode["attempt_id"],
                episode_index=index,
                generation=generation,
                last_progress_ns=time.monotonic_ns(),
                step=None,
            )

    def update(self, state, step):
        with self.lock:
            changed = self.status["state"] != state
            if changed or self.status["step"] != step:
                self.status["last_progress_ns"] = time.monotonic_ns()
            self.status.update(state=state, step=step)
        if changed:
            self._write()

    def close(self):
        self.stop.set()
        self.thread.join(2)


class SharedPlant:
    def __init__(self):
        self.key = self.plant = None
        self.previous_attempt = None

    def record_closed(self, result, generation):
        directory = Path(result["directory"]).resolve()
        manifest_path = directory / "manifest.final.json"
        marker_path = directory / "COMPLETE"
        marker = marker_path.read_text(encoding="ascii").strip()
        manifest = json.loads(manifest_path.read_bytes())
        identity = (
            result["run_id"],
            result["attempt_id"],
            result["episode_id"],
            generation,
        )
        finalized = (
            marker == file_hash(manifest_path)
            and tuple(
                manifest.get(k)
                for k in ("run_id", "attempt_id", "episode_id", "generation")
            )
            == identity
            and manifest.get("last_record_hash") == result.get("artifact_digest")
        )
        if not finalized:
            raise ValueError(
                "Previous attempt did not commit a matching final manifest"
            )
        self.previous_attempt = {
            "attempt_id": result["attempt_id"],
            "episode_id": result["episode_id"],
            "generation": generation,
            "directory": str(directory),
            "artifact_digest": result["artifact_digest"],
            "manifest_sha256": marker,
            "isolation_before_teardown": result.get("isolation_before_teardown"),
        }

    def acquire(self, backend, options, plugin):
        key = canonical(
            {
                "backend": backend,
                "options": options or {},
                "scene_runtime": f"{type(plugin).__module__}.{type(plugin).__qualname__}",
            }
        )
        if self.plant is None:
            self.plant, self.key = make_plant(backend, options, plugin), key
        elif self.key != key:
            raise ValueError(
                "Hard reload requires one fixed runtime backend and option set"
            )
        return self.plant

    def close(self):
        plant, self.plant = self.plant, None
        if plant is not None:
            plant.close()


def _inbox(path, beat, cancel):
    beat.update("IDLE", None)
    with Path(path).open("rb") as stream:
        while not cancel.exists():
            offset = stream.tell()
            line = stream.readline()
            if line.endswith(b"\n"):
                item = json.loads(line)
                if item.get("stop"):
                    return
                yield item["episode"]
                beat.update("IDLE", None)
            else:
                stream.seek(offset)
                time.sleep(0.1)


def _append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute_job(path, *, process_boundary=False):
    from .campaign import exclusive

    spec = json.loads(path.read_text(encoding="utf-8-sig"))
    lease_directory = spec.get("plant_lease_directory")
    lease = nullcontext()
    if lease_directory is not None:
        directory = Path(lease_directory)
        directory.mkdir(parents=True, exist_ok=True)
        lease = exclusive(directory)
    try:
        # The OS owns the lock until normal exit or native Kit fast shutdown.
        with lease:
            if process_boundary:
                from cellio.adapters.isaac62.application import worker_main

                return worker_main(_execute_job, path, spec)
            return _execute_job(path, spec)
    except CellIOError as exc:
        if exc.code != "RUN_PLAN_LOCKED":
            raise
        write_json(
            Path(spec.get("result_path", path.with_suffix(".result.json"))),
            {
                "completion_status": "INFRA_ERROR",
                "reason": "RUN_PLANT_LOCKED",
                "message": "Another plant worker owns the simulator lease",
                "episode_count": 0,
            },
        )
        return 4


def _execute_job(path, spec):

    def output(key, suffix):
        return Path(spec.get(key, path.with_suffix(suffix)))

    beat = Heartbeat(output("heartbeat_path", ".heartbeat.json"))
    cancel = output("cancel_path", ".cancel")
    result_path = output("result_path", ".result.json")
    rows_path = output("results_path", ".results.jsonl")
    resource = ResourceSampler()
    shared = None
    grouped = "episodes" in spec or "inbox_path" in spec
    results = 0
    failed = False
    last_reason = None
    last_generation = None
    identifiers = set()
    directories = set()
    message = None
    try:
        if grouped:
            shared = SharedPlant()
            episodes = (
                _inbox(spec["inbox_path"], beat, cancel)
                if "inbox_path" in spec
                else spec["episodes"]
            )
        else:
            episodes = (spec["episode"],)
        for index, episode in enumerate(episodes):
            if cancel.exists() and grouped:
                break
            job = dict(episode)
            generation = job.setdefault("generation", index)
            if (
                type(generation) is not int
                or generation < 0
                or (last_generation is not None and generation <= last_generation)
            ):
                raise ValueError("Hard-reload generations must strictly increase")
            identity = (job["run_id"], job["attempt_id"])
            directory = Path(job["directory"]).resolve()
            if identity in identifiers or directory in directories:
                raise ValueError(
                    "Each grouped attempt requires a unique identity and directory"
                )
            identifiers.add(identity)
            directories.add(directory)
            last_generation = generation
            beat.bind(job, index, generation)
            resource.reset()
            # Resolve explicit registered bindings from the requested asset manifest.
            assets = job.get("assets", {})
            options = dict(job.pop("plant_options", {}) or {})
            for key in ("asset_root", "robot_asset", "robot_profile"):
                if key in assets and key not in options:
                    options[key] = assets[key]
            result = run_episode(
                **job,
                plant_options=options,
                hardware_session=shared,
                resource_sampler=resource,
                cancel_requested=cancel.exists,
                heartbeat=beat.update,
            )
            compact = {
                k: v
                for k, v in result.items()
                if k not in ("performance", "faults", "requirements")
            }
            compact["generation"] = generation
            results += 1
            if grouped:
                _append(rows_path, compact)
            else:
                write_json(result_path, compact)
            if result["completion_status"] == "INFRA_ERROR":
                failed = True
                last_reason = result["reason"]
                break
            if grouped:
                shared.record_closed(result, generation)
            if (
                result["completion_status"] == "CANCELLED"
                and result.get("reason") != "TEST_RESET_CANCEL"
            ):
                last_reason = result["reason"]
                break
    except (Exception, KeyboardInterrupt) as exc:
        failed = True
        last_reason = getattr(exc, "code", "WORKER_ERROR")
        message = str(exc)
        write_json(
            result_path,
            {
                "completion_status": "INFRA_ERROR",
                "reason": last_reason,
                "message": str(exc),
                "episode_count": results,
            },
        )
    finally:
        try:
            if shared:
                shared.close()
        except Exception as exc:
            failed = True
            last_reason = getattr(exc, "code", "WORKER_CLOSE_ERROR")
        finally:
            resource.close()
            if grouped:
                write_json(
                    result_path,
                    {
                        "episode_count": results,
                        "completion_status": "INFRA_ERROR"
                        if failed
                        else "CANCELLED"
                        if cancel.exists() or last_reason
                        else "COMPLETED",
                        "reason": last_reason,
                        "message": message,
                    },
                )
            beat.update("EXITED", None)
            beat.close()
    return (
        4
        if failed
        else 130
        if cancel.exists() or last_reason == "USER_CANCELLED"
        else 0
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    return execute_job(parser.parse_args(argv).job, process_boundary=True)


if __name__ == "__main__":
    sys.exit(main())
