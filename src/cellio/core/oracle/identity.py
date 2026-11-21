"""Identify the rule inputs and evaluator sources used for a particular attempt."""

import hashlib
import importlib
import inspect
import re
from pathlib import Path

from cellio.io import digest, json_value

LOCK_FILE = "rules.resolved.json"
SCHEMA = "cellio.rule-lock/1"
SHA256 = re.compile(r"[0-9a-f]{64}")


def evaluator_identity(plugin, oracle):
    """Only the installed plugin declares source modules. Never import names from an artifact."""
    if oracle is None:
        return None
    cls = type(oracle)
    declare = getattr(plugin, "oracle_source_modules", None)
    sources = {}
    complete = declare is not None
    modules = {
        type(plugin).__module__,
        cls.__module__,
        "cellio.core.models",
        "cellio.core.oracle",
        "cellio.core.oracle.engine",
        "cellio.io",
    }
    if declare is not None:
        modules.update(declare())
    for name in sorted(modules):
        module = importlib.import_module(name)
        try:
            source = inspect.getsourcefile(module)
            if source is None:
                complete = False
                continue
            # A Git checkout and its installed wheel can use different line endings.
            with Path(source).open(encoding="utf-8", newline=None) as stream:
                sources[name] = hashlib.sha256(
                    stream.read().encode("utf-8")
                ).hexdigest()
        except (OSError, TypeError, UnicodeError):
            complete = False
    return {
        "class": f"{cls.__module__}.{cls.__qualname__}",
        "complete": complete,
        "source_sha256": sources,
    }


def rule_lock(plugin, horizon, rules, oracle):
    return {
        "schema_version": SCHEMA,
        "scene_id": plugin.scene_id,
        "horizon_steps": horizon,
        "rules": json_value(rules),
        "rules_sha256": digest(rules),
        "evaluator": evaluator_identity(plugin, oracle),
    }


def validate_rule_lock(lock, config):
    rules = lock["rules"]
    if (
        lock["schema_version"] != SCHEMA
        or lock["scene_id"] != config["scene"]["cell_id"]
        or lock["horizon_steps"] != config["episode"]["horizon_steps"]
        or not isinstance(rules, list)
        or any(
            not isinstance(rule, dict) or not isinstance(rule.get("id"), str)
            for rule in rules
        )
        or len({rule["id"] for rule in rules}) != len(rules)
        or lock["rules_sha256"] != digest(rules)
    ):
        raise ValueError("Invalid recorded rule specification or digest")
    engine = lock["evaluator"]
    if engine is not None and not (
        isinstance(engine, dict)
        and isinstance(engine.get("class"), str)
        and type(engine.get("complete")) is bool
        and isinstance(engine.get("source_sha256"), dict)
        and (engine["source_sha256"] or not engine["complete"])
        and all(
            isinstance(v, str) and SHA256.fullmatch(v)
            for v in engine["source_sha256"].values()
        )
    ):
        raise ValueError("Invalid recorded evaluator identity")
