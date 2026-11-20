"""Verify both completed Cell C runs offline against the motion trace and composed USD."""

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom

from cellio.inspection.study.cell_c_inspection_logic import measure_part, part_truth
from cellio.io import write_json


HERE = Path(__file__).resolve().parent
FILES = ("result.json", "events.json", "motion.jsonl", "live.usda", "replay.usda")
SOURCES = ("cell_c_inspection.usda", "inspection_equipment.usdc", "cell_c_motion.py",
           "cell_c_handling.py", "cell_c_inspection_logic.py", "run_inspection.py")
PARTS = ("P1", "P2")
FRAME_HZ = 30
TCP_OFFSET = .1034


def require(value, message):
    if not value:
        raise ValueError(message)


def near(a, b, limit, message):
    require(math.isfinite(a) and abs(a - b) <= limit, f"{message}: {a} vs {b} ± {limit}")


def digest(path):
    with path.open("rb") as stream:
        result = sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


def physical_region(xyz, quaternion):
    """Classify all four rotated sole corners, including the actual tray height."""
    q = np.asarray(quaternion, dtype=float)
    near(float(np.linalg.norm(q)), 1, 2e-5, "orientation norm")
    corner = np.array([[x, y, 0.] for x in (-.062, .062) for y in (-.041, .041)])
    v = q[1:]
    rotated = corner + 2 * np.cross(v, np.cross(v, corner) + q[0] * corner)
    corners = rotated + np.asarray(xyz)
    specs = {"infeed": ((-.440,-.248,.899), (.190,.050)),
             "fixture": ((.077, .001, .882), (.066, .045)),
             "OK": ((.500, -.190, .854), (.068, .048)),
             "NG": ((.500, .160, .854), (.068, .048))}
    for name, (centre, half) in specs.items():
        displacement = np.abs(corners - centre)
        if np.all(displacement[:, :2] <= half) and np.max(displacement[:, 2]) < .0015:
            return name
    return "outside"


def verify_seat(witness, station, part):
    require(witness["part"] == part and witness["seated"] and witness["region"] == station,
            f"{part}: no stable seating witness in {station}")
    require(physical_region(witness["position_m"], witness["orientation_wxyz"]) == station,
            f"{part}: sole geometry outside {station}")
    for name, maximum in (("corner_height_error_m", .0015), ("linear_speed_m_s", .005),
                          ("angular_speed_rad_s", .1)):
        require(0 <= witness[name] < maximum, f"{part}: out-of-range witness {name} in {station}")


def verify_replay(folder, trace, inspections, rows):
    stage = Usd.Stage.Open(str(folder / "replay.usda"))
    require(stage is not None and "./live.usda" in stage.GetRootLayer().subLayerPaths, "replay composition")
    near(stage.GetStartTimeCode(), 0, 0, "replay first frame")
    near(stage.GetEndTimeCode(), trace[-1]["frame"], 0, "replay last frame")
    near(stage.GetTimeCodesPerSecond(), FRAME_HZ, 0, "replay frame rate")
    live = stage.GetPrimAtPath("/World/Robot/Panda/panda_hand")
    require(live.IsValid(), "replay does not resolve the installed Franka hand")
    parts = {p: stage.GetPrimAtPath("/World/ActiveParts/" + p) for p in PARTS}
    require(all(parts.values()), "replay does not resolve both workpieces")
    tcp_delta = body_delta = rotation_delta = 0.
    for frame, sample in enumerate(trace):
        at = Usd.TimeCode(frame)
        cache = UsdGeom.XformCache(at)
        point = cache.GetLocalToWorldTransform(live).Transform(Gf.Vec3d(0, 0, TCP_OFFSET))
        tcp_delta = max(tcp_delta, math.dist(point, sample["tcp"]))
        for p, prim in parts.items():
            observed = sample["parts"][p]
            transform = cache.GetLocalToWorldTransform(prim)
            body_delta = max(body_delta, math.dist(transform.ExtractTranslation(), observed["xyz"]))
            quat = transform.ExtractRotationQuat().GetNormalized()
            q = np.array([quat.GetReal(), *quat.GetImaginary()])
            observed_q = np.array(observed["wxyz"])
            rotation_delta = max(rotation_delta, float(min(np.linalg.norm(q-observed_q), np.linalg.norm(q+observed_q))))
            require(UsdGeom.Imageable(prim).ComputeVisibility(at) == observed["visibility"],
                    f"{p}: replay and trace visibility disagree at {frame}")
    near(tcp_delta, 0, 1e-6, "stage/trace TCP replay [m]")
    near(body_delta, 0, 1e-6, "stage/trace part position [m]")
    near(rotation_delta, 0, 2e-5, "stage/trace part quaternion")
    for row, event in zip(rows, inspections):
        part, frame = row["part"], event["frame"]
        path = parts[part].GetPath()
        measured = measure_part(stage, path, time=frame)
        truth = part_truth(stage, path, time=frame)
        near(measured.height_mm, row["probe"]["height_mm"], .02, f"{part}: offline probe [mm]")
        near(truth["height_mm"], row["geometry_reference"]["height_mm"], .001, f"{part}: offline part datum [mm]")
        require(measured.within_tolerance == row["probe"]["within_tolerance"], f"{part}: offline probe verdict")
        ruby = stage.GetPrimAtPath("/World/Equipment/RubyContactBall")
        ruby_mesh = next(UsdGeom.Mesh(p) for p in Usd.PrimRange(ruby) if p.IsA(UsdGeom.Mesh))
        local = np.asarray(ruby_mesh.GetPointsAttr().Get(frame))
        world = np.asarray(UsdGeom.XformCache(frame).GetLocalToWorldTransform(ruby_mesh.GetPrim()))
        tip_z = float((local @ world[:3, :3] + world[3, :3])[:, 2].min())
        near(tip_z, measured.contact_world_m[2], .000025, f"{part}: ruby contact [m]")
    return {"max_tcp_difference_m": tcp_delta, "max_part_difference_m": body_delta,
            "max_part_quaternion_difference": rotation_delta, "offline_inspections": 2}


