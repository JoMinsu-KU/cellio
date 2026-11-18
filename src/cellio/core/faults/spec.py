"""Compile the registered single-fault models without loading the simulator."""

import re
from collections.abc import Mapping
from math import isfinite

from cellio.plugins import scene_plugin

from ..models import CellIOError, FaultSpec

MODEL_IDS = {
    "F1": "boolean_stuck_at_v1",
    "F2": "frozen_sample_with_age_v1",
    "F3": "fifo_delay_flush_v1",
    "F4": "fixed_delay_flush_resume_v1",
    "F5": "explicit_invalid_v1",
    "F6": "position_target_hold_v1",
}


def _valid(condition, code, message):
    if not condition:
        raise CellIOError(code, message)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def compile_fault(
    spec, horizon, cell_id, *, signal_types=None, endpoints=None, predicates=None
):
    if isinstance(spec, Mapping):
        try:
            spec = FaultSpec(**spec)
        except (TypeError, ValueError) as exc:
            raise CellIOError("CFG_FAULT_SPEC", str(exc)) from exc
    _valid(
        isinstance(spec, FaultSpec),
        "CFG_FAULT_SPEC",
        "expected one registered specification",
    )
    family = spec.family
    _valid(
        family in MODEL_IDS and spec.model_id == MODEL_IDS[family],
        "CFG_FAULT_MODEL",
        spec.model_id,
    )
    _valid(
        isinstance(spec.fault_id, str)
        and re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", spec.fault_id),
        "CFG_FAULT_SPEC",
        "invalid fault_id",
    )
    _valid(
        type(spec.require_exercised) is bool,
        "CFG_FAULT_SPEC",
        "invalid require_exercised",
    )
    registry = scene_plugin(cell_id).entity_registry()
    if signal_types is None:
        signal_types = registry["signals"]
    if endpoints is None:
        endpoints = set(registry["actuators"]) - {"control.operation"}
    hold_targets = {
        name
        for name, kinds in registry["actuators"].items()
        if {"GRIPPER_GAP", "JOINT_POSITION"}.intersection(kinds)
    }
    target = spec.target_id
    if family in {"F1", "F2", "F3", "F5"}:
        _valid(
            target in signal_types
            and (family != "F1" or signal_types[target] == "bool"),
            "CFG_FAULT_TARGET",
            target,
        )
    else:
        _valid(
            target in endpoints and (family != "F6" or target in hold_targets),
            "CFG_FAULT_TARGET",
            target,
        )
    p = spec.parameters
    _valid(
        isinstance(p, Mapping), "CFG_FAULT_PARAMETER", "parameters must be a mapping"
    )
    expected = {
        "F1": {"value"},
        "F3": {"delay_steps"},
        "F4": {"delay_steps"},
        "F6": {"hold_tolerance_m", "conflicting_travel_min_m"},
    }.get(family, set())
    _valid(set(p) == expected, "CFG_FAULT_PARAMETER", f"expected {sorted(expected)}")
    if family == "F1":
        _valid(
            type(p["value"]) is bool,
            "CFG_FAULT_PARAMETER",
            "stuck value must be boolean",
        )
    elif family in {"F3", "F4"}:
        _valid(
            _integer(p["delay_steps"], 1, 120),
            "CFG_FAULT_PARAMETER",
            "delay_steps must be 1..120",
        )
    elif family == "F6":
        _valid(
            all(type(v) in (float, int) and isfinite(v) for v in p.values())
            and p["hold_tolerance_m"] == 0.0005
            and p["conflicting_travel_min_m"] == 0.010,
            "CFG_FAULT_PARAMETER",
            "expected registered F6 position thresholds",
        )
    a = spec.arm
    _valid(
        isinstance(a, Mapping)
        and set(a) == {"open_step", "close_step"}
        and _integer(a["open_step"], 0, horizon - 1)
        and _integer(a["close_step"], a["open_step"] + 1, horizon),
        "CFG_FAULT_TIME",
        "invalid arm window",
    )
    _valid(
        type(spec.offset_steps) is int
        and spec.offset_steps >= 0
        and _integer(spec.duration_steps, 1, 1200),
        "CFG_FAULT_TIME",
        "invalid offset or duration",
    )
    tr = spec.trigger
    _valid(isinstance(tr, Mapping), "CFG_FAULT_TRIGGER", "expected mapping")
    kind = tr.get("kind")
    if kind == "at_step":
        _valid(
            set(tr) == {"kind", "at_step"}
            and type(tr["at_step"]) is int
            and tr["at_step"] >= 0,
            "CFG_FAULT_TRIGGER",
            "invalid time trigger",
        )
    elif kind == "controller_event":
        _valid(
            set(tr) == {"kind", "event_type", "event_filter"}
            and isinstance(tr["event_type"], str)
            and tr["event_type"].startswith("controller.")
            and isinstance(tr["event_filter"], Mapping)
            and all(
                isinstance(k, str) and type(v) in (str, int, float, bool)
                for k, v in tr["event_filter"].items()
            ),
            "CFG_FAULT_TRIGGER",
            "invalid controller event trigger",
        )
        fields=registry.get("events",{}).get(tr["event_type"])
        _valid(fields is not None,"CFG_FAULT_TRIGGER",f"unregistered controller event {tr['event_type']}")
        types={"string":(str,),"integer":(int,),"boolean":(bool,),"number":(int,float)}
        for key,value in tr["event_filter"].items():
            declaration=fields.get(key,{})
            _valid(type(value) in types.get(declaration.get("type"),()) and
                   (type(value) is not float or isfinite(value)) and
                   ("enum" not in declaration or value in declaration["enum"]) and
                   ("minimum" not in declaration or value>=declaration["minimum"]),
                   "CFG_FAULT_TRIGGER",f"unregistered or invalid filter {tr['event_type']}.{key}")
    elif kind in {"world_level", "world_rising"}:
        if predicates is None:
            predicates = registry["predicates"]
        allowed = {"kind", "predicate_id"} | (
            {"fire_on_initial"} if kind == "world_rising" else set()
        )
        _valid(
            set(tr) <= allowed
            and tr.get("predicate_id") in predicates
            and type(tr.get("fire_on_initial", False)) is bool,
            "CFG_FAULT_TRIGGER",
            "invalid world predicate",
        )
    else:
        raise CellIOError("CFG_FAULT_TRIGGER", str(kind))
    return spec
