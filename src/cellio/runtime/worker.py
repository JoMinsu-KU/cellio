"""Spawn target for isolated attempts and sequential hard-reload workers."""

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time

from cellio.io import canonical, write_json
from .episode import make_plant, run_episode


def _rss_bytes():
    if os.name=="nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[("cb",wintypes.DWORD),("page_faults",wintypes.DWORD),
                      *((name,ctypes.c_size_t) for name in ("peak_ws","ws","peak_paged","paged","peak_nonpaged",
                                                           "nonpaged","pagefile","peak_pagefile"))]
        data=Counters()
        data.cb=ctypes.sizeof(data)
        process=ctypes.windll.kernel32.GetCurrentProcess
        process.argtypes=[]
        process.restype=wintypes.HANDLE
        query=ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        query.restype=wintypes.BOOL
        if query(process(),ctypes.byref(data),data.cb):
            return data.ws
        return None
    try:
        return int(Path("/proc/self/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")
    except (OSError,ValueError,IndexError):
        return None


class ResourceSampler:
    def __init__(self):
        self.pid=os.getpid()
        self.lock=Lock()
        self.stop=Event()
        self.nv=None
        self.handle=None
        try:
            import pynvml as nv
            nv.nvmlInit()
            self.nv,self.handle=nv,nv.nvmlDeviceGetHandleByIndex(0)
        except (ImportError,OSError,RuntimeError):
            pass
        except Exception:
            # WDDM and GPU-free CI may not expose a per-process memory accounting API.
            self.nv,self.handle=None,None
        self.reset()
        self.thread=Thread(target=self._sample_loop,name="cellio-resource-sampler",daemon=True)
        self.thread.start()

    def reset(self):
        with self.lock:
            self.peak_rss=self.peak_vram=self.latest_rss=self.latest_vram=None
            self.latest_device=None
            self.samples=0

    def _sample_loop(self):
        while not self.stop.wait(1):
            self._sample()

    def _sample(self):
        rss=_rss_bytes()
        vram=device=None
        if self.handle is not None:
            try:
                device=self.nv.nvmlDeviceGetMemoryInfo(self.handle).used
                rows=self.nv.nvmlDeviceGetComputeRunningProcesses(self.handle)
                own=[p.usedGpuMemory for p in rows if p.pid==self.pid and isinstance(p.usedGpuMemory,int)
                     and 0<=p.usedGpuMemory<=device]
                if own:
                    vram=max(own)
            except Exception:
                pass
        with self.lock:
            self.latest_rss,self.latest_vram,self.latest_device=rss,vram,device
            if rss is not None:
                self.peak_rss=max(self.peak_rss or 0,rss)
            if vram is not None:
                self.peak_vram=max(self.peak_vram or 0,vram)
            self.samples+=1

    def read(self):
        self._sample()
        with self.lock:
            return {"peak_rss_bytes":self.peak_rss,"peak_vram_bytes":self.peak_vram,
                    "end_rss_bytes":self.latest_rss,"end_vram_bytes":self.latest_vram,
                    "end_gpu_device_used_bytes":self.latest_device,"memory_samples":self.samples,
                    "vram_measurement":"nvml_process" if self.peak_vram is not None else "process_measurement_unavailable"}

    def close(self):
        self.stop.set()
        self.thread.join(2)
        if self.nv:
            self.nv.nvmlShutdown()


class Heartbeat:
    def __init__(self,path):
        self.path=Path(path)
        self.lock=Lock()
        self.stop=Event()
        self.status={"pid":os.getpid(),"state":"BOOTING","step":None,"run_id":None,"attempt_id":None,
                     "episode_index":None,"generation":None,"last_progress_ns":time.monotonic_ns()}
        self._write()
        self.thread=Thread(target=self._background,name="cellio-heartbeat",daemon=True)
        self.thread.start()

    def _write(self):
        with self.lock:
            write_json(self.path,{**self.status,"timestamp":datetime.now(timezone.utc).isoformat(),
                                  "monotonic_ns":time.monotonic_ns()})

    def _background(self):
        while not self.stop.wait(2):
            self._write()

    def bind(self,episode,index,generation):
        with self.lock:
            self.status.update(run_id=episode["run_id"],attempt_id=episode["attempt_id"],episode_index=index,
                               generation=generation,last_progress_ns=time.monotonic_ns(),step=None)

    def update(self,state,step):
        with self.lock:
            changed=self.status["state"]!=state
            if changed or self.status["step"]!=step:
                self.status["last_progress_ns"]=time.monotonic_ns()
            self.status.update(state=state,step=step)
        if changed:
            self._write()

    def close(self):
        self.stop.set()
        self.thread.join(2)


class SharedPlant:
    def __init__(self):
        self.key=self.plant=None

    def acquire(self,backend,options):
        key=canonical({"backend":backend,"options":options or {}})
        if self.plant is None:
            self.plant,self.key=make_plant(backend,options),key
        elif self.key!=key:
            raise ValueError("Hard reload requires one fixed runtime backend and option set")
        return self.plant

    def close(self):
        if self.plant:
            self.plant.close()


def _inbox(path,beat,cancel):
    beat.update("IDLE",None)
    with Path(path).open("rb") as stream:
        while not cancel.exists():
            offset=stream.tell()
            line=stream.readline()
            if line.endswith(b"\n"):
                item=json.loads(line)
                if item.get("stop"):
                    return
                yield item["episode"]
                beat.update("IDLE",None)
            else:
                stream.seek(offset)
                time.sleep(.1)


def _append(path,row):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical(row)+b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute_job(path):
    spec=json.loads(path.read_text(encoding="utf-8-sig"))
    def output(key,suffix):
        return Path(spec.get(key,path.with_suffix(suffix)))
    beat=Heartbeat(output("heartbeat_path",".heartbeat.json"))
    cancel=output("cancel_path",".cancel")
    result_path=output("result_path",".result.json")
    rows_path=output("results_path",".results.jsonl")
    resource=ResourceSampler()
    shared=None
    grouped="episodes" in spec or "inbox_path" in spec
    results=0
    failed=False
    last_reason=None
    message=None
    try:
        if grouped:
            shared=SharedPlant()
            episodes=_inbox(spec["inbox_path"],beat,cancel) if "inbox_path" in spec else spec["episodes"]
        else:
            episodes=(spec["episode"],)
        for index,episode in enumerate(episodes):
            if cancel.exists() and grouped:
                break
            job=dict(episode)
            generation=job.setdefault("generation",index)
            if grouped and index>0 and generation<=last_generation:
                raise ValueError("Hard-reload generations must strictly increase")
            last_generation=generation
            beat.bind(job,index,generation)
            resource.reset()
            # Resolve explicit registered bindings from the requested asset manifest.
            assets=job.get("assets",{})
            options=dict(job.pop("plant_options",{}) or {})
            for key in ("asset_root","robot_asset","robot_profile"):
                if key in assets and key not in options:
                    options[key]=assets[key]
            result=run_episode(**job,plant_options=options,hardware_session=shared,resource_sampler=resource,
                               cancel_requested=cancel.exists,heartbeat=beat.update)
            compact={k:v for k,v in result.items() if k not in ("performance","faults","requirements")}
            compact["generation"]=generation
            results+=1
            if grouped:
                _append(rows_path,compact)
            else:
                write_json(result_path,compact)
            if result["completion_status"]=="INFRA_ERROR":
                failed=True
                last_reason=result["reason"]
                break
            if result["completion_status"]=="CANCELLED" and result.get("reason")!="TEST_RESET_CANCEL":
                last_reason=result["reason"]
                break
    except (Exception,KeyboardInterrupt) as exc:
        failed=True
        last_reason=getattr(exc,"code","WORKER_ERROR")
        message=str(exc)
        write_json(result_path,{"completion_status":"INFRA_ERROR","reason":last_reason,"message":str(exc),"episode_count":results})
    finally:
        try:
            if shared:
                shared.close()
        except Exception as exc:
            failed=True
            last_reason=getattr(exc,"code","WORKER_CLOSE_ERROR")
        finally:
            resource.close()
            if grouped:
                write_json(result_path,{"episode_count":results,"completion_status":"INFRA_ERROR" if failed else
                                         "CANCELLED" if cancel.exists() or last_reason else "COMPLETED","reason":last_reason,"message":message})
            beat.update("EXITED",None)
            beat.close()
    return 4 if failed else 130 if cancel.exists() or last_reason=="USER_CANCELLED" else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job",type=Path)
    return execute_job(parser.parse_args(argv).job)


if __name__=="__main__":
    sys.exit(main())
