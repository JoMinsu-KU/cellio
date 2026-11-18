"""Single-owner PhysX plant. Kit imports and application startup are explicitly deferred."""

from __future__ import annotations

import gc
import json
from math import isclose
from pathlib import Path

from cellio.core.models import (
    ActuationReceipt, AdvanceReceipt, AppliedCommand, BodyState, CellIOError, CommandKind,
    JointState, TruthSnapshot,
)
from cellio.scenes import get_scene
from cellio.scenes.definition import ARM_NAMES, FINGER_NAMES, HOME
from .assets import CATALOG_PATH, verify_assets
from .geometry import compose, rotate, world_aabb

DT = 1 / 120


def _slew(current, goal, delta):
    return current + min(delta, max(-delta, goal-current))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _floats(array):
    return tuple(map(float, array.numpy()[0]))


class IsaacPlant:
    def __init__(self, *, headless=True, asset_root=None, robot_asset=None, robot_profile=None):
        self.headless = headless
        self.asset_root, self.robot_asset = asset_root, robot_asset
        if isinstance(robot_profile, (str, Path)):
            robot_profile = json.loads(Path(robot_profile).read_text(encoding="utf-8"))
        self.profile = dict(robot_profile or {})
        self.hand_T_tcp = tuple(self.profile.get("hand_T_tcp", (0., 0., .1034, 1., 0., 0., 0.)))
        self.scene = self.app = self.builder = self.contact = self.motion = None
        self.generation = None
        self.provenance = {}
        self.geometry_manifest = {}

    def load(self, scene_config):
        scene_id = scene_config.get("scene_id", scene_config.get("id"))
        self.scene = get_scene(scene_id)
        if self.app is not None:
            return
        from isaacsim.simulation_app import SimulationApp
        self.app = SimulationApp({"headless": self.headless, "multi_gpu": False,
                                  "disable_viewport_updates": self.headless})
        import omni.kit.app
        import omni.timeline
        import omni.usd
        from isaacsim.core.simulation_manager import SimulationManager
        from isaacsim.core.rendering_manager import RenderingManager
        from isaacsim.storage.native import get_assets_root_path

        self.manager, self.rendering = SimulationManager, RenderingManager
        self.context = omni.usd.get_context()
        self.timeline = omni.timeline.get_timeline_interface()
        extensions = omni.kit.app.get_app().get_extension_manager()
        for name in ("isaacsim.robot_motion.experimental.motion_generation", "isaacsim.robot_motion.cumotion"):
            if not extensions.is_extension_enabled(name):
                extensions.set_extension_enabled_immediate(name, True)
            if not extensions.is_extension_enabled(name):
                raise CellIOError("ENV_EXTENSION_MISSING", name)
        if self.robot_asset is None:
            if self.asset_root:
                lock = verify_assets(self.asset_root)
                self.robot_asset = str((Path(self.asset_root) / lock["entry"]).resolve())
            else:
                root = get_assets_root_path()
                if not root:
                    raise CellIOError("ASSET_REFERENCE_FAILED", "No NVIDIA asset root")
                self.robot_asset = root.rstrip("/") + "/" + CATALOG_PATH

    def _release_stage(self):
        if self.contact is not None:
            self.contact.close()
            self.contact = None
        if self.builder is not None:
            self.timeline.stop()
            self.app.update()  # Dispatch STOP before destroying PhysX tensor objects.
            self.motion = self.robot = self.views = self.builder = self.physics_scene = None
            self._cache = None
            gc.collect()
            self.context.close_stage()

    def reset(self, initial_state, generation):
        if self.app is None:
            raise CellIOError("ENV_NOT_LOADED", "Call load before reset")
        if self.generation is not None and generation <= self.generation:
            raise CellIOError("STALE_GENERATION", str(generation))
        from isaacsim.core.experimental.prims import Articulation, RigidPrim
        from isaacsim.core.simulation_manager import PhysxScene
        from .contacts import ContactPairs
        from .motion import FrankaMotion
        from .stage import ROBOT, StageBuilder

        self._release_stage()
        self.generation, self.step, self._cache = generation, 0, None
        self.context.new_stage()
        stage = self.context.get_stage()
        self.manager.switch_physics_engine("physx")
        self.physics_scene = PhysxScene("/World/PhysicsScene")
        self.physics_scene.set_steps_per_second(120)
        self.physics_scene.set_solver_type("TGS")
        self.physics_scene.set_enabled_gpu_dynamics(False)
        self.physics_scene.set_gravity((0., 0., -9.81))
        self.builder = StageBuilder(stage, self.scene, self.robot_asset, self.profile)
        self.manager.setup_simulation(dt=DT, device="cpu")
        self.manager.enable_fabric(True)
        self.timeline.play()
        self.manager.initialize_physics()
        if (self.manager.get_physics_simulation_view() is None or str(self.manager.get_device()) != "cpu"
            or self.physics_scene.get_steps_per_second() != 120 or self.physics_scene.get_enabled_gpu_dynamics()
            or self.physics_scene.get_solver_type() != "TGS"):
            raise CellIOError("ENV_PHYSICS_MISMATCH", "PhysX CPU/TGS/120 Hz readback failed")
        self.robot = Articulation(ROBOT)
        self.names = list(self.robot.dof_names)
        if tuple(n for n in self.names if n in ARM_NAMES) != ARM_NAMES or not set(FINGER_NAMES) <= set(self.names):
            raise CellIOError("ASSET_BINDING_MISMATCH", f"Unexpected robot joints: {self.names!r}")
        indices = self.robot.get_dof_indices(list(ARM_NAMES + FINGER_NAMES)).numpy().tolist()
        self.robot.set_dof_positions([[*HOME, .040, .040]], dof_indices=indices)
        self.robot.set_dof_velocities([[0.] * len(indices)], dof_indices=indices)
        driver_names = list(ARM_NAMES) + self.builder.finger_drivers
        drivers = self.robot.get_dof_indices(driver_names).numpy().tolist()
        self.robot.set_dof_position_targets([[*HOME, *([.040]*len(self.builder.finger_drivers))]], dof_indices=drivers)
        self.views = {entity: RigidPrim(path) for entity, path in self.builder.paths.items()}
        pos, rotation = self.scene.initial_pose(initial_state)
        self.views["part_000"].set_world_poses(positions=[pos], orientations=[rotation])
        self.views["part_000"].set_velocities(linear_velocities=[[0., 0., 0.]], angular_velocities=[[0., 0., 0.]])
        self.contact = ContactPairs(self.builder.collider_ids)
        self.motion = FrankaMotion(self.robot, self.builder, self.profile, self.hand_T_tcp)
        self._goals = {"actuator.gripper": .080, "actuator.belt": False}
        self._slewed = {n: .040 for n in self.builder.finger_drivers}
        self._held_drivers = {}
        self._evidence, self._overrides = {}, set()
        self.belt_speed, self.process_steps = 0., 0
        for _ in range(240):
            self.manager.step(steps=1, update_fabric=True)
        self._offset = self.manager.get_simulation_time()
        self.geometry_manifest = {"part_dimensions_m": (.04, .04, .04),
                                  "collider_local_corners": self.builder.local_corners,
                                  "collider_id_by_path": self.builder.collider_ids,
                                  "hand_T_tcp": self.hand_T_tcp, "site_T_tcp": self.motion.site_T_tcp}
        self.provenance = {"device": str(self.manager.get_device()), "solver": self.physics_scene.get_solver_type(),
                           "physics_hz": self.physics_scene.get_steps_per_second(),
                           "gravity": list(self.physics_scene.get_gravity()), "episode_clock_offset_s": self._offset,
                           "robot_asset": str(self.robot_asset), "tool_site": self.motion.site,
                           "dof_names": self.names, "finger_drivers": list(self.builder.finger_drivers)}

    def _body(self, entity, view):
        p, q = view.get_world_poses()
        v, w = view.get_velocities()
        return BodyState(entity, "world", _floats(p), _floats(q), _floats(v), _floats(w), True)

    def snapshot(self, tick):
        if self.generation != tick.generation or self.step != tick.step:
            raise CellIOError("CLOCK_ORDER", "Unexpected plant snapshot")
        if self._cache is not None:
            if self._cache.tick != tick:
                raise CellIOError("CLOCK_ORDER", "Tick identity changed")
            return self._cache
        bodies = {entity: self._body(entity, view) for entity, view in self.views.items()}
        hand = bodies["hand"]
        hand_pose = (*hand.position_m, *hand.orientation_wxyz)
        tcp = compose(hand_pose, self.hand_T_tcp)
        delta_v = _cross(hand.angular_velocity_rps, rotate(hand.orientation_wxyz, self.hand_T_tcp[:3]))
        tcp_v = tuple(v+d for v, d in zip(hand.linear_velocity_mps, delta_v))
        bodies["cell_tcp"] = BodyState("cell_tcp", "world", tcp[:3], tcp[3:], tcp_v, hand.angular_velocity_rps, True)
        q = _floats(self.robot.get_dof_positions())
        qdot = _floats(self.robot.get_dof_velocities())
        joints = {name: JointState(name, "revolute" if name in ARM_NAMES else "prismatic", p, v, None, True)
                  for name, p, v in zip(self.names, q, qdot, strict=True)}
        self.motion.observe(tcp, self.step)
        meta = {"gripper.gap_m": sum(joints[n].position for n in FINGER_NAMES),
                "gripper.opening_min_m": min(joints[n].position for n in FINGER_NAMES),
                "gripper.max_speed_mps": max(abs(joints[n].velocity) for n in FINGER_NAMES),
                "robot.at_waypoint": self.motion.reached, "robot.motion_failed": self.motion.failed,
                "robot.completed_command_id": self.motion.completed}
        if self.builder.belt is not None:
            meta["belt.surface_speed_mps"] = float(self.builder.belt.GetSurfaceVelocityAttr().Get()[1])
        for entity, corners in self.builder.local_corners.items():
            body = bodies[entity]
            bounds = world_aabb((*body.position_m, *body.orientation_wxyz), corners)
            for suffix, point in zip(("min", "max"), bounds):
                for axis, coordinate in zip("xyz", point):
                    meta[f"{entity}.bounds.{suffix}_{axis}"] = coordinate
        self._cache = TruthSnapshot(tick, bodies, joints, self.contact.snapshot(), meta, True)
        return self._cache

    def driver_positions(self, target, truth):
        if target == "actuator.gripper":
            return {n: truth.joints[n].position for n in self.builder.finger_drivers}
        if target == "actuator.clamp":
            return {"clamp": truth.joints["clamp"].position}
        raise CellIOError("CFG_FAULT_TARGET", target)

    def coordinate(self, target, truth):
        if target == "actuator.gripper":
            return truth.plant_state["gripper.gap_m"]
        return self.driver_positions(target, truth)["clamp"]

    def actuation_evidence(self, target):
        return self._evidence.get(target, {})

    def _receive(self, intent, truth, sim_time):
        target = intent.actuator_id
        if intent.kind == CommandKind.CANCEL_OPERATION:
            return
        if target == "actuator.robot":
            if intent.kind == CommandKind.HOLD:
                self.motion.hold()
            else:
                tcp = truth.bodies["cell_tcp"]
                self.motion.start(intent, (*tcp.position_m, *tcp.orientation_wxyz), sim_time, self.step)
        elif intent.kind == CommandKind.HOLD:
            self._held_drivers[target] = self.driver_positions(target, truth)
            self._goals[target] = self.coordinate(target, truth)
        else:
            self._held_drivers.pop(target, None)
            self._goals[target] = intent.value

    def _gripper(self, truth, overrides):
        target = "actuator.gripper"
        drivers = self.builder.finger_drivers
        measured = self.driver_positions(target, truth)
        override = overrides.get(target)
        if (target in self._overrides) != (override is not None):
            self._slewed.update(measured)
        coordinate = self._goals[target]
        nominal = {}
        for n in drivers:
            desired = self._held_drivers.get(target, {}).get(n, coordinate/2)
            nominal[n] = _slew(self._slewed[n], desired, .040 * DT)
        values = override["driver_positions"] if override is not None else nominal
        indices = [self.names.index(n) for n in drivers]
        self.robot.set_dof_position_targets([[values[n] for n in drivers]], dof_indices=indices)
        self.robot.set_dof_velocity_targets([[0.]*len(indices)], dof_indices=indices)
        actual = dict(zip(drivers, _floats(self.robot.get_dof_position_targets(dof_indices=indices)), strict=True))
        if any(abs(actual[n]-values[n]) > 1e-6 for n in drivers):
            raise CellIOError("BACKEND_TARGET_REJECTED", target)
        self._slewed.update(actual)
        self._evidence[target] = {"nominal_coordinate": coordinate, "nominal_targets": nominal, "applied_targets": actual}
        return tuple(actual.values())

    def apply(self, commands, overrides, tick):
        truth = self.snapshot(tick)
        if getattr(self, "_last_applied", None) == tick:
            raise CellIOError("CLOCK_ORDER", "Repeated actuation")
        sim_time = self.manager.get_simulation_time()
        delivered = [d for d in commands if d.status == "DELIVERED"]
        for decision in delivered:
            self._receive(decision.intent, truth, sim_time)
        self.motion.servo(sim_time, carrying=self._goals["actuator.gripper"] < .055)
        committed = {"actuator.gripper": self._gripper(truth, overrides)}
        if self.builder.belt is not None:
            self.belt_speed = _slew(self.belt_speed, .06 if self._goals["actuator.belt"] else 0., DT)
            self.builder.belt.GetSurfaceVelocityAttr().Set(self.builder.Gf.Vec3f(0., self.belt_speed, 0.))
            committed["actuator.belt"] = float(self.builder.belt.GetSurfaceVelocityAttr().Get()[1])
        committed["actuator.robot"] = _floats(self.robot.get_dof_position_targets(dof_indices=list(self.motion.arm_indices)))
        self._overrides = set(overrides)
        applied = tuple(AppliedCommand(
            d.intent.command_id, d.intent.actuator_id, tick.generation, d.intent.issued_step,
            d.due_step, tick.step, "APPLIED", d.reason, committed.get(d.intent.actuator_id, d.intent.value))
            for d in delivered)
        self._last_applied = tick
        return ActuationReceipt(tick, True, applied, committed, tuple(o["fault_id"] for o in overrides.values()))

    def advance_one_step(self, render=False):
        if self._cache is None or self._last_applied != self._cache.tick:
            raise CellIOError("CLOCK_ORDER", "Advance must follow actuation")
        before = self.manager.get_simulation_time()
        self.manager.step(steps=1, update_fabric=True)
        after = self.manager.get_simulation_time()
        if not isclose(after-before, DT, abs_tol=1e-6):
            raise CellIOError("CLOCK_ADVANCE_MISMATCH", f"Engine advanced {after-before} seconds")
        if render:
            self.rendering.render()
            if self.manager.get_simulation_time() != after:
                raise CellIOError("CLOCK_ADVANCE_MISMATCH", "Render advanced PhysX")
        self.step += 1
        self._cache = None
        return AdvanceReceipt(self.step-1, self.step, before, after, True, render)

    def close(self):
        if self.app is not None:
            try:
                self._release_stage()
            finally:
                self.app.close()
                self.app = None
