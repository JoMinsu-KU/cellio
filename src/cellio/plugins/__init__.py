"""Resolve registered scene descriptors without importing Kit or accepting arbitrary import paths."""

import inspect
import os
import re
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from cellio.io import CellIOError, digest, file_hash

GROUP = "cellio.scene_plugins"
REFERENCE_SCENES = ("cell_a", "cell_b")
BUILTINS = (*REFERENCE_SCENES, "cell_c")
ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}")


def allowed_scenes():
    extra = [
        s.strip()
        for s in os.environ.get("CELLIO_SCENE_ALLOWLIST", "").split(",")
        if s.strip()
    ]
    if any(not ID.fullmatch(s) for s in extra):
        raise CellIOError("CFG_PLUGIN", "Invalid plugin allowlist entry")
    return tuple(dict.fromkeys((*BUILTINS, *extra)))


def validate_plugin(name, plugin):
    if getattr(plugin, "scene_id", None) != name or not re.fullmatch(
        r"\d+\.\d+\.\d+", str(getattr(plugin, "revision", ""))
    ):
        raise CellIOError("CFG_PLUGIN", "Scene descriptor identity/version mismatch")
    for method in (
        "build_config",
        "entity_registry",
        "create_plant",
        "signal_bindings",
        "create_mapper",
        "create_world_predicates",
        "create_controller",
        "controller_presets",
        "requirement_presets",
        "create_oracle",
        "scenario_presets",
        "initial_state_sampler",
        "evidence_manifest",
    ):
        if not callable(getattr(plugin, method, None)):
            raise CellIOError("CFG_PLUGIN", f"Missing scene contract: {method}")
    registry = plugin.entity_registry()
    if not isinstance(registry, dict):
        raise CellIOError("CFG_PLUGIN", "Invalid scene resource registry")
    for group in (
        "bodies",
        "joints",
        "signals",
        "actuators",
        "zones",
        "waypoints",
        "predicates",
    ):
        values = registry.get(group)
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or not ID.fullmatch(key) for key in values
        ):
            raise CellIOError("CFG_PLUGIN", f"Invalid resource registry {group}")
    if not registry["signals"] or any(
        t not in ("int", "bool", "float") for t in registry["signals"].values()
    ):
        raise CellIOError("CFG_PLUGIN", "Invalid public signal types")
    from cellio.core.models import CommandKind

    if any(
        not isinstance(kinds, (tuple, list))
        or not kinds
        or any(k not in CommandKind._value2member_map_ for k in kinds)
        for kinds in registry["actuators"].values()
    ):
        raise CellIOError("CFG_PLUGIN", "Invalid actuator command kinds")
    bindings = tuple(plugin.signal_bindings())
    if len({b.signal_id for b in bindings}) != len(bindings):
        raise CellIOError("CFG_PLUGIN", "Duplicate public signal ID")
    if {b.signal_id: b.dtype for b in bindings} != registry["signals"]:
        raise CellIOError("CFG_PLUGIN", "Mapper bindings disagree with descriptor")
    controllers = tuple(plugin.controller_presets())
    if (
        not controllers
        or not all(isinstance(i, str) and ID.fullmatch(i) for i in controllers)
        or len(set(controllers)) != len(controllers)
    ):
        raise CellIOError("CFG_PLUGIN", "No registered controller presets")
    return plugin


@lru_cache(maxsize=64)
def _resolve(name, allowlist):
    if name not in allowlist:
        raise CellIOError(
            "CFG_PLUGIN_UNKNOWN", f"Scene not in installed allowlist: {name}"
        )
    entries = metadata.entry_points(group=GROUP, name=name)
    if len(entries) > 1:
        raise CellIOError("CFG_PLUGIN", f"Multiple providers for {name}")
    if entries:
        plugin = next(iter(entries)).load()()
    elif name in REFERENCE_SCENES:
        # A source checkout and an editable/wheel install use the same factory.
        from .reference import reference_scene

        plugin = reference_scene(name)
    elif name == "cell_c":
        from cellio.inspection.framework import cell_c

        plugin = cell_c()
    else:
        raise CellIOError(
            "CFG_PLUGIN_UNKNOWN", f"Missing installed entry point: {name}"
        )
    return validate_plugin(name, plugin)


def scene_plugin(name):
    return _resolve(name, allowed_scenes())


def plugin_provenance(plugin):
    module_path = Path(inspect.getfile(type(plugin))).resolve()
    entry = next(iter(metadata.entry_points(group=GROUP, name=plugin.scene_id)), None)
    return {
        "scene_id": plugin.scene_id,
        "revision": plugin.revision,
        "provider": entry.dist.name if entry and entry.dist else "cellio-source",
        "provider_version": entry.dist.version if entry and entry.dist else None,
        "implementation_sha256": file_hash(module_path),
        "registry_sha256": digest(plugin.entity_registry()),
        "evidence": plugin.evidence_manifest(),
    }


def check_ports(plugin, plant):
    for name in ("load", "reset", "snapshot", "apply", "advance_one_step", "close"):
        if not callable(getattr(plant, name, None)):
            raise CellIOError("CFG_PLUGIN", f"{plugin.scene_id} plant lacks {name}")
