import json
import math
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cellio.adapters.isaac62.assets import LOCK, file_sha256, verify_assets
from cellio.adapters.isaac62.geometry import compose, corners, inverse, world_aabb
from cellio.adapters.isaac62.motion import FrankaMotion
from cellio.adapters.isaac62.contacts import ContactPairs
from cellio.adapters.isaac62.plant import IsaacPlant
from cellio.core.models import CommandIntent, CommandKind
from cellio.io import CellIOError
from cellio.scenes import get_scene


def test_import_does_not_start_kit_or_import_usd():
    code = ("import sys; from cellio.adapters.isaac62 import IsaacPlant; "
            "assert not {'omni.kit.app','pxr.Usd','isaacsim.simulation_app'}.intersection(sys.modules)")
    subprocess.run([sys.executable, "-c", code], check=True,
                   env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})


def test_calibrated_site_and_rotated_bounds():
    # A rotated hand offset must affect the motion site in world coordinates.
    hand = (0., 0., 1., math.sqrt(.5), 0., math.sqrt(.5), 0.)
    offset = (0., 0., .1034, 1., 0., 0., 0.)
    tcp = compose(hand, offset)
    assert tcp[:3] == pytest.approx((.1034, 0., 1.))
    assert compose(tcp, inverse(offset)) == pytest.approx(hand)
    low, high = world_aabb(hand, corners((-.1, -.2, -.3), (.1, .2, .3)))
    assert low == pytest.approx((-.3, -.2, .9))
    assert high == pytest.approx((.3, .2, 1.1))


def test_authoritative_waypoint_dwell_uses_13_measured_boundaries():
    motion = object.__new__(FrankaMotion)
    motion.goal = (.45, -.116, .815, 0., 0., 1., 0.)
    motion.active = CommandIntent("a:cmd:11", 11, "a", 1, 0, 3, "actuator.robot",
                                  CommandKind.POSE, motion.goal, 0)
    motion.completed, motion.reached, motion.failed = -1, False, False
    motion.applied_step, motion.first_in_range = 3, None
    for k in range(3, 15):
        motion.observe(motion.goal, k)
    assert not motion.reached and motion.completed == -1
    motion.observe(motion.goal, 15)
    assert motion.reached and motion.completed == 11 and not motion.failed


def test_scene_seed_coordinates_and_fixture_clearance_geometry():
    a, b = get_scene("cell_a"), get_scene("cell_b")
    p, q = a.initial_pose({"x_offset_m": .001, "y_offset_m": -.004, "yaw_rad": .03})
    assert p == pytest.approx((.451, -.414, .815))
    assert sum(v*v for v in q) == pytest.approx(1.)
    assert b.waypoints["fixture_load_place"][0] != b.waypoints["fixture_unload_grasp"][0]
    jaw = next(box for box in b.boxes if box.kind == "moving_jaw")
    assert jaw.center[0] == .590 and jaw.mass == .25


def test_contact_lifetimes_and_multiple_collider_pairs():
    contact = object.__new__(ContactPairs)
    contact.to_path, contact.lost, contact.active = str, "lost", set()
    contact.keys = {"finger1": "finger_left", "finger2": "finger_left", "part": "part_000"}
    def header(collider, event):
        return SimpleNamespace(collider0=collider, collider1="part", type=event)
    expected = (("finger_left", "part_000"),)
    contact._event([header("finger1", "found"), header("finger2", "found")], [])
    assert contact.snapshot() == expected
    contact._event([], [])
    assert contact.snapshot() == expected
    contact._event([header("finger1", "lost")], [])
    assert contact.snapshot() == expected
    contact._event([header("finger2", "lost")], [])
    assert contact.snapshot() == ()


def test_body_snapshot_copies_arrays_and_rejects_invalid_physics_reads():
    class Buffer:
        def __init__(self, row):
            self.rows = [row]
        def numpy(self):
            return self.rows
    xyz = Buffer([0., 0., .81])
    wxyz = Buffer([1., 0., 0., 0.])
    static = Buffer([0., 0., 0.])
    view = SimpleNamespace(get_world_poses=lambda: (xyz, wxyz), get_velocities=lambda: (static, static),
                           is_physics_tensor_entity_valid=lambda: True)
    plant = IsaacPlant()
    plant.robot, plant.views, plant.body_order = view, {"bodies": view}, ("part_000",)
    initial = plant._read_bodies()["part_000"]
    xyz.rows[0][2] = .82
    assert plant._read_bodies()["part_000"].position_m[2] == .82
    assert initial.position_m[2] == .81
    view.is_physics_tensor_entity_valid = lambda: False
    with pytest.raises(CellIOError, match="tensor view became invalid"):
        plant._read_bodies()


def test_offline_lock_detects_tampering_and_path_escape(tmp_path):
    asset = tmp_path / "franka.usda"
    asset.write_text("#usda 1.0", encoding="utf-8")
    lock = {"schema": "cellio.assets/1.0", "entry": asset.name, "source": "test",
            "files": {asset.name: {"bytes": asset.stat().st_size, "sha256": file_sha256(asset)}}}
    file = tmp_path / LOCK
    file.write_text(json.dumps(lock), encoding="utf-8")
    assert verify_assets(tmp_path) == lock
    asset.write_text("corrupted", encoding="utf-8")
    with pytest.raises(CellIOError):
        verify_assets(tmp_path)
    lock["entry"] = "../franka.usda"
    lock["files"] = {lock["entry"]: {"bytes": 0, "sha256": ""}}
    file.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(CellIOError, match="escapes"):
        verify_assets(tmp_path)


@pytest.mark.skipif(not os.environ.get("CELLIO_RUN_ISAAC_TESTS"),
                    reason="Set CELLIO_RUN_ISAAC_TESTS and CELLIO_ASSET_CACHE on a 6.2 runtime")
def test_isaac_real_one_step_and_hard_reload():
    # Let the executable use the vendor's supported native fast shutdown after
    # assertions complete; a loaded Kit must never terminate the pytest host.
    script = ("import runpy,sys; from cellio.adapters.isaac62.application import worker_main; "
              "native=runpy.run_path(sys.argv[1]); raise SystemExit(worker_main(native['_check_physics']))")
    subprocess.run([sys.executable, "-c", script, __file__], check=True)


def _check_physics():
    from cellio.adapters.isaac62 import IsaacPlant
    from cellio.core.models import Tick
    plant = IsaacPlant(asset_root=os.environ["CELLIO_ASSET_CACHE"],
                       robot_profile=os.environ.get("CELLIO_ROBOT_PROFILE"))
    try:
        for generation, cell in enumerate(("cell_a", "cell_b"), 1):
            plant.load({"scene_id": cell})
            plant.reset({}, generation)
            tick = Tick(cell, generation, 0)
            truth = plant.snapshot(tick)
            assert truth.complete and truth.bodies["cell_tcp"].valid
            assert all(plant.reset_evidence(truth).values())
            assert plant.apply((), {}, tick).accepted
            receipt = plant.advance_one_step(render=generation == 2)
            assert receipt.success and receipt.engine_time_after_s - receipt.engine_time_before_s == pytest.approx(1/120)
            assert plant.snapshot(Tick(cell, generation, 1)).tick.step == 1
    finally:
        plant.close()
    return 0
