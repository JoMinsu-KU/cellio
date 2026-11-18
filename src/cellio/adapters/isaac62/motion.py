"""Official cuMotion RMPflow integration. All arrays are copied at the port boundary."""

from math import dist
from cellio.core.models import CellIOError
from cellio.scenes.definition import ARM_NAMES
from .geometry import compose, inverse, interpolate_target, quat_distance


class FrankaMotion:
    def __init__(self, articulation, builder, profile, hand_T_tcp):
        import numpy as np
        from isaacsim.core.experimental.objects import Cube
        from isaacsim.robot_motion import cumotion
        from isaacsim.robot_motion.experimental import motion_generation as mg

        self.np, self.mg, self.robot = np, mg, articulation
        self.names = list(articulation.dof_names)
        self.arm_indices = tuple(self.names.index(n) for n in ARM_NAMES)
        self.config = cumotion.load_cumotion_supported_robot("franka")
        self.sites = list(self.config.robot_description.tool_frame_names())
        self.site = profile.get("tool_frame") or self.sites[0]
        if self.site not in self.sites:
            raise CellIOError("ASSET_TOOL_FRAME", f"{self.site!r} not in {self.sites}")
        self.site_T_tcp = self._site_to_tcp(builder, profile, hand_T_tcp)
        strategy = mg.ObstacleStrategy()
        strategy.set_default_configuration(Cube, mg.ObstacleConfiguration("cube", 0.))
        self.world = mg.WorldBinding(
            world_interface=cumotion.CumotionWorldInterface(device="cpu"),
            obstacle_strategy=strategy, tracked_prims=["/World/Ground", "/World/Cell/Table/Geometry"],
            tracked_collision_api=mg.TrackableApi.PHYSICS_COLLISION)
        self.world.initialize()
        self.controller = cumotion.RmpFlowController(
            cumotion_robot=self.config, cumotion_world_interface=self.world.get_world_interface(),
            robot_joint_space=self.names, robot_site_space=self.sites, tool_frame=self.site)
        self.goal = self.setpoint = self.active = None
        self.reached = self.failed = False
        self.first_in_range = None
        self.completed = -1

    def _site_to_tcp(self, builder, profile, hand_T_tcp):
        if "site_T_tcp" in profile:
            return tuple(profile["site_T_tcp"])
        stage = builder.stage
        hand = stage.GetPrimAtPath(builder.paths["hand"])
        if self.site == hand.GetName():
            return hand_T_tcp
        sites = [p for p in builder.Usd.PrimRange(hand) if p.GetName() == self.site]
        if len(sites) != 1:
            raise CellIOError("ASSET_TOOL_FRAME", f"Calibrate site_T_tcp for {self.site!r}")
        cache = builder.Geom.XformCache(builder.Usd.TimeCode.Default())
        matrix, _ = cache.ComputeRelativeTransform(sites[0], hand)
        rot = matrix.ExtractRotationQuat()
        hand_T_site = (*matrix.ExtractTranslation(), rot.GetReal(), *rot.GetImaginary())
        return compose(inverse(hand_T_site), hand_T_tcp)

    def _state(self):
        joints = self.mg.JointState.from_name(
            robot_joint_space=self.names,
            positions=(self.names, self.robot.get_dof_positions()),
            velocities=(self.names, self.robot.get_dof_velocities()))
        return self.mg.RobotState(joints=joints)

    def _target(self):
        site = compose(self.setpoint, inverse(self.site_T_tcp))
        # The underlying buffer shape matches a single robot with one indexed site.
        xyz = self.np.array([site[:3]], dtype=self.np.float32)
        wxyz = self.np.array([site[3:]], dtype=self.np.float32)
        return self.mg.RobotState(sites=self.mg.SpatialState.from_name(
            spatial_space=self.sites, positions=([self.site], xyz), orientations=([self.site], wxyz)))

    def start(self, intent, tcp, sim_time, step):
        self.active, self.goal, self.setpoint = intent, tuple(intent.value), tcp
        self.first_in_range = None
        self.reached = self.failed = False
        self.applied_step = step
        self._sync()
        if not self.controller.reset(self._state(), self._target(), t=sim_time):
            raise CellIOError("MOTION_RESET_FAILED", intent.command_id)

    def observe(self, tcp, step):
        if self.active is None:
            return
        within = dist(tcp[:3], self.goal[:3]) <= .004 and quat_distance(tcp[3:], self.goal[3:]) <= .05
        if within:
            if self.first_in_range is None:
                self.first_in_range = step
            self.reached = step - self.first_in_range >= 12
        else:
            self.first_in_range, self.reached = None, False
        if self.reached:
            self.completed = self.active.sequence
        if self.completed != self.active.sequence and step - self.applied_step >= 960:
            self.failed = True

    def _sync(self):
        self.world.get_world_interface().update_world_to_robot_root_transforms(self.robot.get_world_poses())
        self.world.synchronize_transforms()

    def servo(self, sim_time, carrying):
        if self.active is None:
            return
        self._sync()
        remaining = self.setpoint[2] - self.goal[2]
        speed = .04 if 0. < remaining <= .050 else (.10 if carrying else .15)
        self.setpoint = interpolate_target(self.setpoint, self.goal, speed, 1./120)
        command = self.controller.forward(self._state(), self._target(), sim_time)
        if command is None or command.joints.positions is None:
            raise CellIOError("MOTION_COMMAND_MISSING", self.active.command_id)
        def array(v):
            return self.np.asarray(v.numpy() if hasattr(v, "numpy") else v).ravel().copy()
        indices = array(command.joints.position_indices)
        positions = array(command.joints.positions)
        by_index = dict(zip(indices.tolist(), positions.tolist(), strict=True))
        if any(i not in by_index for i in self.arm_indices):
            raise CellIOError("MOTION_COMMAND_MISSING", "RMP result is missing one or more arm DOFs")
        self.robot.set_dof_position_targets([[by_index[i] for i in self.arm_indices]], dof_indices=list(self.arm_indices))

    def hold(self):
        self.active = self.goal = self.setpoint = None
        self.first_in_range, self.reached, self.failed = None, False, False
        indices = list(self.arm_indices)
        measured = self.robot.get_dof_positions(dof_indices=indices).numpy().copy()
        self.robot.set_dof_position_targets(measured, dof_indices=indices)
        self.robot.set_dof_velocity_targets(self.np.zeros_like(measured), dof_indices=indices)
