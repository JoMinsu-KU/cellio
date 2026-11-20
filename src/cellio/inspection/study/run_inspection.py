"""Commission two consecutive parts in the local Cell C design study."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from cellio.adapters.isaac62.application import standalone_main, start_application
from cellio.inspection.study.prepare import prepare_stage

HERE = Path(__file__).resolve().parent
PARTS = ("P1", "P2")
GRIP_OFFSET = (-.013, .026, .049)


def write_json(path, data):
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Recorder:
    """Record measured world poses, not desired pick or bin coordinates."""
    def __init__(self, folder, stage, handling):
        from pxr import Sdf, Usd, UsdGeom

        self.folder = folder
        self.stage = stage
        self.handling = handling
        self.events = []
        self.file = (folder / "motion.jsonl").open("w", encoding="utf-8", buffering=1)
        self.layer = Usd.Stage.CreateNew(str(folder / "replay.usda"))
        self.layer.GetRootLayer().subLayerPaths = ["./live.usda"]
        self.layer.SetTimeCodesPerSecond(30)
        self.layer.SetFramesPerSecond(30)
        self.layer.SetStartTimeCode(0)
        UsdGeom.SetStageMetersPerUnit(self.layer, 1.0)
        UsdGeom.SetStageUpAxis(self.layer, UsdGeom.Tokens.z)
        self.layer.SetDefaultPrim(self.layer.GetPrimAtPath("/World"))
        self.attrs = {}
        for part, path in handling.paths.items():
            prim = self.layer.OverridePrim(path)
            prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(
                ["xformOp:translate:measured", "xformOp:orient:measured"]
            )
            self.attrs[part] = (
                prim.CreateAttribute("xformOp:translate:measured", Sdf.ValueTypeNames.Double3),
                prim.CreateAttribute("xformOp:orient:measured", Sdf.ValueTypeNames.Quatd),
                UsdGeom.Imageable(prim).CreateVisibilityAttr(),
            )
            prim.CreateAttribute("physics:rigidBodyEnabled", Sdf.ValueTypeNames.Bool).Set(False)
        self.previous_quat = {}
        self.last_frame = -1

    def record(self, frame, phase, tcp):
        from pxr import Gf, UsdGeom

        parts = {}
        for name, path in self.handling.paths.items():
            pos, ori = self.handling.pose(name)
            pos, ori = tuple(map(float, pos)), tuple(map(float, ori))
            if name in self.previous_quat and sum(a*b for a,b in zip(ori, self.previous_quat[name])) < 0:
                ori = tuple(-x for x in ori)
            self.previous_quat[name] = ori
            visible = UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).GetVisibilityAttr().Get()
            trans, rot, vis = self.attrs[name]
            trans.Set(Gf.Vec3d(*pos), frame)
            rot.Set(Gf.Quatd(ori[0], Gf.Vec3d(*ori[1:])).GetNormalized(), frame)
            vis.Set(visible, frame)
            parts[name] = {"xyz": pos, "wxyz": ori, "visibility": visible}
        self.last_frame = frame
        self.file.write(json.dumps({"frame": frame, "phase": phase, "tcp": list(map(float, tcp)), "parts": parts}) + "\n")

    def event(self, frame, event, **values):
        row = {"frame": frame, "event": event, **values}
        self.events.append(row)
        write_json(self.folder / "events.json", self.events)
        print(f"{frame:5d} {event} {values}", flush=True)

    def finish(self):
        self.file.close()
        self.layer.SetEndTimeCode(self.last_frame)
        self.stage.GetRootLayer().Save()
        self.layer.GetRootLayer().Save()


def run(folder, stale, diagnostic=False, *, static_stage=None, assets=None, recipe=None, bridge=None):
    app = start_application({"headless": True, "multi_gpu": False, "limit_cpu_threads": 4,
                             "disable_viewport_updates": True, "width": 640, "height": 400})
    # Let Kit initialize its USD runtime before composing the optional local scene.
    scene = Path(static_stage).resolve() if static_stage is not None else prepare_stage(folder / "stage", assets)
    import omni.usd
    from pxr import Usd, UsdGeom
    from cellio.inspection.study.cell_c_handling import CellHandling, STATIONS, make_physics_scene, start_physics
    from cellio.inspection.study.cell_c_inspection_logic import Decision, InspectionController, measure_part, part_truth, set_boss_error, set_tooling
    from cellio.inspection.study.cell_c_motion import CellCMotion

    sources = [scene,scene.parent/"inspection_equipment.usdc",HERE/"cell_c_motion.py",HERE/"cell_c_handling.py",
               HERE/"cell_c_inspection_logic.py",Path(__file__)]
    source_hashes = {p.name:digest(p) for p in sources}
    layer = Usd.Stage.CreateNew(str(folder / "live.usda"))
    layer.GetRootLayer().subLayerPaths = [os.path.relpath(scene, folder).replace(os.sep, "/")]
    layer.SetTimeCodesPerSecond(30)
    layer.SetFramesPerSecond(30)
    layer.SetStartTimeCode(0)
    layer.SetEndTimeCode(12000)
    UsdGeom.SetStageMetersPerUnit(layer, 1.0)
    UsdGeom.SetStageUpAxis(layer, UsdGeom.Tokens.z)
    layer.GetRootLayer().Save()
    del layer
    context = omni.usd.get_context()
    if not context.open_stage(str(folder / "live.usda")):
        raise RuntimeError("Cannot open the composed workcell")
    for _ in range(10):
        app.update()
    stage = context.get_stage()
    handling = CellHandling(stage)
    handling.prepare()
    make_physics_scene(stage)
    errors_mm = recipe["errors_mm"] if recipe is not None else (0., .60)
    offsets_mm = recipe["infeed_offsets_mm"] if recipe is not None else ((0.,0.),(0.,0.))
    for part, error_mm in zip(PARTS, errors_mm, strict=True):
        if error_mm:
            set_boss_error(stage, handling.paths[part], error_mm)
    set_tooling(stage, .11, True)
    set_tooling(stage, .11, True, time=0)
    motion = CellCMotion(stage, check_clearance=not diagnostic)
    control = InspectionController(stale_ok=stale) if bridge is None else None
    stage.GetRootLayer().Save()
    runtime = start_physics()
    handling.bind()
    if bridge is not None:
        bridge.start(runtime)
    rec = Recorder(folder, stage, handling)
    rows = []
    phase, attached = "initial", None
    tooling_state = (.11, 1., None)

    def sample(frame, tcp):
        if attached is not None:
            handling.follow(attached, tcp)
        if bridge is None:
            runtime.step(steps=4, update_fabric=True)
        else:
            bridge.advance_frame()
        rec.record(frame, phase, tcp)

    def hold(frames=1):
        for _ in range(frames):
            motion.hold(phase=phase,on_sample=sample)

    def event(name, **fields):
        rec.event(motion.frame, name, **fields)
        if bridge is not None and name != "inspection":
            bridge.event(motion.frame, name, fields)

    def move(xyz, name):
        nonlocal phase
        phase = name
        motion.move_tcp(xyz, phase=name, on_sample=sample)

    def gap(metres, name):
        nonlocal phase
        phase = name
        motion.set_gap(metres, phase=name, on_sample=sample)

    def tool(lift_a, lift_b, clamp_open, reading=None):
        nonlocal phase, tooling_state
        phase = "tool_open" if lift_b > lift_a else "tool_close"
        last_lift, last_open, last_reading = tooling_state
        set_tooling(stage, last_lift, last_open, time=motion.frame, reading=last_reading)
        for i in range(13):
            lift = lift_a + (lift_b - lift_a)*i/12
            opening = i/12 if clamp_open else 1-i/12
            set_tooling(stage, lift, opening, time=motion.frame+1, reading=reading if i==12 else None)
            set_tooling(stage, lift, opening, reading=reading if i==12 else None)
            hold()
        tooling_state = (lift_b, float(clamp_open), reading)

    def wait_seated(part, region):
        nonlocal phase
        phase = "settle_" + region
        for _ in range(45):
            hold()
            witness = handling.classify(part)
            if witness["region"] == region and witness["seated"]:
                # Reject a transient, single-sample contact as a seated result.
                hold(6)
                witness = handling.classify(part)
                if witness["region"] == region and witness["seated"]:
                    return witness
        raise RuntimeError(f"{part} did not settle in {region}: {witness}")

    def exchange(part, station, origin, pickup):
        nonlocal attached, phase
        x,y,z = (float(a+b) for a,b in zip(origin, GRIP_OFFSET))
        if station == "infeed":
            move((.015,-.20,1.19),"return_above_operator_side")
            move((x,y,1.10),"infeed_approach")
        elif station == "fixture":
            if not pickup:
                move((-.12,-.18,1.08),"transfer_to_fixture")
            move((.064,-.145,1.045),"fixture_front")
            move((x,-.13,.944),"fixture_low_approach")
            move((x,y,.944),"fixture_insert_above_pins")
        else:
            move((.280,-.195,1.075),"transfer_to_trays")
            if station == "NG":
                move((.39,-.12,1.032),"NG_front")
            move((x,y,1.012 if station == "NG" else 1.005),station+"_approach")
        drop = .00015 if station == "fixture" else .003
        move((x,y,z + (0 if pickup else drop)), station+"_engage")
        if pickup:
            gap(.012, station+"_grip")
            grasp = handling.acquire(part, motion.read_tcp())
            attached = part
            event("grasp", part=part, station=station, tcp=list(map(float,motion.read_tcp())), witness=grasp)
        else:
            released, orientation = handling.release(part)
            attached = None
            event("release",part=part,station=station,tcp=list(map(float,motion.read_tcp())),
                  position_m=released,orientation_wxyz=orientation)
            gap(.074, station+"_release")
        if station == "fixture":
            move((x,y,.944),"fixture_lift_over_pins")
            move((x,-.145,.944),"fixture_low_withdraw")
            move((.064,-.145,1.045),"fixture_clear")
        elif station == "infeed":
            move((x,y,1.07),"infeed_withdraw")
        else:
            move((x,y,1.012 if station == "NG" else 1.005),station+"_withdraw")

    try:
        rec.record(0, phase, motion.read_tcp())
        for n, part in enumerate(PARTS):
            handling.load(part, offsets_mm[n])
            phase = "index_part"
            event("infeed_index", part=part, commanded_xy_offset_mm=offsets_mm[n])
            witness = wait_seated(part, "infeed")
            gap(.074, "open_empty_gripper")
            exchange(part, "infeed", witness["position_m"], True)
            exchange(part, "fixture", STATIONS["fixture"], False)
            seat = wait_seated(part, "fixture")
            handling.sync_visual(part)
            tool(.11, 0., False)
            reading = measure_part(stage, handling.paths[part])
            truth = part_truth(stage, handling.paths[part])
            set_tooling(stage, 0., False, time=motion.frame, reading=reading)
            set_tooling(stage, 0., False, reading=reading)
            tooling_state = (0.,0.,reading)
            if bridge is None:
                decision = control.inspect(part, reading)
            else:
                owner, route = bridge.inspect(motion.frame, part, reading.height_mm, reading.within_tolerance, truth["height_mm"])
                decision = Decision(part, owner, route, part == owner)
            event("inspection", part=part, probe=asdict(reading), reference=truth, decision=asdict(decision))
            hold(10)
            tool(0., .11, True)
            exchange(part, "fixture", seat["position_m"], True)
            tool(.11, 0., False)
            exchange(part, decision.route, STATIONS[decision.route], False)
            actual = handling.classify(part)
            # The observed output is classified against both bin regions.
            phase = "output_settle"
            for _ in range(45):
                hold()
                actual = handling.classify(part)
                if actual["region"] in ("OK","NG") and actual["seated"]:
                    hold(6)
                    actual = handling.classify(part)
                    if actual["region"] in ("OK","NG") and actual["seated"]:
                        break
            if actual["region"] not in ("OK","NG") or not actual["seated"]:
                raise RuntimeError(f"Unseated output {part}: {actual}")
            row = {"part":part, "infeed_seat":witness, "probe":asdict(reading), "geometry_reference":truth,
                   "decision":asdict(decision), "fixture_seat":seat, "physical_output":actual,
                   "correct_destination": actual["region"] == ("OK" if truth["within_tolerance"] else "NG")}
            rows.append(row)
            if bridge is not None:
                bridge.set_output(actual)
            event("output_settled", part=part, observed=actual["region"], correct=row["correct_destination"])
            if n == 0:
                hold(12)
                takeaway = handling.take_away(part)
                event("external_takeaway", part=part, witness=takeaway)
                hold(3)
                if decision.route == "NG":
                    move((.39,-.12,1.032),"NG_front_return")
                move((.280,-.195,1.075),"return_from_tray")
                move((.064,-.145,1.045),"clear_for_gauge_parking")
                tool(0.,.11,True)
        diagnostics = motion.diagnostics()
        rec.finish()
        result = {"status":"completed", "diagnostic":diagnostic, "condition":"stale_ok" if stale else "normal", "parts":rows, "recipe":recipe,
                  "motion":diagnostics, "fps":30, "frames":motion.frame,
                  "physics":{"engine":"Isaac Sim 6.2 PhysX CPU TGS","step_hz":120,"steps_per_pose":4,
                             "gravity_m_s2":stage.GetPrimAtPath("/World/PhysicsScene").GetAttribute("physics:gravityMagnitude").Get()},
                  "source_sha256":source_hashes,
                  "motion_trace_sha256":digest(folder/"motion.jsonl")}
        write_json(folder/"result.json",result)
        print("COMPLETED",folder,flush=True)
        if bridge is not None:
            bridge.finish()
        return 0
    except Exception as exc:
        import traceback
        write_json(folder/"failure.json", {"phase":phase,"frame":motion.frame,"parts":rows,"error":str(exc),
                                          "traceback":traceback.format_exc()})
        rec.finish()
        raise
    finally:
        if bridge is not None and not rec.file.closed:
            # A configured horizon or cancellation can stop the parent between poses.
            # Persist only observations already made; full native replay requires completion.
            rec.finish()
            write_json(folder/"result.json", {
                "status":"incomplete", "condition":"stale_ok" if stale else "normal",
                "parts":rows, "recipe":recipe, "last_recorded_frame":rec.last_frame,
                "last_physics_step":bridge.step, "phase":phase, "fps":30,
                "motion_trace_sha256":digest(folder/"motion.jsonl"), "source_sha256":source_hashes,
            })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condition", choices=["normal","stale_ok"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--assets", type=Path, help="Directory containing assets.lock.json; defaults to the CellIO data root")
    parser.add_argument("--recipe", type=Path, help="JSON two-part inspection recipe")
    parser.add_argument("--diagnostic", action="store_true", help="Exercise native handling without swept clearance checks")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    recipe = json.loads(args.recipe.read_text(encoding="utf-8")) if args.recipe else None
    raise SystemExit(standalone_main(lambda: run(args.out.resolve(), args.condition == "stale_ok",
                                               args.diagnostic, assets=args.assets, recipe=recipe)))