def verify(folder, stale, source_root=HERE):
    manifest = {name: digest(folder / name) for name in FILES}
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
    trace = [json.loads(line) for line in (folder / "motion.jsonl").read_text(encoding="utf-8").splitlines()]
    condition = "stale_ok" if stale else "normal"
    require(result["status"] == "completed" and result["condition"] == condition
            and not result.get("diagnostic", False), f"{condition}: missing completed qualification run")
    runtime = result["physics"]
    require(runtime["step_hz"] == 120 and runtime["steps_per_pose"] == 4
            and "PhysX" in runtime["engine"], "unexpected physics runtime")
    near(runtime["gravity_m_s2"], 9.81, .001, "native gravity magnitude")
    require(manifest["motion.jsonl"] == result["motion_trace_sha256"], "trace digest differs from run manifest")
    require(set(SOURCES) <= result["source_sha256"].keys(), "source manifest incomplete")
    # Historical design-study outputs preceded the run-local stage directory.
    # They can be audited against their exact original sources with --source-root.
    stage_root = folder / "stage" if (folder / "stage").is_dir() else source_root
    for name in SOURCES:
        root = stage_root if name in ("cell_c_inspection.usda", "inspection_equipment.usdc") else source_root
        require(digest(root / name) == result["source_sha256"][name], f"run source digest differs: {name}")
    rows = result["parts"]
    recipe = result.get("recipe")
    if recipe and "condition" in recipe:
        require(recipe["condition"] == condition, "recipe controller and native condition differ")
    schedule = recipe if recipe is not None else {"errors_mm": [0., .60], "infeed_offsets_mm": [[0.,0.],[0.,0.]]}
    errors = np.asarray(schedule["errors_mm"], dtype=float)
    offsets = np.asarray(schedule["infeed_offsets_mm"], dtype=float)
    require(errors.shape == (2,) and offsets.shape == (2,2) and np.isfinite(errors).all()
            and np.isfinite(offsets).all(), "invalid two-part geometry or infeed recipe")
    require([r["part"] for r in rows] == list(PARTS), "run must contain exactly two ordered unique parts")
    require(len(trace) == result["frames"] + 1 and result["fps"] == FRAME_HZ, "trace frame count/rate")
    expected = []
    for part in PARTS:
        expected.extend([(part, "infeed_index"), (part, "grasp"), (part, "release"), (part, "inspection"),
                         (part, "grasp"), (part, "release"), (part, "output_settled")])
        if part == "P1":
            expected.append((part, "external_takeaway"))
    require([(e["part"], e["event"]) for e in events] == expected, "unexpected cycle/event order")
    require([e["frame"] for e in events] == sorted(e["frame"] for e in events), "event times regress")
    for frame, sample in enumerate(trace):
        require(sample["frame"] == frame and set(sample["parts"]) == set(PARTS), f"incomplete frame {frame}")
        require(np.isfinite(sample["tcp"]).all(), f"invalid TCP at frame {frame}")
        for observed in sample["parts"].values():
            require(np.isfinite(observed["xyz"]).all(), f"invalid part position at frame {frame}")
            near(float(np.linalg.norm(observed["wxyz"])), 1, 2e-5, f"part quaternion at {frame}")
    inspections, outputs = [e for e in events if e["event"] == "inspection"], [e for e in events if e["event"] == "output_settled"]
    grasps = [e for e in events if e["event"] == "grasp"]
    releases = [e for e in events if e["event"] == "release"]
    inputs = [e for e in events if e["event"] == "infeed_index"]
    require([e["station"] for e in grasps] == ["infeed", "fixture"] * 2, "acquisition sequence")
    for event in releases:
        sample = trace[event["frame"]]
        near(math.dist(event["position_m"], sample["parts"][event["part"]]["xyz"]), 0, 1e-6,
             "free-flight start vs trace [m]")
        near(math.dist(event["tcp"],sample["tcp"]), 0, 1e-6, "release TCP vs trace [m]")
    for event in grasps:
        observed = trace[event["frame"]]["parts"][event["part"]]
        tcp = trace[event["frame"]]["tcp"]
        grip_offset = Gf.Quatd(observed["wxyz"][0], Gf.Vec3d(*observed["wxyz"][1:])).Transform(Gf.Vec3d(-.013,.026,.049))
        near(math.dist(np.asarray(observed["xyz"])+grip_offset, tcp), 0, .003, "acquisition offset [m]")
        near(math.dist(event["tcp"],tcp), 0, 1e-6, "logged grasp TCP [m]")
        if "witness" in event:
            require(0 <= event["witness"]["grasp_error_m"] <= .003, "virtual grasp witness")
    reports = []
    for index, (row, inspection, out) in enumerate(zip(rows, inspections, outputs)):
        part, reference, probe = row["part"], row["geometry_reference"], row["probe"]
        expected_height = 33.0 + errors[index]
        in_spec = bool(abs(errors[index]) <= .2001)
        quality_bin = "OK" if in_spec else "NG"
        previous_ok = index == 1 and reports[0]["observed_bin"] == "OK"
        route = "OK" if stale and previous_ok else quality_bin
        result_id = "P1" if stale and previous_ok else part
        require([e["station"] for e in releases[2*index:2*index+2]] == ["fixture", route],
                f"{part}: release station sequence")
        if recipe is not None or "infeed_seat" in row:
            infeed = row["infeed_seat"]
            verify_seat(infeed, "infeed", part)
            near(math.dist(inputs[index]["commanded_xy_offset_mm"], offsets[index]),
                 0., 1e-8, f"{part}: commanded source offset [mm]")
            target_xy = np.array([-.440,-.248]) + offsets[index]/1000
            near(math.dist(infeed["position_m"][:2], target_xy),0.,.00030,f"{part}: settled source pose [m]")
            initial = trace[inputs[index]["frame"]+1]["parts"][part]["xyz"]
            near(math.dist(initial[:2], target_xy),0.,.00030,f"{part}: measured source pose [m]")
        require(inspection["reference"] == reference and inspection["probe"] == probe and
                inspection["decision"] == row["decision"], f"{part}: event/result inspection mismatch")
        near(reference["height_mm"], expected_height, .001, f"{part}: actual geometry [mm]")
        near(probe["height_mm"], expected_height, .020, f"{part}: probe vs drawing [mm]")
        require(reference["within_tolerance"] == probe["within_tolerance"] == in_spec, f"{part}: wrong quality verdict")
        require("FacedDatumBoss" in probe["hit_mesh"] and len(probe["seat_errors_mm"]) == 3
                and max(map(abs,probe["seat_errors_mm"])) <= .15 and probe["tilt_deg"] <= .15
                and probe["locator_error_mm"] <= .35, f"{part}: seating/probe registration")
        require(row["decision"] == dict(part_id=part, result_part_id=result_id, route=route, fresh=(result_id==part)),
                f"{part}: incorrect route or reading ownership")
        verify_seat(row["fixture_seat"], "fixture", part)
        verify_seat(row["physical_output"], route, part)
        observed = trace[out["frame"]]["parts"][part]
        release = releases[2 * index + 1]
        start_z = release["position_m"][2]
        drop = start_z - observed["xyz"][2]
        require(.0015 <= drop <= .0045, f"{part}: bin descent outside 3 ± 1.5 mm: {drop} m")
        flight = trace[release["frame"]+1:min(release["frame"]+4,out["frame"]+1)]
        require(any(start_z - tick["parts"][part]["xyz"][2] > .0005 for tick in flight),
                f"{part}: no measured downward movement after gravity release")
        near(math.dist(observed["xyz"], row["physical_output"]["position_m"]), 0, 1e-6, "output pose vs trace")
        for sample in trace[out["frame"]-5:out["frame"]+1]:
            v = sample["parts"][part]
            require(physical_region(v["xyz"],v["wxyz"]) == route, f"{part}: transient or incorrect output")
        previous = trace[out["frame"]-1]["parts"][part]
        trace_speed = FRAME_HZ * math.dist(observed["xyz"], previous["xyz"])
        require(trace_speed < .0055, f"{part}: measured trace still moving in {route} at {trace_speed} m/s")
        require(out["observed"] == route and out["correct"] == row["correct_destination"] == (route == quality_bin),
                f"{part}: wrong observed result")
        reports.append({"part": part, "height_mm": probe["height_mm"], "geometry_height_mm": reference["height_mm"],
                        "result_owner": result_id, "observed_bin": route,
                        "release_to_bin_drop_mm": 1000 * drop,
                        "trace_end_speed_m_s": trace_speed, "correct": out["correct"]})
    takeaway = next(e["frame"] for e in events if e["event"] == "external_takeaway")
    require(all(s["parts"]["P1"]["visibility"] == "invisible" for s in trace[takeaway+1:]),
            "first article not removed before the second output")
    m = result["motion"]
    clearance = m["clearance"]
    require(0 < m["samples"] == result["frames"] and clearance["frames"] == m["samples"], "motion/clearance coverage")
    require(not clearance["unresolved_pairs"] and clearance["minimum_sampled_lower_bound_m"] > 0,
            "unresolved equipment interference or nonpositive separation")
    require(clearance["moving_links"] >= 9 and clearance["equipment_meshes"] > 0, "missing clearance geometry")
    require(m["max_tcp_error_m"] <= .0005 and m["max_orientation_error_rad"] <= .005 and
            m["minimum_joint_limit_margin_rad"] >= 0 and m["max_stage_fk_difference_m"] <= 1e-6,
            "joint, IK, or composed FK failure")
    require(0 < m["max_joint_speed_rad_s"] <= .8*2.61 + 1e-6, "configured joint speed envelope")
    replay = verify_replay(folder, trace, inspections, rows)
    return {"condition":condition, "recipe":recipe, "folder":str(folder), "frames":len(trace), "parts":reports,
            "sampled_clearance_lower_bound_m":clearance["minimum_sampled_lower_bound_m"],
            "clearance_frames":clearance["frames"], "clearance_scope":clearance["excluded"],
            "source_sha256":result["source_sha256"], "sha256":manifest, "replay":replay}


