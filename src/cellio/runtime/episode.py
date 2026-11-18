"""One fixed-horizon episode. The worker owns all simulator and controller callbacks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
from time import perf_counter_ns
from uuid import uuid4

from cellio.artifacts import AttemptWriter
from cellio.config import resolve
from cellio.controllers import ReferenceController
from cellio.core.clock import Phase, PhaseGuard, StepClock
from cellio.core.commands import CommandRouter
from cellio.core.events import EventBus
from cellio.core.faults import FaultEngine
from cellio.core.models import CellIOError, SignalSample, TickEvidenceFrame, freeze, json_value
from cellio.core.oracle import Oracle, preset, reduce_category, task_outcome
from cellio.core.signals import NominalSampler, WorldPredicates, canonical_bindings, for_controller, view_hash
from cellio.io import digest


def make_plant(backend,options=None):
    if backend=="mock":
        from cellio.adapters.mock import MockPlant
        return MockPlant()
    if backend=="isaac62":
        from cellio.adapters.isaac62 import IsaacPlant
        return IsaacPlant(**(options or {}))
    raise CellIOError("CFG_PROFILE",str(backend))


def _expectation(expected,result,requirements,faults):
    if expected is None:
        return "NOT_SCORED"
    outcomes=[]
    for key in ("completion_status","task_outcome"):
        if key in expected:
            outcomes.append(expected[key]==result[key])
    for key,values,actual in (("requirements",requirements,"verdict"),("fault_coverage",faults,"coverage")):
        ident="requirement_id" if key=="requirements" else "fault_id"
        achieved={record[ident]:record[actual] for record in values}
        outcomes.extend(None if i not in achieved else achieved[i]==v for i,v in expected.get(key,{}).items())
    if False in outcomes:
        return "FAIL"
    if None in outcomes:
        return "UNKNOWN"
    return "PASS" if outcomes else "NOT_SCORED"


def _changed(results,prior):
    changed=[]
    for r in results:
        key=(r.verdict,r.first_witness_step,r.obligation_count,r.violated_count,r.unresolved_count,r.reason)
        if prior.get(r.requirement_id)!=key:
            changed.append(r)
            prior[r.requirement_id]=key
    return tuple(changed)


class _Cancelled(Exception):
    pass


class _Run:
    def __init__(self,config,directory,run_id,attempt_id,initial_state,environment,assets,protocol,
                 requirements_lock,backend,hardware_session,generation,benchmark_mode,test_control,
                 heartbeat,cancel_requested,writer_factory,plant_options,resource_sampler):
        self.config=config
        self.directory=Path(directory)
        self.run_id,self.attempt_id,self.episode_id=run_id,attempt_id,str(uuid4())
        self.generation=generation
        self.initial_state,self.environment,self.assets,self.protocol=initial_state,environment,assets,protocol
        self.requirements_lock=requirements_lock
        self.backend,self.plant=backend,hardware_session
        self.owns_plant=hardware_session is None
        self.plant_options=plant_options
        self.mode=benchmark_mode
        self.control=test_control or {"kind":"full_h"}
        self.heartbeat,self.cancel_requested=heartbeat,cancel_requested
        self.writer_factory=writer_factory or AttemptWriter
        self.resources=resource_sampler
        self.writer=self.controller=self.oracle=self.clock=self.fault=None
        self.status,self.reason="INFRA_ERROR",None
        self.state,self.phase="PLANNED",None
        self.history=[]
        self.last_truth=self.previous=self.last_snapshot_advance=None
        self.recorded_step=self.observed_step=None
        self.running_start=None
        self._resource_start=None
        self.errors=[]
        self.fault_reports,self.requirement_results=[],[]
        self._verdict_signature={}
        self._perf={"running_wall_ns":None,"startup_wall_ns":None,"warmup_wall_ns":None,"finalization_wall_ns":None,
                    "hard_reload_wall_ns":None,"peak_rss_bytes":None,"peak_vram_bytes":None,"rtf":None,
                    "critical_path_ns_by_step":[],"controller_active_by_step":[]}
        self._prior_tick_perf={}
        self.started=perf_counter_ns()

    def notify(self,state):
        self.state=state
        self.history.append({"state":state,"elapsed_wall_ns":perf_counter_ns()-self.started})
        if self.heartbeat:
            self.heartbeat(state,self.clock.step if self.clock else None)

    def error(self,exc):
        code=getattr(exc,"code",None)
        if not code:
            if self.state in ("PREFLIGHT","BOOTING","LOADING","WARMUP"):
                code="ENV_BOOT_FAILED" if self.state=="BOOTING" else "SIM_INIT_FAILED"
            elif self.phase==Phase.CONTROL:
                code="CTRL_EXCEPTION"
            elif self.phase==Phase.EVALUATE:
                code="ORACLE_ERROR"
            elif self.phase==Phase.RECORD:
                code="TRACE_WRITE"
            else:
                code="SIM_EXECUTION_FAILED"
        self.errors.append({"code":code,"severity":"ERROR","module":self.state.lower(),"phase":self.phase,
                            "step":self.clock.step if self.clock else None,"message":str(exc),
                            "exception_type":type(exc).__name__,"context":{},"traceback_path":None})
        self.status,self.reason="INFRA_ERROR",code

    def prepare(self):
        self.notify("PREFLIGHT")
        self.config=resolve(self.config)
        cfg=self.config
        expected="mock_lockstep" if self.backend=="mock" else "isaac62_lockstep"
        if cfg["profile"]!=expected:
            raise CellIOError("CFG_PROFILE",f"{self.backend} requires {expected}")
        if self.mode not in (None,"B0","B1","B2"):
            raise CellIOError("CFG_PROFILE","unknown benchmark mode")
        if self.mode in ("B0","B1") and cfg["faults"]:
            raise CellIOError("CFG_PROFILE","B0/B1 require a healthy scenario")
        if self.mode=="B2" and not (len(cfg["faults"])==1 and cfg["faults"][0]["family"]=="F4"):
            raise CellIOError("CFG_PROFILE","B2 requires an F4 arm")
        if self.control not in ({"kind":"full_h"},{"kind":"fault_start_offset","steps":4},
                {"kind":"fault_start_offset","steps":8},{"kind":"fault_end_offset","steps":12}):
            raise CellIOError("CFG_TEST_CONTROL","unknown deterministic cancellation contract")
        if self.control["kind"]!="full_h" and cfg["episode"]["reset_mode"]!="hard_reload":
            raise CellIOError("CFG_TEST_CONTROL","reset cancellation requires a hard_reload plan")
        self.cell=cfg["scene"]["cell_id"]
        self.horizon=cfg["episode"]["horizon_steps"]
        ancestor=self.directory
        while not ancestor.exists():
            ancestor=ancestor.parent
        if self.backend=="isaac62" and shutil.disk_usage(ancestor).free<2*1024**3:
            raise CellIOError("ENV_DISK_SPACE","less than 2 GiB available")
        self.writer=self.writer_factory(self.directory,run_id=self.run_id,attempt_id=self.attempt_id,
                    episode_id=self.episode_id,generation=self.generation,config=cfg,initial_state=self.initial_state,
                    environment=self.environment,assets=self.assets,protocol=self.protocol,requirements_lock=self.requirements_lock)
        self.clock=StepClock(self.episode_id,self.generation,self.horizon)
        self.rules=preset(self.cell,self.horizon)
        if self.mode!="B0":
            self.oracle=Oracle(self.cell,self.horizon)
            self.oracle.reset(self.rules,self.generation)
        self.notify("BOOTING")
        if self.plant is None:
            self.plant=make_plant(self.backend,self.plant_options)
        elif hasattr(self.plant,"acquire"):
            self.plant=self.plant.acquire(self.backend,self.plant_options)
        self.notify("LOADING")
        self.plant.load({**cfg["scene"],"scene_id":self.cell,"warmup_steps":cfg["episode"]["warmup_steps"]})
        self.notify("WARMUP")
        warm=perf_counter_ns()
        self.plant.reset(self.initial_state,self.generation)
        self._perf["warmup_wall_ns"]=perf_counter_ns()-warm
        self.notify("READY")
        self.guard=PhaseGuard()
        self.bus=EventBus()
        self.bindings=canonical_bindings(self.cell)
        self.sampler=NominalSampler(self.bindings)
        self.sampler.reset(self.generation)
        self.world=WorldPredicates(self.cell)
        self.world.reset(self.generation)
        self.router=CommandRouter(self.cell,self.attempt_id,self.episode_id,self.generation)
        self.fault=FaultEngine(cfg["faults"][0] if cfg["faults"] else None,self.horizon,self.cell)
        self.fault.reset(self.episode_id,self.generation)
        self.controller=ReferenceController(self.cell,cfg["controller"]["id"])
        first=self.clock.at()
        missing={b.signal_id:SignalSample.missing(b.signal_id,b.dtype,self.generation) for b in self.bindings}
        self.controller.reset(dict(cfg["controller"]["parameters"],attempt_id=self.attempt_id),
                              for_controller(missing,first))
        self.last_truth=self.snapshot(first)
        self.writer.start(self.last_truth)
        if self.resources:
            self._resource_start=self.resources.read()
        self.notify("RUNNING")
        self.running_start=perf_counter_ns()
        self._perf["startup_wall_ns"]=warm-self.started
        if cfg["episode"]["reset_mode"]=="hard_reload":
            self._perf["hard_reload_wall_ns"]=self.running_start-warm

    def snapshot(self,tick):
        truth=self.plant.snapshot(tick)
        if truth.tick!=tick or not truth.complete:
            raise CellIOError("SIM_STATE_INVALID","incomplete snapshot or incorrect clock identity")
        bodies={"part_000","cell_tcp","hand","finger_left","finger_right"}
        joints={f"panda_joint{i}" for i in range(1,8)}|{"panda_finger_joint1","panda_finger_joint2"}
        if self.cell=="cell_b":
            joints.add("clamp")
        if any(key not in truth.bodies or not truth.bodies[key].valid for key in bodies) or any(
                   key not in truth.joints or not truth.joints[key].valid for key in joints):
            raise CellIOError("SIM_STATE_INVALID","required raw body or joint reading unavailable")
        return truth

    def _enter(self,tick,phase):
        self.phase=phase
        self.guard.enter(tick.step,phase)

    def _stop(self,k):
        if self.cancel_requested and self.cancel_requested():
            raise _Cancelled("USER_CANCELLED")
        if perf_counter_ns()-self.running_start>1_800_000_000_000:
            raise CellIOError("RUN_WALL_TIMEOUT","RUNNING exceeded 1800 wall seconds")
        kind=self.control["kind"]
        boundary=self.fault.state.actual_start if kind=="fault_start_offset" else self.fault.state.actual_end
        if kind!="full_h" and boundary is not None and k==boundary+self.control["steps"]:
            raise _Cancelled("TEST_RESET_CANCEL")

    def _actuator_readings(self,t):
        if self.fault.spec is None or self.fault.spec.family!="F6":
            return None,None
        target=self.fault.spec.target_id
        return self.plant.driver_positions(target,t),self.plant.coordinate(target,t)

    def step(self):
        tick=self.clock.at()
        k=tick.step
        start=perf_counter_ns()
        self._enter(tick,Phase.GUARD)
        self._stop(k)
        self._enter(tick,Phase.SNAPSHOT)
        read_start=perf_counter_ns()
        truth=self.last_truth if k==0 else self.snapshot(tick)
        self.last_truth=truth
        self.last_snapshot_advance=self.previous
        positions,coordinate=self._actuator_readings(truth)
        read_time=perf_counter_ns()-read_start
        self._enter(tick,Phase.SCHEDULE)
        levels=self.world.sample(truth,tick)
        trigger=self.fault.spec.trigger if self.fault.spec else {}
        fire=(trigger["predicate_id"],) if trigger.get("fire_on_initial") else ()
        edges=self.bus.world_edges(tick,levels,fire_on_initial=fire)
        ready=self.bus.pop_ready_for_fault_trigger(k)
        changes=self.fault.advance(tick,self.bus.merge_unique(edges,ready),levels,positions,coordinate)
        self._enter(tick,Phase.OBSERVE)
        raw=self.sampler.sample(truth,tick)
        observed=self.fault.transform_observations(raw,tick)
        self._enter(tick,Phase.CONTROL)
        output=view=None
        ctl_start=perf_counter_ns()
        if self.clock.control_due:
            view=for_controller(observed,tick,self.router.public_receipts(tick),self.router.epoch)
            output=self.controller.tick(view,tick)
        ctl_time=perf_counter_ns()-ctl_start
        emitted=self.bus.commit_controller(tick,output.emitted_events if output else ())
        intents=output.new_commands if output else ()
        self._enter(tick,Phase.COMMAND)
        before={a:i.command_id for a,i in self.router.effective.items()}
        deliveries=self.router.resolve(intents,self.fault,tick)
        after={a:i.command_id for a,i in self.router.effective.items()}
        self._enter(tick,Phase.ACTUATE)
        act_start=perf_counter_ns()
        receipt=self.plant.apply(deliveries,self.fault.actuator_overrides(),tick)
        details=self.plant.actuation_evidence(self.fault.spec.target_id) if positions is not None else {}
        act_time=perf_counter_ns()-act_start
        self._enter(tick,Phase.EVALUATE)
        frame=TickEvidenceFrame(tick,truth,raw,observed,view is not None,view_hash(view) if view else None,
                     json_value(output) if output else None,intents,deliveries,receipt,(*edges,*emitted),
                     changes,{},self.previous,False)
        probe=self.fault.observe(frame,effective_before=before,effective_after=after,actual_positions=positions,**details)
        audit={**probe,"pending_command_count":self.router.pending_count}
        if positions is not None:
            audit.update(driver_positions=positions,actuator_coordinate=coordinate,**details)
        frame=replace(frame,fault_audit=audit)
        updates=self.oracle.observe(frame) if self.oracle else ()
        self.observed_step=k
        self._enter(tick,Phase.RECORD)
        perf={"backend_read_ns":read_time,"actuation_ns":act_time,"controller_ns":ctl_time,
              "previous_tick":self._prior_tick_perf}
        before_enqueue=perf_counter_ns()
        self.writer.record(frame,fault_audit=audit,oracle_updates=_changed(updates,self._verdict_signature),performance=perf)
        self.recorded_step=k
        self.router.acknowledge(receipt)
        critical=perf_counter_ns()-start-read_time-act_time-ctl_time
        self._perf["critical_path_ns_by_step"].append(critical)
        active=self.controller.terminal is None
        self._perf["controller_active_by_step"].append(active)
        self._enter(tick,Phase.ADVANCE)
        physics_start=perf_counter_ns()
        advance=self.plant.advance_one_step(render=not (self.plant_options or {}).get("headless",True) and k%4==3)
        self.clock.commit(advance)
        self.previous=advance
        self._prior_tick_perf={"step":k,"critical_path_ns":critical,"controller_active":active,
                     "physics_advance_ns":perf_counter_ns()-physics_start,"record_enqueue_ns":physics_start-before_enqueue}
        if self.heartbeat:
            self.heartbeat("RUNNING",self.clock.step)

    def close_frame(self):
        current=self.clock.at()
        try:
            final=self.snapshot(current)
        except Exception as exc:
            if self.last_truth is None:
                raise
            self.error(exc)
            final=self.last_truth
        self.last_truth=final
        reused=final.tick.step==self.recorded_step
        audit=dict(self.fault.report())
        if reused:
            audit.update(reused_snapshot=True,reused_record_step=final.tick.step)
        previous=self.previous
        if previous and previous.to_step!=final.tick.step:
            previous=self.last_snapshot_advance
        frame=TickEvidenceFrame(final.tick,final,{},{},False,None,None,(),(),None,(),(),audit,previous,True)
        positions,_=self._actuator_readings(final)
        if not reused and final.tick.step!=self.fault._inspected_step:
            self.fault.observe(frame,actual_positions=positions)
        probe=self.fault.finalize(final.tick.step,complete_horizon=self.status=="COMPLETED")
        self.fault_reports=[json_value(probe)] if self.fault.spec else []
        audit={**audit,**probe}
        frame=replace(frame,fault_audit=audit)
        changes=()
        if self.oracle and self.observed_step!=final.tick.step:
            changes=self.oracle.observe(frame)
            self.observed_step=final.tick.step
        self.writer.record(frame,fault_audit=audit,oracle_updates=_changed(changes,self._verdict_signature),
                           performance={"previous_tick":self._prior_tick_perf})

    def execute(self):
        self.notify("PLANNED")
        try:
            self.prepare()
            while self.clock.step<self.horizon:
                self.step()
            self.status,self.reason="COMPLETED",None
            if self.control["kind"]!="full_h":
                self.reason="SKIPPED_PRECONDITION"
        except _Cancelled as exc:
            self.status,self.reason="CANCELLED",str(exc)
        except (Exception,KeyboardInterrupt) as exc:
            if isinstance(exc,KeyboardInterrupt):
                self.status,self.reason="CANCELLED","USER_CANCELLED"
            else:
                self.error(exc)
        if self.running_start is not None:
            span=perf_counter_ns()-self.running_start
            self._perf.update(running_wall_ns=span,rtf=(self.clock.step/120)/(span/1e9) if span else None)
        self.notify("FINALIZING")
        finish_start=perf_counter_ns()
        if self.writer is not None and self.last_truth is not None and self.fault is not None:
            try:
                self.close_frame()
            except Exception as exc:
                self.error(exc)
        if self.oracle is not None:
            try:
                last=-1 if self.observed_step is None else self.observed_step
                self.requirement_results=self.oracle.finalize(last,self.status=="COMPLETED")
            except Exception as exc:
                self.error(exc)
        if self.controller is not None:
            try:
                self.controller.close()
            except Exception as exc:
                self.error(exc)
        if self.plant is not None and self.owns_plant:
            try:
                self.plant.close()
            except Exception as exc:
                self.error(exc)
        reqs=json_value(self.requirement_results)
        compliance=reduce_category(r for r,s in zip(self.requirement_results,getattr(self,"rules",())) if s["category"]=="compliance")
        result={"run_id":self.run_id,"attempt_id":self.attempt_id,"episode_id":self.episode_id,
                "backend":self.backend,"benchmark_mode":self.mode,"completion_status":self.status,
                "task_outcome":task_outcome(self.requirement_results,self.status=="COMPLETED") if reqs else "UNKNOWN",
                "compliance_verdict":str(compliance) if reqs else "UNKNOWN","reason":self.reason,
                "end_step":self.last_truth.tick.step if self.last_truth else None,
                "no_snapshot":self.last_truth is None,"last_successful_advance":self.clock.step if self.clock else None,
                "last_valid_snapshot":self.last_truth.tick.step if self.last_truth else None,
                "termination_phase":self.phase,"errors":self.errors,"lifecycle":self.history,
                "faults":self.fault_reports,"requirements":reqs,"artifact_digest":None}
        result["case_verdict"]=_expectation(self.config.get("expected"),result,reqs,self.fault_reports)
        if self.resources:
            self._perf.update(self.resources.read())
        self._perf["finalization_wall_ns"]=perf_counter_ns()-finish_start
        if self.writer is not None:
            try:
                self.writer.finish(result,performance=self._perf,faults=self.fault_reports,
                                   requirements=reqs,errors=self.errors)
                result["artifact_digest"]=getattr(self.writer,"previous",None)
            except Exception as exc:
                self.error(exc)
                result.update(completion_status=self.status,reason=self.reason,case_verdict="UNKNOWN")
        self.notify("CLOSED")
        result.update(directory=str(self.directory),performance=self._perf)
        return result


def run_episode(config,directory,*,run_id,attempt_id,initial_state,environment,assets,protocol,
                requirements_lock,backend=None,hardware_session=None,generation=0,benchmark_mode=None,test_control=None,
                heartbeat=None,cancel_requested=None,writer_factory=None,plant_options=None,resource_sampler=None):
    """Run a single episode; a provided plant remains owned by its hard-reload supervisor."""
    backend=backend or ("mock" if config.get("profile")=="mock_lockstep" else "isaac62")
    return _Run(config,directory,run_id,attempt_id,initial_state,environment,assets,protocol,requirements_lock,
                backend,hardware_session,generation,benchmark_mode,test_control,heartbeat,cancel_requested,
                writer_factory,plant_options,resource_sampler).execute()
