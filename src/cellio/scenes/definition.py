"""Metric, world-frame definitions shared by stage generation and commissioning."""

from dataclasses import dataclass
from math import cos, sin
from typing import Mapping

Vec3 = tuple[float, float, float]
DOWNWARD = (0., 0., 1., 0.)
HOME = (0., -.785398163, 0., -2.356194490, 0., 1.570796327, .785398163)
ARM_NAMES = tuple(f"panda_joint{i}" for i in range(1, 8))
FINGER_NAMES = ("panda_finger_joint1", "panda_finger_joint2")
TABLE = ((.35, 0., .725), (1.20, 1.10, .05))


@dataclass(frozen=True)
class Box:
    name: str
    center: Vec3
    size: Vec3
    kind: str = "static"
    mass: float | None = None


@dataclass(frozen=True)
class Scene:
    scene_id: str
    boxes: tuple[Box, ...]
    zones: tuple[Box, ...]
    part_center: Vec3
    waypoints: Mapping[str, Vec3]

    def initial_pose(self, state):
        x, y, z = self.part_center
        theta = float(state.get("yaw_rad", 0.))
        position = (x + float(state.get("x_offset_m", 0.)),
                    y + float(state.get("y_offset_m", 0.)), z)
        return position, (cos(theta / 2), 0., 0., sin(theta / 2))
