"""The versioned configuration schema and the two built-in hardware registries."""

ID = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,95}$"}
CELLS = ("cell_a", "cell_b")
MODELS = {
    "F1": "boolean_stuck_at_v1", "F2": "frozen_sample_with_age_v1",
    "F3": "fifo_delay_flush_v1", "F4": "fixed_delay_flush_resume_v1",
    "F5": "explicit_invalid_v1", "F6": "position_target_hold_v1",
}
CONTROLLERS = ["reference", *(f"mutant.c_m{i}" for i in range(1, 7))]
COMMON_SIGNALS = {
    "robot.at_waypoint", "robot.motion_failed", "robot.completed_command_id",
    "gripper.open", "gripper.closed", "gripper.gap_m", "destination.present",
}
SIGNALS = {
    "cell_a": COMMON_SIGNALS | {"pickup.present", "belt.stopped"},
    "cell_b": COMMON_SIGNALS | {"fixture.present", "source.present", "clamp.open",
                                "clamp.closed", "robot.clear_fixture", "process.done"},
}
ACTUATORS = {
    "cell_a": {"actuator.robot", "actuator.gripper", "actuator.belt"},
    "cell_b": {"actuator.robot", "actuator.gripper", "actuator.clamp", "actuator.process"},
}
CTRL_DEFAULTS = {"freshness_limit_steps": 12, "wait_timeout_steps": 360,
                 "motion_timeout_steps": 960, "grip_timeout_steps": 240, "terminal_mode": "hold"}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def integer(minimum, maximum=None):
    result = {"type": "integer", "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


def enum(values):
    return {"type": "string", "enum": list(values)}


def dynamic_map(values):
    return {"type": "object", "patternProperties": {ID["pattern"]: values},
            "additionalProperties": False}


def config_schema():
    const = lambda value: {"const": value}
    triggers = [
        obj({"kind": const("at_step"), "at_step": integer(0)}, ["kind", "at_step"]),
        obj({"kind": const("controller_event"), "event_type": ID,
             "event_filter": dynamic_map({"type": ["string", "boolean", "integer", "number"]})},
            ["kind", "event_type", "event_filter"]),
        obj({"kind": enum(["world_level", "world_rising"]), "predicate_id": ID,
             "fire_on_initial": {"type": "boolean"}}, ["kind", "predicate_id"]),
    ]
    delay = obj({"delay_steps": integer(1, 120)}, ["delay_steps"])
    parameters = {
        "F1": obj({"value": {"type": "boolean"}}, ["value"]),
        "F2": obj({}), "F3": delay, "F4": delay, "F5": obj({}),
        "F6": obj({"hold_tolerance_m": const(0.0005), "conflicting_travel_min_m": const(0.010)}),
    }
    fault_forms = []
    for family, model in MODELS.items():
        fields = {"fault_id": ID, "family": const(family), "model_id": const(model),
                  "target_id": ID, "trigger": {"oneOf": triggers},
                  "arm": obj({"open_step": integer(0), "close_step": integer(1)},
                             ["open_step", "close_step"]),
                  "offset_steps": integer(0), "duration_steps": integer(1, 1200),
                  "parameters": parameters[family]}
        fault_forms.append(obj({**fields, "require_exercised": {"type": "boolean"}}, fields))
    params = {k: integer(1, 7200) for k in CTRL_DEFAULTS if k.endswith("_steps")}
    expected = obj({
        "completion_status": enum(["COMPLETED", "INFRA_ERROR", "CANCELLED"]),
        "task_outcome": enum(["SUCCESS", "CONTROLLED_ABORT", "FAILED", "UNKNOWN"]),
        "requirements": dynamic_map(enum(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"])),
        "fault_coverage": dynamic_map(enum(["EXERCISED", "NOT_EXERCISED", "CENSORED", "INJECTOR_ERROR"])),
    })
    expected["minProperties"] = 1
    result = obj({
        "schema_version": const("cellio.config/1.0"), "scenario_id": ID,
        "profile": enum(["isaac62_lockstep", "mock_lockstep"]),
        "scene": obj({"cell_id": enum(CELLS), "revision": const(1),
                      "variant_id": const("canonical")}, ["cell_id"]),
        "clock": obj({"physics_hz": const(120), "controller_divisor": const(4)}),
        "controller": obj({"id": enum(CONTROLLERS), "revision": const(1),
                           "parameters": obj({**params, "terminal_mode": const("hold")})}, ["id"]),
        "seeds": obj({k: integer(0, 2**32-1) for k in ["scene", "controller", "fault"]},
                     ["scene", "controller", "fault"]),
        "episode": obj({"horizon_steps": integer(1), "warmup_steps": integer(0),
                        "reset_mode": enum(["fresh_process", "hard_reload"])}),
        "faults": {"type": "array", "maxItems": 1, "items": {"oneOf": fault_forms}},
        "requirements": obj({"preset": enum(CELLS), "revision": const(1),
                             "parameter_overrides": obj({})}, ["preset"]),
        "artifacts": obj({"output_root": {"type": "string", "minLength": 1},
                          "trace_format": const("jsonl_gzip"), "video": {"type": "boolean"}}),
        "expected": {"anyOf": [{"type": "null"}, expected]},
    }, ["schema_version", "scenario_id", "scene", "controller", "seeds", "requirements"])
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **result}
