"""Resumable Linux replay of only the formal commission-v1 first attempts."""
import fcntl
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from cellio.analysis.replay import read_attempt
from cellio.environment import source_hash
from cellio.io import digest, file_hash, write_json
from cellio.runtime.campaign import read_plan

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
SUITE = ROOT / "commission-v1"
PLAN = "cd1b7c29336858d9c653732ef8166e3cc65fe2237fb45473e976472a69de9a01"
JOURNAL = ROOT / "protocol-runs" / "5338023b-623c-4e01-92d9-b6bada9bcd12" / "journal.jsonl"

def read(p):
    return json.loads(p.read_text(encoding="utf-8"))

def records(p):
    raw = p.read_bytes()
    return [json.loads(s) for s in raw.split(b"\n")[:-1] if s]

def audit(row, first, plan, wsl_source):
    run = row["run_id"]
    attempt = first["attempt_id"]
    path = SUITE / run / attempt
    archived = read(path/"result.json")
    online = read(path/"requirements.json")
    env = read(path/"environment.lock.json")
    result = read_attempt(path)
    wsl = {r["requirement_id"]: [r["verdict"],r["first_witness_step"]] for r in result["requirements"]}
    win = {r["requirement_id"]: [r["verdict"],r["first_witness_step"]] for r in online}
    provenance = plan["provenance"]
    inputs = {key:digest(read(path/f"{stem}.lock.json")) for stem,key in (("assets","assets_hash"),("protocol","protocol_hash"),("environment","environment_hash"))}
    inputs["requirements_hash"] = file_hash(path/"requirements.lock")
    inputs["code_hash"] = env["cellio_source_sha256"]
    goals = [k for k in wsl if ".r14." in k]
    expected_steps = row["config"]["episode"]["horizon_steps"]
    checks = {
        "first_attempt": first["attempt_index"]==0 and first.get("parent_attempt_id") is None,
        "registered_identity": result["run_id"]==archived["run_id"]==run and result["attempt_id"]==archived["attempt_id"]==attempt,
        "registered_inputs": inputs==provenance and wsl_source==provenance["code_hash"],
        "registered_config_state": digest(read(path/"config.resolved.json"))==row["config_hash"] and read(path/"initial_state.json")==row["initial_state"],
        "raw_chain": result["source_digest"]==read(path/"manifest.final.json")["last_record_hash"]==archived["artifact_digest"],
        "complete_horizon": archived["completion_status"]==result["completion_status"]=="COMPLETED" and archived["end_step"]==result["end_step"]==expected_steps,
        "outcomes_equal": archived["task_outcome"]==result["task_outcome"] and archived["compliance_verdict"]==result["compliance_verdict"],
        "online_comparable":result["online_comparable"] is True and result["same_rule_revision"] is True,
        "all_requirement_witnesses_equal":result["online_offline_equal"] is True and wsl==win,
        "physical_goal_defined":len(goals)==1
    }
    return {"schema":"cellio.wsl-first-attempt-replay/1.0","plan_sha256":PLAN,"inputs":inputs,
            "run_id":run,"attempt_id":attempt,"attempt_index":first["attempt_index"],"cell":row["cell"],"repeat_index":row["repeat_index"],
            "end_step":result["end_step"],"task_outcome":result["task_outcome"],"compliance_verdict":result["compliance_verdict"],
            "goal":{g:wsl[g] for g in goals},"rules_sha256":result["rules_digest"],"chain_sha256":result["source_digest"],
            "manifest_sha256":file_hash(path/"manifest.final.json"),"windows_witness_sha256":digest(win),"linux_witness_sha256":digest(wsl),
            "windows_witnesses":win,"linux_witnesses":wsl,"differences":result["differences"],"checks":checks,
            "healthy_gate":result["task_outcome"]=="SUCCESS" and result["compliance_verdict"]=="PASS" and all(wsl[g][0]=="PASS" for g in goals),
            "verified":all(checks.values()),"timestamp_utc":datetime.now(timezone.utc).isoformat()}

