"""Public control policy. Coordinates are CellTCP world goals in metres."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    # POSE entries are sequential. Other entry actions execute once on entry.
    poses: tuple = ()
    actions: tuple = ()
    signals: tuple = ()
    timeout: int = 960
    dwell: int = 0
    admission: tuple = ()


CELL_A = (
    Stage(
        "initialize",
        ("park",),
        (("gripper", 0.080), ("belt", False)),
        ("gripper.open",),
        1200,
    ),
    Stage(
        "wait_part",
        actions=(("belt", True),),
        signals=("pickup.present",),
        timeout=1200,
    ),
    Stage(
        "wait_stop",
        actions=(("belt", False),),
        signals=("belt.stopped",),
        timeout=360,
        dwell=24,
    ),
    Stage("approach", ("pickup_hover",), admission=("pickup.present", "belt.stopped")),
    Stage("descend", ("pickup_grasp",)),
    Stage(
        "grasp",
        actions=(("gripper", 0.0),),
        signals=("gripper.closed",),
        timeout=240,
        admission=("robot.at_waypoint", "robot.completed_command_id"),
    ),
    Stage("lift", ("pickup_hover",), admission=("gripper.closed",)),
    Stage(
        "transfer",
        ("transfer_mid", "output_hover"),
        admission=("gripper.closed", "robot.at_waypoint", "robot.completed_command_id"),
    ),
    Stage(
        "place",
        ("output_place",),
        admission=("gripper.closed", "robot.at_waypoint", "robot.completed_command_id"),
    ),
    Stage(
        "release",
        actions=(("gripper", 0.080),),
        signals=("gripper.open",),
        timeout=240,
        admission=("robot.at_waypoint", "robot.completed_command_id"),
    ),
    Stage("retreat", ("output_hover", "park")),
    Stage(
        "verify_output",
        signals=("destination.present", "gripper.open"),
        timeout=360,
        dwell=60,
    ),
)

A_POINTS = {
    "park": (0.30, 0.0, 1.10),
    "pickup_hover": (0.45, -0.116, 1.030),
    "pickup_grasp": (0.45, -0.116, 0.815),
    "transfer_mid": (0.45, 0.08, 1.030),
    "output_hover": (0.45, 0.25, 1.030),
    "output_place": (0.45, 0.25, 0.815),
}
