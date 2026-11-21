"""Re-evaluate finalized attempts from their verified raw evidence."""

import json
from pathlib import Path

from cellio.artifacts import frames, verify_artifact
from cellio.core.models import json_value
from cellio.core.oracle import reduce_category, task_outcome
from cellio.core.oracle.identity import (
    LOCK_FILE,
    SCHEMA,
    evaluator_identity,
    validate_rule_lock,
)
from cellio.io import digest, write_json
from cellio.plugins import scene_plugin


def read(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def rule_list(config, requirements):
    if isinstance(requirements, (str, Path)):
        path = Path(requirements)
        if path.suffix.lower() == ".json":
            requirements = read(path)
        else:
            import yaml

            requirements = yaml.safe_load(path.read_text(encoding="utf-8"))
    cell = config["scene"]["cell_id"]
    horizon = config["episode"]["horizon_steps"]
    if requirements is None:
        requirements = config.get("requirements", {"preset": cell})
    if isinstance(requirements, dict):
        if requirements.get("schema_version") == SCHEMA:
            validate_rule_lock(requirements, config)
            return tuple(requirements["rules"])
        if "requirements" in requirements:
            return rule_list(config, requirements["requirements"])
        if "preset" not in requirements:
            raise ValueError(
                "A requirement document must name a preset or contain an explicit rule array"
            )
        name = requirements["preset"]
        if requirements.get("parameter_overrides"):
            raise ValueError(
                "Protocol v1 has no registered canonical parameter overrides"
            )
        return scene_plugin(name).requirement_presets(horizon)
    if not isinstance(requirements, (list, tuple)):
        raise TypeError(
            "A requirement document must contain a named preset or rule array"
        )
    return tuple(requirements)


def read_attempt(artifact, requirements=None, *, on_frame=None, recorded_rules=False):
    path = Path(artifact)
    verified = verify_artifact(path)
    config = read(path / "config.resolved.json")
    online = read(path / "requirements.json")
    if isinstance(online, dict):
        online = online.get("requirements", [])
    result = read(path / "result.json")
    identity = read(path / "manifest.start.json")
    lock = read(path / LOCK_FILE) if "rule_lock_sha256" in verified else None
    if lock is not None and digest(lock) != verified["rule_lock_sha256"]:
        raise ValueError("Recorded rule specification changed during evaluation")
    if recorded_rules and requirements is not None:
        raise ValueError("Choose recorded rules or explicit requirements")
    if recorded_rules and lock is None:
        raise ValueError("This artifact has no sealed rule specification")
    rules = tuple(lock["rules"]) if recorded_rules else rule_list(config, requirements)
    plugin = scene_plugin(config["scene"]["cell_id"])
    oracle = plugin.create_oracle(config["episode"]["horizon_steps"])
    evaluator = evaluator_identity(plugin, oracle)
    source_evaluator = lock["evaluator"] if lock else None
    rules_digest = digest(rules)
    recorded_digest = lock["rules_sha256"] if lock else None
    same_rules = rules_digest == recorded_digest if lock else None
    same_engine = (
        evaluator == source_evaluator
        if source_evaluator and source_evaluator["complete"] and evaluator["complete"]
        else None
    )
    if False in (same_rules, same_engine):
        same_revision = False
    elif None in (same_rules, same_engine):
        same_revision = None
    else:
        same_revision = True
    oracle.reset(rules, identity["generation"])
    last = None
    for f in frames(path):
        if f["fault_audit"].get("reused_snapshot") and f["step"] == last:
            continue
        updates = oracle.observe(f)
        if on_frame:
            on_frame(f, updates)
        last = f["step"]
    complete = verified["completion_status"] == "COMPLETED"
    final = (
        oracle.finalize(last, complete)
        if last is not None
        else oracle.finalize(-1, False)
    )
    expected = {
        v["requirement_id"]: (v["verdict"], v.get("first_witness_step")) for v in online
    }
    actual = {v.requirement_id: (v.verdict, v.first_witness_step) for v in final}
    differences = (
        {
            key: {"online": expected.get(key), "offline": actual.get(key)}
            for key in sorted(expected.keys() | actual.keys())
            if expected.get(key) != actual.get(key)
        }
        if expected
        else {}
    )
    comparable = bool(expected) and same_revision is True
    evidence_summary = getattr(oracle, "evidence_summary", None)
    return {
        "schema_version": "cellio.evaluation/1.1",
        "source_artifact_ids": [verified["attempt_id"]],
        "run_id": verified["run_id"],
        "attempt_id": verified["attempt_id"],
        "source_digest": verified["artifact_digest"],
        "evaluation_mode": (
            "recorded_rules"
            if recorded_rules
            else "explicit_requirements"
            if requirements is not None
            else "installed_preset"
        ),
        "rules_digest": rules_digest,
        "recorded_rules_digest": recorded_digest,
        "evaluator_digest": digest(evaluator),
        "recorded_evaluator_digest": digest(source_evaluator)
        if source_evaluator
        else None,
        "end_step": last,
        "completion_status": verified["completion_status"],
        "completion_reason": result.get("reason"),
        "requirements": json_value(final),
        "task_outcome": getattr(plugin, "task_outcome", task_outcome)(final, complete),
        "compliance_verdict": reduce_category(
            r for r, s in zip(final, rules) if s["category"] == "compliance"
        ),
        "case_verdict": result.get("case_verdict", "NOT_SCORED"),
        "online_comparable": comparable,
        "same_rule_specification": same_rules,
        "same_oracle_implementation": same_engine,
        "rule_identity_status": {
            True: "matched",
            False: "different",
            None: "unavailable",
        }[same_revision],
        "same_rule_revision": same_revision,
        "observed_results_equal": not differences if expected else None,
        "online_offline_equal": not differences if comparable else None,
        "differences": differences,
        **(
            {"scene_evidence": json_value(evidence_summary())}
            if evidence_summary
            else {}
        ),
        **{k: verified[k] for k in ("source_kind", "coverage") if k in verified},
    }


def evaluate(artifact, requirements=None, *, recorded_rules=False):
    evidence = read_attempt(artifact, requirements, recorded_rules=recorded_rules)
    path = Path(artifact).resolve()
    analysis_id = digest(
        {
            key: evidence[key]
            for key in ("rules_digest", "evaluator_digest", "evaluation_mode")
        }
    )
    destination = (
        path.parent / "evaluation" / path.name / analysis_id / "evaluation.json"
    )
    write_json(destination, evidence)
    return {**evidence, "output": str(destination)}