def verify_geometry_audits(summary, xrdf):
    folder = Path(summary["folder"])
    from cellio.inspection.study.audit_gripper_mesh import audit as audit_grasps
    from cellio.inspection.study.audit_self_clearance import audit_stage, default_xrdf
    from cellio.inspection.study.verify_payload import evaluate

    def audit_robot():
        stage = Usd.Stage.Open(str(folder / "replay.usda"))
        return audit_stage(stage, int(stage.GetEndTimeCode()), xrdf or default_xrdf())

    for filename, calculate in (
        ("self-clearance.json", audit_robot),
        ("gripper-mesh.json", lambda: audit_grasps(folder)),
        ("payload-clearance.json", lambda: evaluate(folder)),
    ):
        if not (folder / filename).exists():
            write_json(folder / filename, calculate())

    def read(name):
        path = folder / name
        summary["sha256"][name] = digest(path)
        return json.loads(path.read_text(encoding="utf-8"))

    robot = read("self-clearance.json")
    require(robot["passes"] and not robot["unresolved"] and robot["minimum_lower_bound_m"] > 0
            and robot["frames_checked"] == summary["frames"] and robot["pairs_checked_per_frame"] == 28
            and len(robot["checked_pairs"]) == 28, "self-clearance audit missing or failed")
    mask = xrdf or Path(robot["xrdf"])
    require(digest(mask if mask.is_file() else default_xrdf()) == robot["xrdf_sha256"],
            "Franka self-collision mask digest")
    grip = read("gripper-mesh.json")
    require(grip["passes"] and len(grip["grasps"]) == grip["expected_grasps"] == 4, "grasp geometry audit")
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
    grasps = [e for e in events if e["event"] == "grasp"]
    for row, event in zip(grip["grasps"], grasps):
        require(all(row[k] == event[k] for k in ("frame", "part", "station")) and row["passes_geometric_fit"]
                and len(row["reads"]) == 4, "grasp geometry evidence linkage")
        for ray in row["reads"]:
            require(abs(ray["left_gap_mm"]) < .2 and abs(ray["right_gap_mm"]) < .2, "grasp bilateral gap")
    payload = read("payload-clearance.json")
    for name in ("replay.usda", "live.usda", "motion.jsonl", "events.json"):
        require(payload["run_inputs_sha256"][name] == summary["sha256"][name],
                f"carried-part audit used different {name}")
    require(payload["passed"] and not payload["unresolved"] and payload["minimum_unintended_sampled_bound_m"] > 0
            and payload["min_pin_hole_radial_bound_m"] > 0 and len(payload["intervals"]) == 4
            and 0 < payload["max_interpolated_vertex_step_m"] <= payload["interpolation_limit_m"] + 1e-9,
            "unresolved carried-part clearance")
    releases = [e for e in events if e["event"] == "release"]
    for interval, grasp, release in zip(payload["intervals"], grasps, releases):
        require((interval["part"], interval["source"], interval["target"],
                 interval["first_frame"], interval["last_frame"]) ==
                (grasp["part"], grasp["station"], release["station"], grasp["frame"], release["frame"]),
                "carried-part audit interval does not match native grasp and release events")
    summary["additional_geometry_audits"] = {
        "self_clearance_lower_bound_m": robot["minimum_lower_bound_m"],
        "self_pairs_per_frame": 28, "checked_grasps": len(grasps),
        "payload_sampled_clearance_lower_bound_m": payload["minimum_unintended_sampled_bound_m"],
        "payload_pin_radial_lower_bound_m": payload["min_pin_hole_radial_bound_m"],
        "audit_source_sha256": {name:digest(HERE / name) for name in
                                ("audit_self_clearance.py", "audit_gripper_mesh.py", "verify_payload.py")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normal", type=Path)
    parser.add_argument("stale_ok", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--core-only", action="store_true", help="Check native conditions before the geometry audit files arrive")
    parser.add_argument("--source-root", type=Path, default=HERE, help="Exact historical sources for replaying archived runs")
    parser.add_argument("--xrdf", type=Path, help="Installed Franka robot.xrdf for collision exclusions")
    args = parser.parse_args()
    normal, stale = (verify(args.normal.resolve(), False, args.source_root),
                     verify(args.stale_ok.resolve(), True, args.source_root))
    require(normal["source_sha256"] == stale["source_sha256"], "conditions used different source revisions")
    def pair_settings(recipe):
        if recipe is None:
            return None
        return {k: v for k, v in recipe.items() if k not in ("id", "condition")}
    require(pair_settings(normal["recipe"]) == pair_settings(stale["recipe"]),
            "conditions used different recipe, placement or protocol")
    for a, b in zip(normal["parts"], stale["parts"]):
        near(a["geometry_height_mm"], b["geometry_height_mm"], .001, "across-condition part geometry [mm]")
    if not args.core_only:
        for summary in (normal, stale):
            verify_geometry_audits(summary, args.xrdf)
    comparison = {"verified":True, "geometry_audits_included":not args.core_only,
                  "conditions":[normal, stale], "verifier_sha256":digest(Path(__file__))}
    default_name = "comparison-core.json" if args.core_only else "comparison.json"
    path = args.out or args.normal.resolve().parent / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    for summary in comparison["conditions"]:
        summary["folder"] = Path(summary["folder"]).name
    pending = path.with_suffix(".partial")
    pending.write_text(json.dumps(comparison, indent=2, allow_nan=False), encoding="utf-8")
    pending.replace(path)
    print(json.dumps({"verified":True, "file":str(path), "sha256":digest(path),
                      "observed_bins":{r["condition"]:[p["observed_bin"] for p in r["parts"]]
                                      for r in (normal,stale)}}))


if __name__ == "__main__":
    main()
