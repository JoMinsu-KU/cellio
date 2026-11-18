import json
from pathlib import Path

import pytest

from cellio.adapters.mock import MockPlant
from cellio.analysis.replay import read_attempt
from cellio.artifacts import AttemptWriter, frames, verify_artifact
from cellio.core.models import CellIOError
from cellio.io import write_json
from cellio.runtime.campaign import initial_state, scenario
from cellio.runtime.episode import run_episode
from cellio.runtime.worker import execute_job


def job(destination, horizon=24, attempt="test", mode="fresh_process"):
    cfg=scenario("cell_a",profile="mock_lockstep")
    cfg["episode"].update(horizon_steps=horizon,reset_mode=mode)
    return dict(directory=str(destination),config=cfg,run_id="unit",attempt_id=attempt,
                initial_state=initial_state("cell_a",0),environment={"backend":"mock"},
                assets={"kind":"mock"},protocol={"horizon":horizon},requirements_lock="test fixture\n")


def test_T_RUN_01_06_full_h_lifecycle_replay(tmp_path):
    dest=tmp_path/"run"
    r=run_episode(**job(dest))
    assert [e["state"] for e in r["lifecycle"]]==[
        "PLANNED","PREFLIGHT","BOOTING","LOADING","WARMUP","READY","RUNNING","FINALIZING","CLOSED"]
    assert (r["completion_status"],r["end_step"],r["last_successful_advance"])==("COMPLETED",24,24)
    v=verify_artifact(dest)
    assert v["ticks"]==24 and v["artifact_digest"]==r["artifact_digest"]
    seq=list(frames(dest))
    assert [f["step"] for f in seq]==list(range(25))
    assert [f["step"] for f in seq if f["controller_called"]]==list(range(0,24,4))
    assert seq[-1]["final_snapshot"] and not seq[-1]["intents"]
    assert seq[0]["previous_advance"] is None and seq[-1]["previous_advance"]["to_step"]==24
    assert all(f["controller_view_hash"] for f in seq if f["controller_called"])
    assert read_attempt(dest)["online_offline_equal"]
    assert r["case_verdict"]=="NOT_SCORED"


def test_T_RUN_02_boot_failure_no_fictitious_snapshot(tmp_path,monkeypatch):
    def cannot_boot(*args):
        raise CellIOError("ENV_BOOT_FAILED","fixture unavailable")
    import cellio.runtime.episode as episode
    monkeypatch.setattr(episode,"make_plant",cannot_boot)
    dest=tmp_path/"failed"
    r=run_episode(**job(dest))
    assert (r["reason"],r["completion_status"],r["task_outcome"])==("ENV_BOOT_FAILED","INFRA_ERROR","UNKNOWN")
    assert r["no_snapshot"] and r["end_step"] is None and not list(frames(dest))
    assert verify_artifact(dest)["completion_status"]=="INFRA_ERROR"


def test_T_RUN_03_stalled_advance_retains_last_decision(tmp_path):
    class Stall(MockPlant):
        def advance_one_step(self,render=False):
            if self.step==3:
                raise CellIOError("SIM_STALL","no physics progress")
            return super().advance_one_step(render)
    dest=tmp_path/"stalled"
    r=run_episode(**job(dest),hardware_session=Stall())
    seq=list(frames(dest))
    assert (r["reason"],r["end_step"],r["last_successful_advance"])==("SIM_STALL",3,3)
    assert [f["step"] for f in seq]==[0,1,2,3,3]
    assert seq[-1]["reused_snapshot"] and verify_artifact(dest)["ticks"]==4
    assert read_attempt(dest)["online_offline_equal"]


def test_T_RUN_04_user_cancel_keeps_prefix(tmp_path):
    current=[]
    def heartbeat(state,step):
        if state=="RUNNING":
            current.append(step)
    dest=tmp_path/"cancel"
    r=run_episode(**job(dest),heartbeat=heartbeat,cancel_requested=lambda:4 in current)
    assert (r["completion_status"],r["reason"],r["end_step"])==("CANCELLED","USER_CANCELLED",4)
    assert verify_artifact(dest)["ticks"]==4
    assert read_attempt(dest)["online_offline_equal"]


def test_T_RUN_05_writer_error_is_not_success(tmp_path):
    class BrokenWriter(AttemptWriter):
        def record(self,frame,**kwargs):
            if frame.tick.step==3:
                raise CellIOError("TRACE_WRITE","injected write error")
            return super().record(frame,**kwargs)
    dest=tmp_path/"writer-failed"
    r=run_episode(**job(dest),writer_factory=BrokenWriter)
    assert r["completion_status"]=="INFRA_ERROR" and r["errors"][0]["code"]=="TRACE_WRITE"
    assert verify_artifact(dest)["ticks"]==3 and r["last_successful_advance"]==3


def test_missing_last_snapshot_uses_its_own_previous_advance(tmp_path):
    class LostView(MockPlant):
        def snapshot(self,tick):
            if tick.step>=3:
                raise CellIOError("SIM_STATE_INVALID","fixture expired physics handle")
            return super().snapshot(tick)
    dest=tmp_path/"lost"
    r=run_episode(**job(dest),hardware_session=LostView())
    assert (r["end_step"],r["last_successful_advance"])==(2,3)
    assert list(frames(dest))[-1]["previous_advance"]["to_step"]==2
    assert verify_artifact(dest)["ticks"]==3


def test_mock_worker_group_isolates_generation_and_routes_outputs(tmp_path):
    a,b=job(tmp_path/"a",attempt="a",mode="hard_reload"),job(tmp_path/"b",attempt="b",mode="hard_reload")
    path=tmp_path/"job.json"
    write_json(path,{"episodes":[a,b]})
    assert execute_job(path)==0
    summary=json.loads(path.with_suffix(".result.json").read_bytes())
    assert summary["episode_count"]==2 and summary["completion_status"]=="COMPLETED"
    first,second=(list(frames(Path(x["directory"]))) for x in (a,b))
    assert first[0]["generation"]==0 and second[0]["generation"]==1
    assert first[0]["truth"]["plant_state"]==second[0]["truth"]["plant_state"]
    assert first[0]["observed_signals"]["gripper.open"]["source_seq"]==0
    assert second[0]["observed_signals"]["gripper.open"]["transport_seq"]==0
    assert not second[0]["intents"] and not second[0]["actuation"]["active_override_ids"]
    assert second[4]["intents"][0]["command_id"].startswith("b:cmd:0")
    assert first[0]["episode_id"]!=second[0]["episode_id"]
    assert read_attempt(Path(b["directory"]))["online_offline_equal"]


def test_reset_control_hits_deterministic_P0_and_B0_is_offline(tmp_path):
    j=job(tmp_path/"reset",horizon=50,mode="hard_reload")
    c=j["config"]
    c["faults"]=[{"fault_id":"f","family":"F5","model_id":"explicit_invalid_v1","target_id":"gripper.closed",
                  "arm":{"open_step":0,"close_step":50},"trigger":{"kind":"at_step","at_step":4},
                  "offset_steps":0,"duration_steps":24,"parameters":{},"require_exercised":True}]
    r=run_episode(**j,test_control={"kind":"fault_start_offset","steps":4})
    assert (r["end_step"],r["reason"],r["completion_status"])==(8,"TEST_RESET_CANCEL","CANCELLED")
    assert r["faults"][0]["actual_start"]==4
    dest=tmp_path/"baseline"
    baseline=run_episode(**job(dest),benchmark_mode="B0")
    assert baseline["requirements"]==[] and baseline["task_outcome"]=="UNKNOWN"
    assert read_attempt(dest)["online_comparable"] is False
