"""Single-owner PhysX plant. Kit imports and application startup are explicitly deferred."""

from __future__ import annotations

import gc
import json
from math import dist, isclose, isfinite
from pathlib import Path

from cellio.core.models import (
    ActuationReceipt, AdvanceReceipt, AppliedCommand, BodyState, CellIOError, CommandKind,
    JointState, TruthSnapshot,
)
from cellio.scenes import get_scene
from cellio.scenes.definition import ARM_NAMES, FINGER_NAMES, HOME
from .assets import CATALOG_PATH, LOCK, file_sha256, motion_files, verify_assets, verify_sdk_materials
from .application import release_application, start_application
from .geometry import compose, quat_distance, rotate, world_aabb

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
        self.gripper_slew_mps = self.profile.get("gripper_slew_mps", .022)
        self.gripper_open_slew_mps = self.profile.get("gripper_open_slew_mps", .040)
        if any(type(speed) not in (int, float) or not isfinite(speed) or speed <= 0
               for speed in (self.gripper_slew_mps, self.gripper_open_slew_mps)):
            raise CellIOError("ASSET_PHYSICS_PROFILE", "Finger slew must be finite and positive")
        self.hand_T_tcp = tuple(self.profile.get("hand_T_tcp", (0., 0., .1034, 1., 0., 0., 0.)))
        self.scene = self.app = self.builder = self.contact = self.motion = None
        self.views = {}
        self._stage_open = False
        self.generation = None
        self.provenance = {}
        self.geometry_manifest = {}
        self._video = None
        self._stage_epoch = 0
        self._previous_handles_invalidated = True

    def load(self, scene_config):
        scene_id = scene_config.get("scene_id", scene_config.get("id"))
        self.scene = get_scene(scene_id)
        variant = scene_config.get("variant_id")
        if variant in (None, "canonical"):
            self.belt_run_speed = .060
        elif self.scene.scene_id == "cell_a" and variant == "conveyor_speed_plus_10pct":
            self.belt_run_speed = .066
        else:
            raise CellIOError("CFG_SCENE_VARIANT", str(variant))
        if self.app is not None:
            return
        self.app = start_application({"headless": self.headless, "multi_gpu": False,
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
                verify_sdk_materials(lock)
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
        if self._stage_open:
            self.timeline.stop()
            self.app.update()  # Dispatch STOP before destroying PhysX tensor objects.
            self._previous_handles_invalidated = all(
                not view.is_physics_tensor_entity_valid() for view in (self.views or {}).values())
            self.motion = self.robot = self.views = self.builder = self.physics_scene = None
            self._cache = None
            gc.collect()
            self.context.close_stage()
            self._stage_open = False

    def reset(self, initial_state, generation):
        self._reset(initial_state, generation, HOME, (0., 0., .750))

    def _reset(self, initial_state, generation, home, robot_base):
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
        self._stage_epoch += 1
        self.generation, self.step, self._cache = generation, 0, None
        self._last_applied = None
        self.context.new_stage()
        self._stage_open = True
        stage = self.context.get_stage()
        self.manager.switch_physics_engine("physx")
        self.physics_scene = PhysxScene("/World/PhysicsScene")
        self.physics_scene.set_steps_per_second(120)
        self.physics_scene.set_solver_type("TGS")
        self.physics_scene.set_enabled_gpu_dynamics(False)
        self.physics_scene.set_gravity((0., 0., -9.81))
        from pxr import PhysxSchema
        scene_api = PhysxSchema.PhysxSceneAPI.Apply(stage.GetPrimAtPath("/World/PhysicsScene"))
        contact_last = self.profile.get("solve_articulation_contact_last", True)
        if type(contact_last) is not bool:
            raise CellIOError("ASSET_PHYSICS_PROFILE", "Expected Boolean articulation constraint ordering")
        # Resolve pad contacts after applying the articulation's force drive.
        # This scene-level flag is immutable once the native scene is created.
        order_attr = scene_api.CreateSolveArticulationContactLastAttr(contact_last)
        self.builder = StageBuilder(stage, self.scene, self.robot_asset, self.profile, robot_base=robot_base)
        self.builder.solver_settings["contact_last"] = order_attr.Get()
        if not self.headless:
            self.builder.show_operator_view()
        self.manager.setup_simulation(dt=DT, device="cpu")
        self.manager.enable_fabric(True)
        self.timeline.play()
        self.manager.initialize_physics()
        if (self.manager.get_physics_simulation_view() is None or str(self.manager.get_device()) != "cpu"
            or self.physics_scene.get_steps_per_second() != 120 or self.physics_scene.get_enabled_gpu_dynamics()
            or self.physics_scene.get_solver_type() != "TGS" or order_attr.Get() != contact_last):
            raise CellIOError("ENV_PHYSICS_MISMATCH", "PhysX CPU/TGS/120 Hz readback failed")
        self.robot = Articulation(ROBOT)
        self.names = list(self.robot.dof_names)
        if tuple(n for n in self.names if n in ARM_NAMES) != ARM_NAMES or not set(FINGER_NAMES) <= set(self.names):
            raise CellIOError("ASSET_BINDING_MISMATCH", f"Unexpected robot joints: {self.names!r}")
        if not self.robot.is_physics_tensor_entity_valid():
            raise CellIOError("SIM_STATE_INVALID", "Franka tensor view unavailable during commissioning readback")
        finger_indices = [self.names.index(n) for n in FINGER_NAMES]
        armatures = _floats(self.robot.get_dof_armatures(dof_indices=finger_indices))
        for name, value in zip(FINGER_NAMES, armatures, strict=True):
            if not isclose(self.builder.solver_settings["finger_armatures_kg"][name], value, abs_tol=1e-6):
                raise CellIOError("ASSET_PHYSICS_PROFILE", f"Live armature mismatch for {name}: {value}")
        driven_indices = [self.names.index(n) for n in self.builder.finger_drivers]
        kp, kd = self.robot.get_dof_gains(dof_indices=driven_indices)
        forces = _floats(self.robot.get_dof_max_efforts(dof_indices=driven_indices))
        for name, force, stiffness, damping in zip(self.builder.finger_drivers, forces, _floats(kp), _floats(kd), strict=True):
            expected = self.builder.finger_drive_settings[name]
            actual = {"force_cap_n": force, "stiffness_n_per_m": stiffness, "damping_n_s_per_m": damping}
            if any(not isclose(value, expected[key], rel_tol=1e-5) for key, value in actual.items()):
                raise CellIOError("ASSET_PHYSICS_PROFILE", f"Live drive mismatch for {name}: {actual}")
        live_fingers = {"armatures_kg": dict(zip(FINGER_NAMES, armatures, strict=True)),
                        "drive_effort_limits_n": dict(zip(self.builder.finger_drivers, forces, strict=True)),
                        "drive_stiffness_n_per_m": _floats(kp), "drive_damping_n_s_per_m": _floats(kd)}
        indices = self.robot.get_dof_indices(list(ARM_NAMES + FINGER_NAMES)).numpy().tolist()
        self.robot.set_dof_positions([[*home, .040, .040]], dof_indices=indices)
        self.robot.set_dof_velocities([[0.] * len(indices)], dof_indices=indices)
        driver_names = list(ARM_NAMES) + self.builder.finger_drivers
        drivers = self.robot.get_dof_indices(driver_names).numpy().tolist()
        self.robot.set_dof_position_targets([[*home, *([.040]*len(self.builder.finger_drivers))]], dof_indices=drivers)
        bodies = RigidPrim(list(self.builder.paths.values()))
        by_path = {path: entity for entity, path in self.builder.paths.items()}
        if len(bodies.paths) != len(by_path) or set(bodies.paths) != set(by_path):
            raise CellIOError("ASSET_BINDING_MISMATCH", f"Resolved rigid bodies: {bodies.paths}")
        self.body_order = tuple(by_path[path] for path in bodies.paths)
        self.views = {"bodies": bodies}
        masses = bodies.get_masses().numpy().reshape(-1).tolist()
        measured_masses = dict(zip(self.body_order, map(float, masses), strict=True))
        expected_masses = {"part_000": .05}
        if self.builder.clamp is not None:
            expected_masses["moving_jaw"] = .25
        for body, expected_mass in expected_masses.items():
            if not isclose(measured_masses[body], expected_mass, abs_tol=1e-6):
                raise CellIOError("ASSET_PHYSICS_PROFILE", f"Unexpected live mass for {body}: {measured_masses[body]}")
        part_index = [self.body_order.index("part_000")]
        pos, rotation = self.scene.initial_pose(initial_state)
        bodies.set_world_poses(positions=[pos], orientations=[rotation], indices=part_index)
        bodies.set_velocities(linear_velocities=[[0., 0., 0.]], angular_velocities=[[0., 0., 0.]], indices=part_index)
        initial_xyz, initial_wxyz = map(_floats, bodies.get_world_poses(indices=part_index))
        root_xyz, root_wxyz = map(_floats, self.robot.get_world_poses())
        root_matrix = self.builder.Geom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(ROBOT))
        root_scale = tuple(self.builder.Gf.Transform(root_matrix).GetScale())
        if (dist(initial_xyz, pos) > 1e-5 or quat_distance(initial_wxyz, rotation) > .001
                or dist(root_xyz, robot_base) > 1e-5 or quat_distance(root_wxyz, (1., 0., 0., 0.)) > .001
                or dist(root_scale, (1., 1., 1.)) > 1e-6):
            raise CellIOError("ASSET_TRANSFORM_MISMATCH", "Live reset poses or composed Franka base scale differ from the input")
        self.contact = ContactPairs(self.builder.collider_ids)
        self.motion = FrankaMotion(self.robot, self.builder, self.profile, self.hand_T_tcp)
        motion_dir = Path(self.motion.config.directory).resolve()
        motion_hashes = {path.relative_to(motion_dir).as_posix(): file_sha256(path) for path in motion_files(motion_dir)}
        if self.asset_root is not None:
            frozen = json.loads((Path(self.asset_root) / LOCK).read_text(encoding="utf-8"))["motion_config"]
            if motion_hashes != {name: record["sha256"] for name, record in frozen.items()}:
                raise CellIOError("ASSET_VERIFY_FAILED", "Installed cuMotion configuration differs from asset lock")
        self._goals = {"actuator.gripper": .080}
        if self.builder.belt is not None:
            self._goals["actuator.belt"] = False
        if self.builder.clamp is not None:
            self._goals.update({"actuator.clamp": 0., "actuator.process": False})
        self._slewed = {n: .040 for n in self.builder.finger_drivers}
        if self.builder.clamp is not None:
            self._slewed["clamp"] = 0.
        self._held_drivers = {}
        self._evidence, self._overrides = {}, set()
        self.belt_speed, self.process_steps = 0., 0
        for _ in range(240):
            self.manager.step(steps=1, update_fabric=True)
        self._offset = self.manager.get_simulation_time()
        if not self.headless:
            self.rendering.render()
            if self.manager.get_simulation_time() != self._offset:
                raise CellIOError("CLOCK_ADVANCE_MISMATCH", "Initial viewport render advanced PhysX")
        self.geometry_manifest = {"part_dimensions_m": (.04, .04, .04),
                                  "collider_local_corners": self.builder.local_corners,
                                  "pad_collider_paths": self.builder.pad_paths,
                                  "pad_collider_local_corners": self.builder.pad_corners,
                                  "composed_materials": self.builder.material_readbacks,
                                  "collider_id_by_path": self.builder.collider_ids,
                                  "hand_T_tcp": self.hand_T_tcp, "site_T_tcp": self.motion.site_T_tcp}
        asset_id = str(self.robot_asset)
        if self.asset_root is not None and Path(asset_id).is_absolute():
            asset_id = "cache://" + Path(asset_id).resolve().relative_to(Path(self.asset_root).resolve()).as_posix()
        self.provenance = {"device": str(self.manager.get_device()), "solver": self.physics_scene.get_solver_type(),
                           "physics_hz": self.physics_scene.get_steps_per_second(),
                           "gravity": list(self.physics_scene.get_gravity()), "episode_clock_offset_s": self._offset,
                           "robot_asset": asset_id, "tool_site": self.motion.site,
                           "motion_config_hashes": motion_hashes,
                           "finger_drive_settings": self.builder.finger_drive_settings,
                           "finger_tensor_readback": live_fingers,
                           "rigid_body_masses_kg": measured_masses,
                           "robot_root_before_warmup": {"xyz_m": root_xyz, "orientation_wxyz": root_wxyz,
                                                       "composed_scale": root_scale},
                           "part_before_warmup": {"xyz_m": initial_xyz, "orientation_wxyz": initial_wxyz},
                           "gripper_slew_mps": self.gripper_slew_mps,
                           "gripper_open_slew_mps": self.gripper_open_slew_mps,
                           "articulation_solver": self.builder.solver_settings,
                           "dof_names": self.names, "finger_drivers": list(self.builder.finger_drivers)}
        if self.builder.belt is not None:
            self.provenance["belt_run_speed_mps"] = self.belt_run_speed

    def _read_bodies(self):
        view = self.views["bodies"]
        if not view.is_physics_tensor_entity_valid() or not self.robot.is_physics_tensor_entity_valid():
            raise CellIOError("SIM_STATE_INVALID", "Rigid body or articulation tensor view became invalid")
        poses = view.get_world_poses()
        velocities = view.get_velocities()
        arrays = [a.numpy() for a in (*poses, *velocities)]
        return {entity: BodyState(entity, "world", *(tuple(map(float, values[index])) for values in arrays), True)
                for index, entity in enumerate(self.body_order)}

    def snapshot(self, tick):
        if self.generation != tick.generation or self.step != tick.step:
            raise CellIOError("CLOCK_ORDER", "Unexpected plant snapshot")
        if self._cache is not None:
            if self._cache.tick != tick:
                raise CellIOError("CLOCK_ORDER", "Tick identity changed")
            return self._cache
        bodies = self._read_bodies()
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
        if self.builder.clamp is not None:
            jaw = bodies["moving_jaw"]
            joints["clamp"] = JointState("clamp", "prismatic", jaw.position_m[0]-.590,
                                          jaw.linear_velocity_mps[0], None, True)
            meta["process.done"] = self.process_steps >= 120
            meta["process.elapsed_steps"] = self.process_steps
            meta["process.enabled"] = self._goals["actuator.process"]
        clear_of_fixture = True
        for entity, corners in self.builder.local_corners.items():
            body = bodies[entity]
            bounds = world_aabb((*body.position_m, *body.orientation_wxyz), corners)
            if self.builder.clamp is not None:
                overlap = all(low <= upper and lower <= high for low, high, lower, upper in
                              zip(bounds[0], bounds[1], (.39, .02, .79), (.66, .22, 1.00)))
                clear_of_fixture = clear_of_fixture and not overlap
            for suffix, point in zip(("min", "max"), bounds):
                for axis, coordinate in zip("xyz", point):
                    meta[f"{entity}.bounds.{suffix}_{axis}"] = coordinate
        if self.builder.clamp is not None:
            meta["robot.clear_fixture"] = clear_of_fixture
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

    def start_video(self, directory):
        if self.headless or self._video is not None:
            raise CellIOError("VIDEO_CAPTURE_FAILED", "Video requires an idle GUI recorder")
        from .video import ViewportVideo
        self._video = ViewportVideo(directory, self.rendering.render)

    def capture_video_frame(self, step):
        if self._video is None or step != self.step:
            raise CellIOError("VIDEO_CAPTURE_FAILED", "Unexpected capture boundary")
        before = self.manager.get_simulation_time()
        self._video.capture(step)
        if self.manager.get_simulation_time() != before:
            raise CellIOError("CLOCK_ADVANCE_MISMATCH", "Video capture advanced PhysX")

    def close_video(self):
        if self._video is None:
            return None
        recorder, self._video = self._video, None
        return recorder.close()

    def reset_evidence(self, truth):
        return {"new_generation": truth.tick.generation == self.generation and self.step == 0,
                "stage_is_current": self.builder.stage == self.context.get_stage() and self._stage_epoch > 0,
                "previous_handles_invalidated": self._previous_handles_invalidated,
                "fresh_handles": all(view.valid and view.is_physics_tensor_entity_valid() for view in self.views.values())
                                 and self.manager.get_physics_simulation_view() is not None,
                "motion_empty": self.motion.active is None and self.motion.completed == -1,
                "overrides_clear": not self._overrides and not self._held_drivers,
                "belt_off": self.builder.belt is None or truth.plant_state["belt.surface_speed_mps"] == 0.,
                "process_clear": self.process_steps == 0 and not self._goals.get("actuator.process", False)}

    def _receive(self, intent, truth, sim_time):
        target = intent.actuator_id
        if intent.kind == CommandKind.CANCEL_OPERATION:
            # Invalidate the old trajectory. Explicit HOLD/OFF intents below own
            # braking; cancellation alone must not create a successful HOLD receipt.
            self.motion.cancel()
            self._held_drivers.clear()
            previous = {n: self._slewed[n] for n in self.builder.finger_drivers}
            self._held_drivers["actuator.gripper"] = previous
            self._goals["actuator.gripper"] = 2 * sum(previous.values()) / len(previous)
            if self.builder.clamp is not None:
                self._goals["actuator.clamp"] = self._slewed["clamp"]
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
            self._slewed.update(self._held_drivers[target])
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
            speed = self.gripper_open_slew_mps if desired > self._slewed[n] else self.gripper_slew_mps
            nominal[n] = _slew(self._slewed[n], desired, speed * DT)
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

    def _clamp(self, truth, overrides):
        target = "actuator.clamp"
        coordinate = self._goals[target]
        override = overrides.get(target)
        if (target in self._overrides) != (override is not None):
            self._slewed["clamp"] = truth.joints["clamp"].position
        nominal = _slew(self._slewed["clamp"], coordinate, .040 * DT)
        value = nominal if override is None else override["driver_positions"]["clamp"]
        drive = self.builder.clamp
        if not drive.GetTargetPositionAttr().Set(value) or not drive.GetTargetVelocityAttr().Set(0.):
            raise CellIOError("BACKEND_TARGET_REJECTED", target)
        actual = float(drive.GetTargetPositionAttr().Get())
        if abs(actual - value) > 1e-6:
            raise CellIOError("BACKEND_TARGET_REJECTED", target)
        self._slewed["clamp"] = actual
        self._evidence[target] = {"nominal_coordinate": coordinate, "nominal_targets": {"clamp": nominal},
                                  "applied_targets": {"clamp": actual}}
        return actual

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
        if self.builder.clamp is not None:
            committed["actuator.clamp"] = self._clamp(truth, overrides)
            committed["actuator.process"] = self._goals["actuator.process"]
            if not self._goals["actuator.process"]:
                self.process_steps = 0
        if self.builder.belt is not None:
            self.belt_speed = _slew(self.belt_speed, self.belt_run_speed if self._goals["actuator.belt"] else 0., DT)
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
        if self._goals.get("actuator.process", False):
            self.process_steps = min(120, self.process_steps+1)
        self._cache = None
        return AdvanceReceipt(self.step-1, self.step, before, after, True, render)

    def close(self):
        if self.app is not None:
            try:
                if self._video is not None:
                    recorder, self._video = self._video, None
                    recorder.abort()
                self._release_stage()
            finally:
                release_application(self.app)
                self.app = None
