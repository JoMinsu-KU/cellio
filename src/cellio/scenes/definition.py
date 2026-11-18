"""Metric, world-frame definitions shared by stage generation and commissioning."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import cos, sin

Vec3 = tuple[float, float, float]
DOWNWARD = (0.0, 0.0, 1.0, 0.0)
HOME = (0.0, -0.785398163, 0.0, -2.356194490, 0.0, 1.570796327, 0.785398163)
ARM_NAMES = tuple(f"panda_joint{i}" for i in range(1, 8))
FINGER_NAMES = ("panda_finger_joint1", "panda_finger_joint2")
TABLE = ((0.35, 0.0, 0.725), (1.20, 1.10, 0.05))
PART_SIZE_M = (0.040, 0.040, 0.040)


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
    placement_support: str | None = None

    def initial_pose(self, state):
        x, y, z = self.part_center
        theta = float(state.get("yaw_rad", 0.0))
        position = (
            x + float(state.get("x_offset_m", 0.0)),
            y + float(state.get("y_offset_m", 0.0)),
            z,
        )
        return position, (cos(theta / 2), 0.0, 0.0, sin(theta / 2))

    def placement_geometry(self, state):
        """Signed separating-axis gaps for the yawed cube and each solid scene box."""
        center, _ = self.initial_pose(state)
        angle = state["yaw_rad"]
        c, s = cos(angle), sin(angle)
        part_half = PART_SIZE_M[0] / 2
        footprint = part_half * (abs(c) + abs(s))
        gaps = {}
        if self.placement_support not in {b.name for b in self.boxes}:
            raise ValueError(f"Unknown placement support in {self.scene_id}")
        for box in (*self.boxes, Box("Table", *TABLE)):
            dx, dy, dz = (p - q for p, q in zip(center, box.center, strict=True))
            hx, hy, hz = (v / 2 for v in box.size)
            # The 3-D SAT reduces to four horizontal axes and shared vertical Z.
            gaps[box.name] = max(
                abs(dx) - hx - footprint,
                abs(dy) - hy - footprint,
                abs(c * dx + s * dy) - part_half - abs(c) * hx - abs(s) * hy,
                abs(-s * dx + c * dy) - part_half - abs(s) * hx - abs(c) * hy,
                abs(dz) - hz - part_half,
            )
            if box.name == self.placement_support:
                support_margin = min(hx - abs(dx), hy - abs(dy)) - footprint
                support_gap = dz - hz - part_half
        colliding = sorted(name for name, gap in gaps.items() if gap < 0)
        return {
            "accepted": not colliding and support_margin >= 0 and support_gap >= 0,
            "support": self.placement_support,
            "support_xy_margin_m": support_margin,
            "support_vertical_gap_m": support_gap,
            "separating_axis_gaps_m": gaps,
            "overlapping_solids": colliding,
        }
