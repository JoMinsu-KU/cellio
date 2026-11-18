"""Load and resolve untrusted scenario specifications before starting a worker."""

import copy
import math
from pathlib import Path, PureWindowsPath

import yaml
from jsonschema import Draft202012Validator
from jsonschema.validators import extend
from yaml.constructor import ConstructorError

from cellio.io import CellIOError
from cellio.core.faults.spec import compile_fault
from .schema import CTRL_DEFAULTS, config_schema


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in seen:
                raise ConstructorError(None, None, f"Duplicate or non-string key: {key}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def read_document(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Configuration exceeds 1 MiB")
        doc = yaml.load(data.decode("utf-8-sig"), Loader=UniqueLoader)
        # Limit alias expansion and reject cycles before validation or copying.
        stack = [(doc, 0)]
        count = 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if depth > 64 or count > 65536:
                raise ValueError("Recursive or oversized document")
            if isinstance(value, dict):
                stack.extend((v, depth+1) for v in value.values())
            elif isinstance(value, list):
                stack.extend((v, depth+1) for v in value)
            elif not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Only JSON-compatible scalars are allowed")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Non-finite number")
        return doc
    except (OSError, ValueError, TypeError, RecursionError, yaml.YAMLError) as exc:
        raise CellIOError("CFG_PARSE", str(exc), path=str(path)) from exc


_types = Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda _, v: type(v) is int)
_validator = extend(Draft202012Validator, type_checker=_types)(config_schema())


def resolve(doc, *, project_root=None):
    error = next(_validator.iter_errors(doc), None)
    if error:
        raise CellIOError("CFG_SCHEMA", error.message, path=error.json_path)
    cfg = copy.deepcopy(doc)
    cell = cfg["scene"]["cell_id"]
    defaults = {
        "scene": {"revision": 1, "variant_id": "canonical"},
        "clock": {"physics_hz": 120, "controller_divisor": 4},
        "controller": {"revision": 1, "parameters": {}},
        "episode": {"horizon_steps": 7200, "warmup_steps": 240, "reset_mode": "fresh_process"},
        "artifacts": {"output_root": "artifacts/runs", "trace_format": "jsonl_gzip", "video": False},
        "requirements": {"revision": 1, "parameter_overrides": {}},
    }
    for section, values in defaults.items():
        cfg[section] = {**values, **cfg.get(section, {})}
    cfg["controller"]["parameters"] = {**CTRL_DEFAULTS, **cfg["controller"]["parameters"]}
    cfg.setdefault("faults", [])
    cfg.setdefault("profile", "isaac62_lockstep")
    cfg.setdefault("expected", None)
    if cell != cfg["requirements"]["preset"]:
        raise CellIOError("CFG_REFERENCE", "Requirement preset must match the scene")
    output = cfg["artifacts"]["output_root"]
    winpath = PureWindowsPath(output)
    if winpath.anchor or ".." in winpath.parts or ":" in output or "\x00" in output:
        raise CellIOError("CFG_SEMANTIC", "Output path must stay within the artifact root")
    if project_root is not None:
        root = Path(project_root).resolve()
        if not (root / output).resolve().is_relative_to(root):
            raise CellIOError("CFG_SEMANTIC", "Output directory escapes root")
    for fault in cfg["faults"]:
        _resolve_fault(fault, cell, cfg["episode"]["horizon_steps"])
    expected = cfg["expected"] or {}
    if set(expected.get("fault_coverage", {})) - {f["fault_id"] for f in cfg["faults"]}:
        raise CellIOError("CFG_REFERENCE", "Unknown expected fault")
    if expected.get("requirements"):
        from cellio.core.oracle import preset
        registered = {r["id"] for r in preset(cell,cfg["episode"]["horizon_steps"])}
        if set(expected["requirements"])-registered:
            raise CellIOError("CFG_REFERENCE", "Unknown expected requirement")
    return cfg


def _resolve_fault(fault, cell, horizon):
    family = fault["family"]
    if family == "F6":
        fault["parameters"] = {"hold_tolerance_m": 0.0005, "conflicting_travel_min_m": 0.010,
                               **fault["parameters"]}
    fault.setdefault("require_exercised", True)
    compile_fault(fault, horizon, cell)
    lower, upper = fault["arm"]["open_step"], fault["arm"]["close_step"]
    if not lower < upper <= horizon:
        raise CellIOError("CFG_SEMANTIC", "Invalid arm window")
    trigger = fault["trigger"]
    if trigger["kind"] == "at_step" and not lower <= trigger["at_step"] < upper:
        raise CellIOError("CFG_SEMANTIC", "Scheduled trigger is outside its arm window")
    if trigger["kind"] == "controller_event":
        if trigger["event_type"] != "controller.stage_enter" or set(trigger["event_filter"]) - {"stage_id"}:
            raise CellIOError("CFG_REFERENCE", "Unknown controller event or filter")


def load(path, *, project_root=None):
    return resolve(read_document(path), project_root=project_root)
