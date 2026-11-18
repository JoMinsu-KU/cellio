"""Replay the independent §27 analytic inputs through the production rule monitor."""
from math import cos, sin

from cellio.core.models import RequirementResult, Verdict
from .audit import CommandAudit, usable
from .geometry import fixture_clamped, goal_now, clear_fixture, all3
from .temporal import Boundary, Monitor, Registry


def event(name, step, index=0, job=0, **details):
    return {"event_type":name,"event_id":f"{name}:{step}:{job}:{index}","job_id":job,
            "operation_epoch":0,**details}


def body(position, yaw=0., speed=0., valid=True):
    return {"position_m":position,"orientation_wxyz":(cos(yaw/2),0.,0.,sin(yaw/2)),
            "linear_velocity_mps":(speed,0.,0.),"angular_velocity_rps":(0.,0.,0.),"valid":valid}


def joint(position):
    return {"position":position,"velocity":0.,"valid":True}


def goal_trace(cell, data, step, center):
    at_goal = step >= data.get("placed_from",140)
    x,y = center
    moved = body((x,y+data.get("horizontal_m",0.),.810),data.get("yaw",0.),data.get("linear_mps",0.),
                 step not in data.get("invalid_part_steps",()))
    outside = body((.5,.12,.81))
    part = moved if at_goal and data.get("placed_body","Part_000") == "Part_000" else outside
    measured = {"part_000":part, "part_001":moved, "hand":body((.30,0.,1.1))}
    fixture_phase = cell == "cell_b" and step<=120
    bounds = {f"{name}.bounds.{side}_{ax}": v for name in ("hand","finger_left","finger_right")
              for side in ("min","max") for ax,v in zip("xyz",(.30,0.,1.1))}
    return {"bodies":measured, "joints":{"panda_finger_joint1":joint(data.get("finger_q",.04)),
            "panda_finger_joint2":joint(data.get("finger_q",.04)), "clamp":joint(-.06 if fixture_phase else 0.)},
            "contacts":(("fixed_jaw","part_000"),("moving_jaw","part_000")) if fixture_phase else (),
            "complete":True,"plant_state":bounds}