def main():
    if platform.system()!="Linux" or "WSL" not in platform.release().upper() or Path(sys.prefix).name!="cellio-audit":
        raise RuntimeError("Requires WSL2 cellio-audit")
    index=OUT/"rows"
    index.mkdir(exist_ok=True)
    source = source_hash()
    plan=read_plan(SUITE/"campaign.plan.json")
    if plan["plan_hash"]!=PLAN or len(plan["runs"])!=40 or source!=plan["provenance"]["code_hash"]:
        raise ValueError("Formal source or plan fingerprint differs")
    audit_environment={"system":platform.system(),"kernel":platform.release(),"python":platform.python_version(),"environment":Path(sys.prefix).name,"source_sha256":source}
    while True:
        attempts={}
        for event in records(SUITE/"ledger.jsonl"):
            if event.get("attempt_index")==0:
                run=event["run_id"]
                if run in attempts and attempts[run]["attempt_id"]!=event["attempt_id"]:
                    raise ValueError(f"Ambiguous first attempt: {run}")
                attempts[run]=event
        verified=[]
        stop_reason=None
        for row in plan["runs"]:
            run=row["run_id"]
            entry=attempts.get(run)
            if not entry:
                continue
            output=index/(run+".json")
            path=SUITE/run/entry["attempt_id"]
            if output.is_file():
                evidence=read(output)
                if (evidence["plan_sha256"]!=PLAN or evidence["attempt_id"]!=entry["attempt_id"] or evidence["inputs"]!=plan["provenance"]
                        or evidence["manifest_sha256"]!=file_hash(path/"manifest.final.json")):
                    raise ValueError(f"Stored evidence lineage changed: {run}")
            elif entry.get("state")=="COMPLETED" and (path/"COMPLETE").is_file():
                evidence=audit(row,entry,plan,source)
                write_json(output,evidence)
                print(row["cell"],row["repeat_index"],run, evidence["verified"],evidence["task_outcome"],evidence["compliance_verdict"], 
                      [k for k,v in evidence["checks"].items() if not v],flush=True)
            else:
                continue
            verified.append(evidence)
            if not evidence["verified"]:
                stop_reason=f"Replay disagreement or integrity failure: {run}"
                break
        journal=records(JOURNAL)
        official=next((event for event in reversed(journal) if event["event"]=="stopped"),None)
        failed=[e for e in verified if not e["healthy_gate"]]
        status="DISAGREEMENT" if stop_reason else ("OFFICIAL_STOP" if official else ("COMPLETE" if len(verified)==40 else "RUNNING"))
        summary={"schema":"cellio.wsl-commission-audit/1.0","plan_sha256":PLAN,"provenance":plan["provenance"],"auditor":audit_environment,
                 "script_sha256":file_hash(Path(__file__)),"planned":len(plan["runs"]),"audited":len(verified),"source_and_witness_agree":sum(e["verified"] for e in verified),
                 "healthy_pass":sum(e["healthy_gate"] for e in verified),"unhealthy_first_attempts":[e["run_id"] for e in failed],"by_cell":{
                     cell:sum(e["cell"]==cell and e["verified"] for e in verified) for cell in ["cell_a","cell_b"]},
                 "rows":[{"run_id":e["run_id"],"attempt_id":e["attempt_id"],"audit_sha256":file_hash(index/(e["run_id"]+".json"))} for e in verified],
                 "status":status,"reason":stop_reason or (official or {}).get("reason"),"last_update_utc":datetime.now(timezone.utc).isoformat()}
        write_json(OUT/"summary.json",summary)
        if status!="RUNNING":
            print(status,summary["audited"],summary["reason"],flush=True)
            return 0 if status=="COMPLETE" and not failed else 5
        sleep(50)

if __name__=="__main__":
    with (OUT/"audit.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        raise SystemExit(main())

