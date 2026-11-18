from cellio.core.models import (ActuationReceipt, AppliedCommand, BodyState, CommandIntent, CommandKind,
                               JointState, SignalSample, Tick, TickEvidenceFrame, TruthSnapshot, json_value)
from cellio.core.oracle import Oracle, preset, reduce_category, task_outcome
from cellio.core.oracle.geometry import FINGERS


def snapshot(k, *, placed=True):
    tick = Tick("trial",0,k)
    def body(name, point):
        return BodyState(name,"world",point,(0.,0.,1.,0.),(0.,0.,0.),(0.,0.,0.),True)
    bodies = {"part_000":body("part_000",(.45,.25 if placed else -.24,.81)),
              "hand":body("hand",(.30,0.,1.1)),"cell_tcp":body("cell_tcp",(.30,0.,1.1))}
    joints = {name:JointState(name,"prismatic",.04,0.,None,True) for name in FINGERS}
    for i in range(1,8):
        name = f"panda_joint{i}"
        joints[name] = JointState(name,"revolute",0.,0.,None,True)
    return TruthSnapshot(tick,bodies,joints,(),{"belt.surface_speed_mps":0.},True)


def evidence(truth, *, commands=(), applied=(), signals=None, final=False, events=()):
    tick = truth.tick
    return TickEvidenceFrame(tick,truth,signals or {},signals or {},False,None,None,commands,(),
                             ActuationReceipt(tick,True,applied,{},()) if not final else None,
                             events,(),{},None,final)


def test_online_offline_equivalence_and_category_independence():
    horizon=64
    live,replay = (Oracle("cell_a",horizon) for _ in range(2))
    rules = preset("cell_a",horizon)
    for o in (live,replay):
        o.reset(rules,0)
    for k in range(horizon+1):
        commands,applied = (),()
        if k==4:
            # P6 approach before the required 24-step actual belt dwell.
            cmd=CommandIntent("run:cmd:0",0,"trial",0,0,4,"actuator.robot",CommandKind.POSE,
                              (.45,-.116,1.030,0.,0.,1.,0.),0)
            commands=(cmd,)
            applied=(AppliedCommand(cmd.command_id,cmd.actuator_id,0,k,k,k,"APPLIED",None,cmd.value),)
        frame=evidence(snapshot(k),commands=commands,applied=applied,final=k==horizon)
        assert live.observe(frame)==replay.observe(json_value(frame))
    results=live.finalize(frame.tick,True)
    assert results==replay.finalize({"step":horizon},True)
    keyed={r.requirement_id:r for r in results}
    assert (keyed["cell_a.r10.belt_before_approach"].verdict,keyed["cell_a.r10.belt_before_approach"].first_witness_step)==("FAIL",4)
    assert (keyed["cell_a.r14.goal"].verdict,keyed["cell_a.r14.goal"].first_witness_step)==("PASS",60)
    assert task_outcome(results,True)=="SUCCESS"
    compliance=[keyed[s["id"]] for s in rules if s["category"]=="compliance"]
    assert reduce_category(compliance)=="FAIL"


def test_abort_declaration_requires_backend_and_actual_stop():
    oracle = Oracle("cell_a",80)
    oracle.reset(preset("cell_a",80),0)
    for k in range(81):
        event=({"event_type":"controller.abort","payload":{"job_id":0,"cancelled_epoch":0}},) if k==4 else ()
        frame=evidence(snapshot(k,placed=False),events=event,final=k==80)
        oracle.observe(frame)
    result=oracle.finalize(frame.tick,True)
    indexed={r.requirement_id:r for r in result}
    assert (indexed["cell_a.r05.abort_applied"].verdict,indexed["cell_a.r05.abort_applied"].first_witness_step)==("FAIL",12)
    assert indexed["cell_a.r06.abort_stopped"].verdict=="PASS"
    assert task_outcome(result,True)=="FAILED"
