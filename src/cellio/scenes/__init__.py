"""Scene-owned reference geometry and waypoints, independent of the simulator."""

from .cell_a import SCENE as CELL_A
from .cell_b import SCENE as CELL_B


def get_scene(scene_id):
    if scene_id == CELL_A.scene_id:
        return CELL_A
    if scene_id == CELL_B.scene_id:
        return CELL_B
    raise ValueError(f"Unknown scene {scene_id!r}")
