"""Generate JSON schemas used by the public CLI."""

import json
from pathlib import Path
from .schema import config_schema, obj, enum, integer
from cellio.io import atomic_write


def export_schemas(directory):
    directory = Path(directory)
    record_hash = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    fields = {
        "schema_version": {"const": "cellio.trace/1.0"},
        "record_index": integer(0),
        "record_type": enum(["episode_start", "tick", "final_snapshot", "episode_end"]),
        "run_id": {"type": "string"},
        "attempt_id": {"type": "string"},
        "episode_id": {"type": "string"},
        "generation": integer(0),
        "previous_hash": record_hash,
        "record_hash": record_hash,
        "step": {"type": ["integer", "null"], "minimum": 0},
    }
    trace = obj(fields, fields)
    trace["properties"].update(
        {
            k: {}
            for k in (
                "tick_kind",
                "truth",
                "previous_advance",
                "raw_signals",
                "observed_signals",
                "controller_called",
                "controller_view_hash",
                "controller_output",
                "intents",
                "deliveries",
                "actuation",
                "applied",
                "actuator_targets",
                "fault_audit",
                "events",
                "oracle_updates",
                "performance",
                "reused_snapshot",
                "reused_record_step",
                "completion_status",
                "end_step",
                "errors",
                "task_outcome",
            )
        }
    )
    trace["allOf"] = [
        {
            "if": {"properties": {"record_type": enum(["tick", "final_snapshot"])}},
            "then": {
                "required": [
                    "truth",
                    "raw_signals",
                    "observed_signals",
                    "controller_called",
                    "actuation",
                    "intents",
                    "deliveries",
                    "applied",
                    "actuator_targets",
                    "events",
                    "previous_advance",
                    "tick_kind",
                ],
                "properties": {
                    "truth": {"type": "object"},
                    "raw_signals": {"type": "object"},
                    "observed_signals": {"type": "object"},
                    "controller_called": {"type": "boolean"},
                    "intents": {"type": "array"},
                    "applied": {"type": "array"},
                    "deliveries": {"type": "array"},
                    "events": {"type": "array"},
                },
            },
        }
    ]
    required = {
        "run_id": {"type": "string"},
        "attempt_id": {"type": "string"},
        "artifact_digest": record_hash,
        "completion_status": enum(["COMPLETED", "CANCELLED", "INFRA_ERROR"]),
        "end_step": {"type": ["integer", "null"]},
        "task_outcome": enum(["SUCCESS", "FAILED", "CONTROLLED_ABORT", "UNKNOWN"]),
        "case_verdict": enum(["PASS", "FAIL", "UNKNOWN", "NOT_SCORED"]),
        "compliance_verdict": enum(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]),
    }
    result = {"type": "object", "properties": required, "required": list(required)}
    docs = {"config": config_schema(), "trace": trace, "result": result}
    for name, content in docs.items():
        out = {"$schema": "https://json-schema.org/draft/2020-12/schema", **content}
        atomic_write(
            directory / f"{name}.schema.json",
            (json.dumps(out, indent=2) + "\n").encode(),
        )
    return {"directory": str(directory), "schemas": list(docs)}
