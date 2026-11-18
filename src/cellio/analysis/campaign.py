"""Classify planned rows, first attempts, retried attempts, and missing evidence."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import json

from cellio.artifacts import verify_artifact
from cellio.io import CellIOError, digest
from cellio.runtime.campaign import read_plan
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
    started: bool=False
    status: str="NOT_STARTED"
    attempt_id: str|None=None
    path: Path|None=None
    eval: dict=field(default_factory=dict)
    fault: dict=field(default_factory=dict)
    performance: dict=field(default_factory=dict)
    measurement: Measurements|None=None
    error: str|None=None

    @property
    def outcome(self):
        if self.status=="COMPLETED" and self.eval:
            return self.eval["task_outcome"]
        if self.status=="NOT_STARTED":
            return "NOT_STARTED"
        if self.status in ("RUNNING","CANCELLED","PARTIAL"):
            return "UNKNOWN"
        return "ERROR"

    @property
    def evaluable(self):
        return (self.status=="COMPLETED" and self.eval.get("online_offline_equal") is not False
                and self.eval.get("task_outcome") not in ("UNKNOWN",None)
                and self.eval.get("compliance_verdict") in ("PASS","FAIL"))

    def flags(self):
        planned=bool(self.row["config"].get("faults"))
        f=self.fault
        end=f.get("actual_end")
        realized=planned and f.get("first_effect_step") is not None
        restored=(realized and end is not None and end<self.row["config"]["episode"]["horizon_steps"]
                  and self.measurement is not None and self.measurement.clear is not None)
        return {"N_plan":1,"N_start":int(self.started),"N_eval":int(self.evaluable),"N_faultplan":int(planned),
                "N_trigger":int(planned and f.get("trigger_match_step") is not None),
                "N_activate":int(planned and f.get("actual_start") is not None),"N_realized":int(realized),
                "N_validfault":int(realized and self.evaluable),"N_clear":int(restored),
                "N_recovery_eligible":int(realized and end is not None and end<self.row["config"]["episode"]["horizon_steps"]),
                "N_error":int(self.status in ("INFRA_ERROR","INTEGRITY_ERROR","FAILED","CRASHED","HUNG")),
                "N_success":int(self.outcome=="SUCCESS")}


def safe_child(parent,*names):
    for name in names:
        if not isinstance(name,str) or not name or name in (".","..") or any(c in name for c in ("/","\\",":")):
            raise CellIOError("RUN_ID_INVALID",str(name))
    p=parent.joinpath(*names).resolve()
    if not p.is_relative_to(parent.resolve()):
        raise CellIOError("RUN_PATH_ESCAPE",str(p))
    return p


def load_campaign(directory):
    directory=Path(directory)
    plan=read_plan(directory/"campaign.plan.json")
    rows=list(plan["runs"])
    if plan.get("plan_mode")=="soak_generator":
        rows.extend(value.get("row",value) for value in lines(directory/"generator.rows.jsonl"))
    planned={row["run_id"] for row in rows}
    if len(planned)!=len(rows):
        raise CellIOError("RUN_PLAN_DUPLICATE","duplicate planned run")
    entries=defaultdict(lambda:defaultdict(list))
    extras=[]
    for entry in lines(directory/"ledger.jsonl"):
        if entry["run_id"] not in planned:
            raise CellIOError("RUN_LEDGER_EXTRA",entry["run_id"])
        entries[entry["run_id"]][entry["attempt_index"]].append(entry)
    trials=[]
    for row in rows:
        trial=Trial(row)
        trials.append(trial)
        indexes=entries.get(row["run_id"],{})
        for i,states in indexes.items():
            ids={s["attempt_id"] for s in states}
            if len(ids)!=1:
                raise CellIOError("RUN_LEDGER_CONFLICT",row["run_id"])
            ident=states[0]["attempt_id"]
            path=safe_child(directory,row["run_id"],ident)
            if i!=0:
                extra={"run_id":row["run_id"],"attempt_id":ident,"attempt_index":i,"states":[s["state"] for s in states]}
                try:
                    extra["integrity"]=verify_artifact(path) if (path/"COMPLETE").exists() else None
                except ValueError as exc:
                    extra["integrity_error"]=str(exc)
                extras.append(extra)
                continue
            trial.path,trial.attempt_id=path,ident
            trial.started=any(s["state"]=="RUNNING" for s in states)
            trial.status=states[-1]["state"]
            if not (path/"COMPLETE").exists():
                if trial.status in ("COMPLETED","INFRA_ERROR","CANCELLED"):
                    trial.status,trial.error="INTEGRITY_ERROR","final ledger state without COMPLETE"
                elif trial.status not in ("RUNNING","FAILED","CRASHED","HUNG"):
                    trial.status="PARTIAL"
                continue
            trial.measurement=Measurements()
            try:
                trial.eval=read_attempt(path,on_frame=trial.measurement.observe)
                if trial.eval["run_id"]!=row["run_id"] or trial.eval["attempt_id"]!=ident:
                    raise CellIOError("RUN_LEDGER_CONFLICT","artifact identity differs from plan/ledger")
                if digest(read(path/"config.resolved.json"))!=row["config_hash"]:
                    raise CellIOError("RUN_PLAN_HASH","artifact configuration differs from frozen row")
                trial.status=trial.eval["completion_status"]
                trial.measurement.finish(row["config"]["episode"]["horizon_steps"],trial.eval)
                trial.fault=trial.measurement.fault.copy()
                stored=read(path/"faults.json")
                if isinstance(stored,dict):
                    stored=stored.get("faults",[stored] if "family" in stored else [])
                if stored:
                    # Includes final-horizon UNOBSERVED/CENSORED closure.
                    trial.fault.update(stored[0])
                trial.performance=read(path/"performance.json")
                trial.performance["trace_bytes"]=(path/"trace.jsonl.gz").stat().st_size
                if trial.eval["online_offline_equal"] is False:
                    trial.error="ONLINE_OFFLINE_MISMATCH"
            except (OSError,ValueError,KeyError,TypeError) as exc:
                trial.status,trial.error,trial.eval,trial.fault="INTEGRITY_ERROR",str(exc),{},{}
    return plan,trials,extras


RATES={"start_rate":("N_start","N_plan"),"evaluable_rate":("N_eval","N_plan"),
       "trigger_rate":("N_trigger","N_faultplan"),"activation_rate":("N_activate","N_trigger"),
       "realization_given_activation":("N_realized","N_activate"),"realization_per_plan":("N_realized","N_faultplan"),
       "valid_fault_rate":("N_validfault","N_faultplan"),"infrastructure_error_rate":("N_error","N_plan"),
       "path_recovery_rate":("N_clear","N_recovery_eligible"),"task_success_rate":("N_success","N_plan")}


def group_metrics(trials,bootstrap=False):
    counts=Counter()
    blocks=defaultdict(Counter)
    for t in trials:
        flags=t.flags()
        counts.update(flags)
        blocks[t.row["initial_state_id"]].update(flags)
    ids=[t.attempt_id for t in trials if t.attempt_id and t.eval]
    missing=Counter(t.error or t.status for t in trials if not t.evaluable)
    rates={}
    for key,(n,d) in RATES.items():
        excluded=Counter()
        for t in trials:
            if not t.flags()[d]:
                excluded[t.error or (d+" eligibility not met")]+=1
        rates[key]=ratio(counts[n],counts[d],ids,dict(excluded))
        if bootstrap:
            rates[key]["bootstrap"]=block_bootstrap([(v[n],v[d]) for v in blocks.values()])
    outcome=Counter(t.outcome for t in trials)
    compliance=Counter(t.eval.get("compliance_verdict","UNAVAILABLE") for t in trials)
    return {"counts":dict(counts),"rates":rates,"non_evaluable_reasons":dict(missing),
            "task_distribution":{k:ratio(outcome[k],len(trials),ids) for k in
                                 ("SUCCESS","CONTROLLED_ABORT","FAILED","UNKNOWN","ERROR","NOT_STARTED")},
            "compliance_distribution":{k:ratio(compliance[k],len(trials),ids) for k in
                                       ("PASS","FAIL","UNKNOWN","NOT_APPLICABLE","UNAVAILABLE")}}
