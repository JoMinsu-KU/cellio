"""Re-evaluate finalized attempts from their verified raw evidence."""

import json
from pathlib import Path

from cellio.artifacts import frames, verify_artifact
from cellio.core.models import json_value
from cellio.core.oracle import reduce_category, task_outcome
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
        if "requirements" in requirements:
            return rule_list(config, requirements["requirements"])
        name = requirements.get("preset", cell)
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


def read_attempt(artifact, requirements=None, *, on_frame=None):
    path = Path(artifact)
    verified = verify_artifact(path)
    config = read(path / "config.resolved.json")
    online = read(path / "requirements.json")
    if isinstance(online, dict):
        online = online.get("requirements", [])
    result = read(path / "result.json")
    identity = read(path / "manifest.start.json")
    rules = rule_list(config, requirements)
    oracle = scene_plugin(config["scene"]["cell_id"]).create_oracle(
        config["episode"]["horizon_steps"]
    )
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
    recorded_rules = rule_list(config, None)
    same_revision = digest(rules) == digest(recorded_rules)
    differences = (
        {
            key: {"online": expected.get(key), "offline": actual.get(key)}
            for key in sorted(expected.keys() | actual.keys())
            if expected.get(key) != actual.get(key)
        }
        if expected and same_revision
        else {}
    )
    comparable = bool(expected) and same_revision
    return {
        "schema_version": "cellio.evaluation/1.0",
        "source_artifact_ids": [verified["attempt_id"]],
        "run_id": verified["run_id"],
        "attempt_id": verified["attempt_id"],
        "source_digest": verified["artifact_digest"],
        "rules_digest": digest(rules),
        "end_step": last,
        "completion_status": verified["completion_status"],
        "requirements": json_value(final),
        "task_outcome": task_outcome(final, complete),
        "compliance_verdict": reduce_category(
            r for r, s in zip(final, rules) if s["category"] == "compliance"
        ),
        "case_verdict": result.get("case_verdict", "NOT_SCORED"),
        "online_comparable": comparable,
        "same_rule_revision": same_revision,
        "online_offline_equal": not differences if comparable else None,
        "differences": differences,
    }


def evaluate(artifact, requirements=None):
    evidence = read_attempt(artifact, requirements)
    path = Path(artifact).resolve()
    destination = (
        path.parent
        / "evaluation"
        / path.name
        / evidence["rules_digest"]
        / "evaluation.json"
    )
    write_json(destination, evidence)
    return {**evidence, "output": str(destination)}
