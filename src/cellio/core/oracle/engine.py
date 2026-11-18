from collections.abc import Mapping
from math import hypot

from cellio.core.models import CellIOError, Verdict
from .audit import usable
from .geometry import (body_in_box, body_speed, fixture_clamped, fixture_loaded, get,
                       gripper_open, measured)
from .presets import preset, profile_for
from .projector import Projector
from .temporal import Monitor, Registry


def reduce_category(results):
    verdicts = {get(r,"verdict") for r in results}
    return next((v for v in (Verdict.FAIL,Verdict.UNKNOWN,Verdict.PASS) if v in verdicts),Verdict.NOT_APPLICABLE)


def task_outcome(results, complete_horizon):
    results_by_number = {get(r,"requirement_id").split(".")[-2].removeprefix("r"): get(r,"verdict")
                         for r in results if len(get(r,"requirement_id").split("."))>=3}
    goal = results_by_number.get("14")
    if goal==Verdict.PASS:
        return "SUCCESS"
    if results_by_number.get("05")==Verdict.PASS and results_by_number.get("06")==Verdict.PASS:
        return "CONTROLLED_ABORT"
    if goal==Verdict.FAIL and complete_horizon and all(results_by_number.get(key)!=Verdict.UNKNOWN for key in ("05","06")):
        return "FAILED"
    return "UNKNOWN"


def _registry():
    register = Registry()
    for key in ("state_valid","input_fresh","command_order","all_targets_held","physical_quiescent",
                "pick_ready","actual_grasp_contact","carry_active","carry_valid","belt_dwell","clamp_ready",
                "process_valid","process_active","clamp_open_dwell","declared_goal","goal_sample",
                "path_restored","no_stale_tail","reset_clean","registered_actions"):
        register.register(key,lambda b,e,a,k=key:e.get(k,b.facts.get(k)))
    register.register("actual_response",lambda b,e,a:b.facts["responses"].get(e.get("command_id")))
    def body(b,a):
        return measured(get(b.raw,"truth"),"bodies",a["body"])
    def joint(b,a):
        return measured(get(b.raw,"truth"),"joints",a["joint"])
    def center(b,e,a):
        actual = get(body(b,a),"position_m")
        if actual is None:
            return None
        return all(c-t<=x<=c+t for c,t,x in zip(a["target"],a["tolerance"],actual,strict=True))
    register.register("body_center_near",center,("body","target","tolerance"))
    register.register("body_in_box",lambda b,e,a:body_in_box(body(b,a),a["center"],a["dimensions"],a["orientation"]),
                      ("body","center","dimensions","orientation"))
    register.register("body_speed_below",lambda b,e,a:body_speed(body(b,a),a["linear"],a["angular"]),
                      ("body","linear","angular"))
    register.register("joint_in_range",lambda b,e,a:None if joint(b,a) is None else a["lo"]<=get(joint(b,a),"position")<=a["hi"],
                      ("joint","lo","hi"))
    register.register("joint_speed_below",lambda b,e,a:None if joint(b,a) is None else abs(get(joint(b,a),"velocity"))<=a["limit"],
                      ("joint","limit"))
    register.register("actual_gripper_open",lambda b,e,a:gripper_open(get(b.raw,"truth")))
    register.register("fixture_loaded",lambda b,e,a:fixture_loaded(get(b.raw,"truth")))
    register.register("fixture_clamped",lambda b,e,a:fixture_clamped(get(b.raw,"truth")))
    register.register("signal_usable",lambda b,e,a:usable(get(b.raw,"observed_signals",{}).get(a["signal"]),a["dtype"],b.step),
                      ("signal","dtype"))
    register.register("goal_placed",lambda b,e,a:b.facts.get("declared_goal"))
    register.register("process_history_valid",lambda b,e,a:b.facts.get("process_history_valid"))
    register.register("expected_operation",lambda b,e,a:e.get("operation_epoch")==a["operation_epoch"] and e.get("job_id")==a["job_id"],
                      ("job_id","operation_epoch"))
    register.register("controller_terminal",lambda b,e,a:get(get(b.raw,"controller_output"),"mission_status")==a["status"],("status",))
    register.register("delivered",lambda b,e,a:any(get(get(r,"intent"),"command_id")==a["command_id"] and get(r,"status")=="DELIVERED"
                                                    for r in get(b.raw,"deliveries",())),("command_id",))
    register.register("applied",lambda b,e,a:None if get(b.raw,"actuation") is None else
                      any(get(r,"command_id")==a["command_id"] and get(r,"status")=="APPLIED"
                          for r in get(get(b.raw,"actuation"),"applied_commands",())),("command_id",))
    register.register("belt_stationary",lambda b,e,a:b.facts.get("belt_stationary"))
    register.register("hand_clear_of",lambda b,e,a:b.facts.get("fixture_clear") if a["zone"]=="fixture_clearance" else None,("zone",))
    return register


class Oracle:
    """Use the same projector and monitor for typed live records and serialized replay."""
    def __init__(self, cell_id="cell_a", horizon_steps=7200, *, registry=None, profile=None):
        self.profile = profile or profile_for(cell_id)
        self.cell_id, self.horizon_steps = cell_id,horizon_steps
        self.registry = registry or _registry()
        self.monitors = ()

    def reset(self, requirements, generation):
        specs = requirements if requirements is not None else preset(self.cell_id,self.horizon_steps)
        if isinstance(specs,Mapping):
            if "preset" not in specs:
                raise CellIOError("CFG_ORACLE_SCHEMA","expected requirement list or named preset")
            specs = preset(specs["preset"],self.horizon_steps)
        self.projector = Projector(self.profile,generation)
        self.monitors = tuple(Monitor(s,self.registry) for s in specs)
        if len({m.id for m in self.monitors})!=len(self.monitors):
            raise CellIOError("CFG_ORACLE_SCHEMA","duplicate rule id")

    def observe(self, frame):
        boundary = self.projector.project(frame)
        for monitor in self.monitors:
            monitor.feed(boundary)
        if sum(len(m.active) for m in self.monitors)>4096:
            raise CellIOError("ORACLE_CAPACITY","too many active obligations")
        return tuple(m.finish(boundary.step,False) for m in self.monitors)

    def finalize(self, final_tick, complete_horizon):
        step = get(final_tick,"step",final_tick)
        return tuple(m.finish(step,complete_horizon) for m in self.monitors)