def evaluate_fixture(case):
    # This method consumes the stimulus only. Label comparison belongs to the caller.
    family, data, horizon = case["family"],case["input"],case["horizon"]
    end = data.get("observed_end",horizon)
    registry = Registry()
    registry.register("trace",lambda b,e,a: b.facts.get(e.get("job_id",0)))
    registry.register("admission",lambda b,e,a:e.get("admitted"))
    registry.register("enabled",lambda b,e,a:False)
    predicate = {"op":"pred","name":"trace"}
    rule = {"id":case["id"],"category":"compliance","scope":"operation","trigger":"action",
            "kind":"response","window":{"lower_steps":1,"upper_steps":5},"predicate":predicate,
            "required_coverage":True}
    source = {k:[item[1] for item in data.get("events",[]) if item[0]==k] for k in range(end+1)}
    reset_case = any(name.startswith("reset") for names in source.values() for name in names)
    if family == "O1":
        rule.update(kind="order", predicate={"op":"pred","name":"admission"})
    elif family == "O2" or family=="O6" and reset_case:
        rule.update(kind="invariant",trigger=None,scope="episode")
    elif family == "O3":
        rule.update(kind="admission")
    elif family == "O4":
        rule["dwell_steps"] = data.get("dwell",0)
    elif family == "O5":
        rule.update(trigger=None,dwell_steps=60,window={"lower_steps":0,"upper_steps":horizon})
    elif family == "O6":
        rule.update(window={"lower_steps":0,"upper_steps":3})
    wrapper = data.get("wrapper")
    if wrapper == "absent_scope_event":
        rule.update(trigger="test_scope_enter",scope="after_event")
    elif wrapper == "disabled":
        rule.update(applicability={"op":"pred","name":"enabled"})
    monitors = [Monitor(rule,registry)]
    if family=="O6" and not reset_case and not wrapper:
        registry.register("no_tail",lambda b,e,a:b.facts.get("no_tail"))
        monitors.append(Monitor({**rule,"kind":"invariant","scope":"after_event",
                                 "predicate":{"op":"pred","name":"no_tail"}},registry))
    before, uncertain_before = set(), set()
    audit = CommandAudit(1)
    process_history = []
    center = (.45,.25) if case["cell"]=="cell_a" else (.35,.30)
    endpoint = "actuator.gripper" if case["cell"]=="cell_a" else "actuator.clamp"
    for k in range(end+1):
        events, facts = [], {}
        if family=="O1":
            if k in data.get("missing_before",()):
                uncertain_before.add(0)
            ordered = [(p,i,j,name) for i,name in enumerate(("before","after"))
                       for s,p,j in data.get(name,()) if s==k]
            for index,(phase,_,job,name) in enumerate(sorted(ordered)):
                if name=="before":
                    before.add(job)
                else:
                    pre = True if job in before else None if job in uncertain_before else False
                    events.append(event("action",k,index,job,phase=phase,admitted=pre))
        elif family == "O2":
            checks = []
            for name in source[k]:
                if name=="reset":
                    audit = CommandAudit(2)
                    continue
                if name=="missing_receipt":
                    checks.append(audit.apply(None,None))
                    continue
                ident,epoch,kind,value = "c0",0,"GRIPPER_GAP",.04
                if name=="cancel":
                    ident,epoch,kind,value = "cancel",1,"CANCEL_OPERATION",0
                elif name=="apply_hold_new":
                    ident,epoch,kind,value = "hold",1,"HOLD",()
                elif name=="apply_conflict":
                    value=.08
                cmd = {"command_id":ident,"generation":1,"operation_epoch":epoch,"kind":kind,
                       "actuator_id":endpoint if name!="cancel" else "control.operation","value":value}
                receipt = {"status":"CANCELLED" if name=="cancelled_old" else "APPLIED","generation":1}
                checks.append(audit.apply(cmd,receipt))
            facts[0] = all3(checks)
        elif family == "O3" and k==20:
            events.append(event("action",k))
            samples = [None if entry is None else dict(zip(("quality","source_step","value"),entry),dtype="bool")
                       for entry in data.get("samples",[])]
            facts[0] = all3(usable(s,"bool",k) for s in samples)
        elif family == "O4":
            events.extend(event("action",k,i,j) for i,(s,j) in enumerate(data.get("triggers",())) if s==k)
            for job in (0,1):
                facts[job] = None if [k,job] in data.get("unknown",[]) else [k,job] in data.get("true",[])
        elif family == "O5":
            truth = goal_trace(case["cell"],data,k,center)
            if case["cell"] == "cell_b" and k<=120:
                process_history.append(all3((fixture_clamped(truth),clear_fixture(truth))))
            process = (all3(process_history) if len(process_history)==121 else False) if case["cell"]=="cell_b" else True
            facts[0] = all3((goal_now(truth,center),process))
        elif family=="O6":
            names = source[k]
            if reset_case:
                facts[0] = not ("reset_dirty_cache" in names or
                                "reset_clean" in names and abs(data.get("post_reset_position_delta_m",0.))>1e-4)
            else:
                if k==6:
                    events.append(event("action",k))
                facts["no_tail"] = not any(n in ("old_packet_applied","old_override_applied") for n in names)
                evidence = ("fresh_sample","router_hold_applied","normal_target_applied")
                facts[0] = None if "unknown_target_receipt" in names else any(n in evidence for n in names)
        for monitor in monitors:
            monitor.feed(Boundary(k,facts,events))
    results = [m.finish(end,end==horizon) for m in monitors]
    if len(results)==1:
        return results[0]
    priority = {Verdict.FAIL:0,Verdict.UNKNOWN:1,Verdict.PASS:2,Verdict.NOT_APPLICABLE:3}
    winner = min(results,key=lambda r:(priority[r.verdict],r.first_witness_step if r.first_witness_step is not None else end+1))
    return RequirementResult(winner.requirement_id,winner.verdict,winner.first_witness_step,end,
                             sum(r.obligation_count for r in results),sum(r.violated_count for r in results),
                             sum(r.unresolved_count for r in results),
                             "RESTORED_NO_TAIL" if winner.verdict==Verdict.PASS else winner.reason)
