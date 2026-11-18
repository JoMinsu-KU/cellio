"""Measure the TCP, FK and pad geometry on the installed Franka asset before trials."""

import argparse
import json
from math import dist
from pathlib import Path

from cellio.core.models import Tick
from cellio.io import write_json
from cellio.scenes.definition import ARM_NAMES, HOME
from .geometry import compose, inverse, quat_distance
from .plant import IsaacPlant


def _offset_joint(index, offset):
    q = list(HOME)
    q[index] += offset
    return tuple(q)


def _pad_check(plant, truth):
    """In hand coordinates, check the TCP lies between the two opposed pad patches."""
    hand = truth.bodies["hand"]
    inv_hand = inverse((*hand.position_m, *hand.orientation_wxyz))
    bounds = []
    for entity in ("finger_left", "finger_right"):
        body = truth.bodies[entity]
        relative = compose(inv_hand, (*body.position_m, *body.orientation_wxyz))
        points = [compose(relative, (*p, 1., 0., 0., 0.))[:3] for p in plant.builder.pad_corners[entity]]
        bounds.append((tuple(map(min, zip(*points))), tuple(map(max, zip(*points)))))
    bounds.sort(key=lambda pair: pair[0][1])
    lower, higher = bounds
    center_y = (lower[1][1] + higher[0][1]) / 2
    p = plant.hand_T_tcp[:3]
    overlap = [(max(lower[0][axis], higher[0][axis]), min(lower[1][axis], higher[1][axis]))
               for axis in (0, 2)]
    # Permit the same 4 mm error as the operational TCP waypoint tolerance.
    passed = (lower[1][1] < higher[0][1] and abs(p[1]-center_y) <= .004
              and all(lo-.004 <= p[axis] <= hi+.004 for axis, (lo, hi) in zip((0, 2), overlap)))
    return {"bounds_in_hand_m": bounds, "midplane_y_m": center_y, "tangential_overlap_m": overlap,
            "tcp_m": p, "passed": passed}


def measure_geometry(plant, output, lock_path):
    samples = []
    poses = (("home", HOME), ("joint1_offset", _offset_joint(0, .22)),
             ("joint3_offset", _offset_joint(2, -.25)), ("joint5_offset", _offset_joint(4, .28)),
             ("base_offset", HOME))
    report = {"profile": "isaac62_commissioning", "scope": "FK, TCP, base offset and opposed pad geometry",
              "passed": False, "samples": samples}
    try:
        plant.load({"scene_id": "cell_a"})
        for index, (name, joints) in enumerate(poses, 1):
            base = (.030, -.015, .750) if name == "base_offset" else (0., 0., .750)
            # This is a new commissioning stage before S0, never an episode-time teleport.
            plant._reset({}, index, joints, base)
            truth = plant.snapshot(Tick(f"geometry-{index}", index, 0))
            body = truth.bodies["cell_tcp"]
            actual = (*body.position_m, *body.orientation_wxyz)
            predicted = plant.motion.measured_fk_tcp()
            position_error = dist(actual[:3], predicted[:3])
            angle = quat_distance(actual[3:], predicted[3:])
            pads = _pad_check(plant, truth)
            sample = {"pose": name, "base_m": base, "q_measured": [truth.joints[n].position for n in ARM_NAMES],
                      "physics_tcp": actual, "fk_tcp": predicted, "fk_position_error_m": position_error,
                      "fk_orientation_error_rad": angle, "pads": pads,
                      "passed": position_error <= .004 and angle <= .05 and pads["passed"]}
            if name == "base_offset":
                first_position = samples[0]["physics_tcp"][:3]
                displacement = tuple(x-y for x, y in zip(actual[:3], first_position))
                sample["base_displacement_error_m"] = dist(displacement, (.030, -.015, 0.))
                sample["passed"] = sample["passed"] and sample["base_displacement_error_m"] <= .004
            samples.append(sample)
        report["passed"] = all(s["passed"] for s in samples)
        report["geometry_manifest"] = plant.geometry_manifest
        report["provenance"] = plant.provenance
        if report["passed"]:
            write_json(Path(lock_path), {"schema": "cellio.robot_profile/1.0",
                       "hand_T_tcp": plant.hand_T_tcp, "tool_frame": plant.motion.site,
                       "site_T_tcp": plant.motion.site_T_tcp, "pad_collider_paths": plant.builder.pad_paths,
                       "dof_names": plant.names, "finger_drivers": plant.builder.finger_drivers})
        return report
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        write_json(Path(output), report)
        plant.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--write-lock", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() == args.write_lock.resolve():
        parser.error("The measurement report and robot lock need distinct paths")
    report = measure_geometry(IsaacPlant(asset_root=args.assets, robot_profile=args.profile),
                              args.output, args.write_lock)
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))
    return 0 if report["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
