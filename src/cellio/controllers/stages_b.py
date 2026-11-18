"""Fixture-cell public control recipe on the same state-machine engine."""

from .stages import Stage

PRIOR_POSE = ("robot.at_waypoint", "robot.completed_command_id")
CARRY = ("gripper.closed", *PRIOR_POSE)
CELL_B = (
    Stage(
        "initialize",
        ("park",),
        (("gripper", 0.080), ("clamp", 0.0), ("process", False)),
        ("gripper.open", "clamp.open"),
        1200,
    ),
    Stage("source_approach", ("source_hover",)),
    Stage("source_descend", ("source_grasp",)),
    Stage(
        "source_grasp",
        actions=(("gripper", 0.0),),
        signals=("gripper.closed",),
        timeout=240,
        admission=PRIOR_POSE,
    ),
    Stage("source_lift", ("source_hover",), admission=("gripper.closed",)),
    Stage("fixture_approach", ("fixture_load_hover",), admission=CARRY),
    Stage("fixture_place", ("fixture_load_place",), admission=CARRY),
    Stage(
        "fixture_release",
        actions=(("gripper", 0.080),),
        signals=("gripper.open",),
        timeout=240,
        admission=PRIOR_POSE,
    ),
    Stage(
        "fixture_retreat",
        ("fixture_load_hover", "park"),
        signals=("robot.clear_fixture",),
    ),
    Stage(
        "clamp_close",
        actions=(("clamp", -0.065),),
        admission=("fixture.present", "robot.clear_fixture"),
    ),
    Stage("wait_clamp", signals=("clamp.closed", "fixture.present"), timeout=360),
    Stage(
        "process",
        actions=(("process", True),),
        signals=("process.done",),
        timeout=240,
        admission=("fixture.present", "clamp.closed", "robot.clear_fixture"),
    ),
    Stage("clamp_open", actions=(("process", False), ("clamp", 0.0))),
    Stage("wait_open", signals=("clamp.open",), timeout=360),
    Stage("unload_approach", ("fixture_unload_hover",), admission=("clamp.open",)),
    Stage("unload_descend", ("fixture_unload_grasp",)),
    Stage(
        "unload_grasp",
        actions=(("gripper", 0.0),),
        signals=("gripper.closed",),
        timeout=240,
        admission=PRIOR_POSE,
    ),
    Stage("unload_lift", ("fixture_unload_hover",), admission=("gripper.closed",)),
    Stage("output_approach", ("output_hover",), admission=CARRY),
    Stage("output_place", ("output_place",), admission=CARRY),
    Stage(
        "output_release",
        actions=(("gripper", 0.080),),
        signals=("gripper.open",),
        timeout=240,
        admission=PRIOR_POSE,
    ),
    Stage("output_retreat", ("output_hover", "park")),
    Stage(
        "verify_output",
        signals=("destination.present", "gripper.open"),
        timeout=360,
        dwell=60,
    ),
)
B_POINTS = {
    "park": (0.30, 0.0, 1.10),
    "source_hover": (0.45, -0.24, 1.030),
    "source_grasp": (0.45, -0.24, 0.815),
    "fixture_load_hover": (0.507, 0.12, 1.030),
    "fixture_load_place": (0.507, 0.12, 0.815),
    "fixture_unload_hover": (0.500, 0.12, 1.030),
    "fixture_unload_grasp": (0.500, 0.12, 0.815),
    "output_hover": (0.35, 0.30, 1.030),
    "output_place": (0.35, 0.30, 0.815),
}
