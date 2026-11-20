"""The versioned configuration schema for installed scene descriptors."""

from cellio.core.faults.spec import MODEL_IDS
from cellio.plugins import BUILTINS, scene_plugin

ID = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,95}$"}
CELLS = BUILTINS
MODELS = MODEL_IDS
CONTROLLERS = ["reference", *(f"mutant.c_m{i}" for i in range(1, 7))]
CTRL_DEFAULTS = {
    "freshness_limit_steps": 12,
    "wait_timeout_steps": 360,
    "motion_timeout_steps": 960,
    "grip_timeout_steps": 240,
    "terminal_mode": "hold",
}


def obj(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def integer(minimum, maximum=None):
    result = {"type": "integer", "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


def enum(values):
    return {"type": "string", "enum": list(values)}


def dynamic_map(values):
    return {
        "type": "object",
        "patternProperties": {ID["pattern"]: values},
        "additionalProperties": False,
    }


def config_schema(scene_ids=None, controller_ids=None, *, scene_parameters=None):
    if scene_ids is None:
        return _builtin_schema()
    if controller_ids is None:
        controller_ids = CONTROLLERS
    const = lambda value: {"const": value}
    triggers = [
        obj({"kind": const("at_step"), "at_step": integer(0)}, ["kind", "at_step"]),
        obj(
            {
                "kind": const("controller_event"),
                "event_type": ID,
                "event_filter": dynamic_map(
                    {"type": ["string", "boolean", "integer", "number"]}
                ),
            },
            ["kind", "event_type", "event_filter"],
        ),
        obj(
            {
                "kind": enum(["world_level", "world_rising"]),
                "predicate_id": ID,
                "fire_on_initial": {"type": "boolean"},
            },
            ["kind", "predicate_id"],
        ),
    ]
    delay = obj({"delay_steps": integer(1, 120)}, ["delay_steps"])
    parameters = {
        "F1": obj({"value": {"type": "boolean"}}, ["value"]),
        "F2": obj({}),
        "F3": delay,
        "F4": delay,
        "F5": obj({}),
        "F6": obj(
            {
                "hold_tolerance_m": const(0.0005),
                "conflicting_travel_min_m": const(0.010),
            }
        ),
    }
    fault_forms = []
    for family, model in MODELS.items():
        fields = {
            "fault_id": ID,
            "family": const(family),
            "model_id": const(model),
            "target_id": ID,
            "trigger": {"oneOf": triggers},
            "arm": obj(
                {"open_step": integer(0), "close_step": integer(1)},
                ["open_step", "close_step"],
            ),
            "offset_steps": integer(0),
            "duration_steps": integer(1, 1200),
            "parameters": parameters[family],
        }
        fault_forms.append(
            obj({**fields, "require_exercised": {"type": "boolean"}}, fields)
        )
    params = {k: integer(1, 7200) for k in CTRL_DEFAULTS if k.endswith("_steps")}
    expected = obj(
        {
            "completion_status": enum(["COMPLETED", "INFRA_ERROR", "CANCELLED"]),
            "task_outcome": enum(["SUCCESS", "CONTROLLED_ABORT", "FAILED", "UNKNOWN"]),
            "requirements": dynamic_map(
                enum(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"])
            ),
            "fault_coverage": dynamic_map(
                enum(["EXERCISED", "NOT_EXERCISED", "CENSORED", "INJECTOR_ERROR"])
            ),
        }
    )
    expected["minProperties"] = 1
    scene = obj(
        {
            "cell_id": enum(scene_ids),
            "revision": const(1),
            "variant_id": enum(["canonical", "conveyor_speed_plus_10pct"]),
        },
        ["cell_id"],
    )
    if scene_parameters is not None:
        scene["properties"]["parameters"] = scene_parameters
    scene["allOf"] = [
        {
            "if": {
                "properties": {"variant_id": const("conveyor_speed_plus_10pct")},
                "required": ["variant_id"],
            },
            "then": {"properties": {"cell_id": const("cell_a")}},
        }
    ]
    result = obj(
        {
            "schema_version": const("cellio.config/1.0"),
            "scenario_id": ID,
            "profile": enum(["isaac62_lockstep", "mock_lockstep"]),
            "scene": scene,
            "clock": obj({"physics_hz": const(120), "controller_divisor": const(4)}),
            "controller": obj(
                {
                    "id": enum(controller_ids),
                    "revision": const(1),
                    "parameters": obj({**params, "terminal_mode": const("hold")}),
                },
                ["id"],
            ),
            "seeds": obj(
                {k: integer(0, 2**32 - 1) for k in ["scene", "controller", "fault"]},
                ["scene", "controller", "fault"],
            ),
            "episode": obj(
                {
                    "horizon_steps": integer(1),
                    "warmup_steps": integer(0),
                    "reset_mode": enum(["fresh_process", "hard_reload"]),
                }
            ),
            "faults": {"type": "array", "maxItems": 1, "items": {"oneOf": fault_forms}},
            "requirements": obj(
                {
                    "preset": enum(scene_ids),
                    "revision": const(1),
                    "parameter_overrides": obj({}),
                },
                ["preset"],
            ),
            "artifacts": obj(
                {
                    "output_root": {"type": "string", "minLength": 1},
                    "trace_format": const("jsonl_gzip"),
                    "video": {"type": "boolean"},
                }
            ),
            "expected": {"anyOf": [{"type": "null"}, expected]},
        },
        [
            "schema_version",
            "scenario_id",
            "scene",
            "controller",
            "seeds",
            "requirements",
        ],
    )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **result}


def _builtin_schema():
    # Package consumers can validate any built-in scene using one exported schema.
    # Bind controller IDs and scene-specific parameters to the selected descriptor.
    controllers = {}
    parameters = {}
    for name in CELLS:
        plugin = scene_plugin(name)
        controllers[name] = tuple(plugin.controller_presets())
        schema = getattr(plugin, "scene_parameters_schema", None)
        if schema is not None:
            parameters[name] = schema()
    all_ids = dict.fromkeys(i for ids in controllers.values() for i in ids)
    result = config_schema(
        CELLS, all_ids, scene_parameters={"type": "object"} if parameters else None
    )
    result["allOf"] = [
        {
            "if": {
                "properties": {"scene": {"properties": {"cell_id": {"const": name}}}}
            },
            "then": {
                "properties": {
                    "controller": {"properties": {"id": enum(ids)}},
                    "scene": {
                        "properties": {"parameters": parameters.get(name, False)}
                    },
                    "requirements": {"properties": {"preset": {"const": name}}},
                }
            },
        }
        for name, ids in controllers.items()
    ]
    return result
