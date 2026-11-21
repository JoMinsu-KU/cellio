"""Distinguish the original rule identity from agreement on a particular trace."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_episode_worker import job
from test_trace_conformance import reseal

from cellio.analysis.replay import evaluate, read_attempt
from cellio.artifacts import verify_artifact
from cellio.artifacts.trace import iter_records
from cellio.cli.main import main
from cellio.core.oracle.identity import LOCK_FILE
from cellio.io import CellIOError, digest, write_json
from cellio.plugins import scene_plugin
from cellio.runtime.episode import run_episode


@pytest.fixture
def attempt(tmp_path):
    path = tmp_path / "attempt"
    assert run_episode(**job(path, horizon=36))["completion_status"] == "COMPLETED"
    return path


def test_sealed_rules_and_unchanged_results(attempt):
    lock = json.loads((attempt / LOCK_FILE).read_bytes())
    assert next(iter_records(attempt))["rule_lock_sha256"] == digest(lock)
    assert verify_artifact(attempt)["rule_lock_sha256"] == digest(lock)
    evidence = read_attempt(attempt)
    assert (
        evidence["rules_digest"]
        == evidence["recorded_rules_digest"]
        == digest(lock["rules"])
    )
    assert evidence["evaluator_digest"] == evidence["recorded_evaluator_digest"]
    assert evidence["same_rule_revision"] is True
    assert evidence["online_comparable"] and evidence["online_offline_equal"]
    assert evidence["observed_results_equal"] and evidence["differences"] == {}
    assert "cellio.core.oracle.temporal" in lock["evaluator"]["source_sha256"]


def test_preset_drift_with_identical_results_and_recorded_rule_reanalysis(
    attempt, monkeypatch
):
    plugin = scene_plugin("cell_a")
    changed = deepcopy(plugin.requirement_presets(36))
    r04 = next(rule for rule in changed if ".r04." in rule["id"])
    assert r04["window"]["upper_steps"] == 960
    r04["window"]["upper_steps"] = 961
    monkeypatch.setattr(plugin, "requirement_presets", lambda horizon: changed)
    current = evaluate(attempt)
    assert (
        current["same_rule_specification"] is False
        and current["same_oracle_implementation"]
    )
    assert (
        current["same_rule_revision"] is False
        and current["rule_identity_status"] == "different"
    )
    assert current["recorded_rules_digest"] != current["rules_digest"]
    assert current["observed_results_equal"] and current["differences"] == {}
    assert (
        current["online_comparable"] is False
        and current["online_offline_equal"] is None
    )
    original = evaluate(attempt, recorded_rules=True)
    assert (
        original["evaluation_mode"] == "recorded_rules"
        and original["online_offline_equal"]
    )
    assert original["rules_digest"] == original["recorded_rules_digest"]
    assert Path(current["output"]).is_file() and current["output"] != original["output"]
    assert read_attempt(attempt, attempt / LOCK_FILE)["online_offline_equal"] is True


def test_explicit_changed_rule_reverses_a_verdict(attempt, tmp_path, capsys):
    changed = deepcopy(scene_plugin("cell_a").requirement_presets(36))
    r01 = next(rule for rule in changed if ".r01." in rule["id"])
    r01["predicate"] = {"op": "not", "item": r01["predicate"]}
    custom = tmp_path / "custom.json"
    write_json(custom, changed)
    assert (
        main(["evaluate", "--artifact", str(attempt), "--requirements", str(custom)])
        == 0
    )
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["evaluation_mode"] == "explicit_requirements"
    assert (
        outcome["same_rule_revision"] is False
        and outcome["observed_results_equal"] is False
    )
    assert outcome["online_offline_equal"] is None and not outcome["online_comparable"]
    assert outcome["differences"][r01["id"]] == {
        "online": ["PASS", 36],
        "offline": ["FAIL", 0],
    }


def test_evaluator_dependency_change_and_missing_plugin_declaration(
    attempt, monkeypatch
):
    plugin = scene_plugin("cell_a")
    declared = plugin.oracle_source_modules()
    monkeypatch.setattr(
        plugin, "oracle_source_modules", lambda: (*declared, "cellio.core.clock")
    )
    different = read_attempt(attempt, recorded_rules=True)
    assert different["same_rule_specification"] and different["observed_results_equal"]
    assert (
        different["same_oracle_implementation"] is False
        and different["same_rule_revision"] is False
    )
    assert different["evaluator_digest"] != different["recorded_evaluator_digest"]
    assert different["online_offline_equal"] is None
    monkeypatch.setattr(plugin, "oracle_source_modules", None)
    unavailable = read_attempt(attempt, recorded_rules=True)
    assert (
        unavailable["same_rule_specification"] and unavailable["observed_results_equal"]
    )
    assert (
        unavailable["same_rule_revision"] is None
        and unavailable["rule_identity_status"] == "unavailable"
    )


def test_old_dense_attempt_reports_only_observed_agreement(capsys):
    path = Path(__file__).resolve().parents[2] / "examples/traces/mock-36"
    replay = read_attempt(path)
    assert (
        replay["observed_results_equal"]
        and replay["rule_identity_status"] == "unavailable"
    )
    assert (
        replay["recorded_rules_digest"] is None
        and replay["recorded_evaluator_digest"] is None
    )
    assert (
        replay["same_rule_revision"] is None and replay["online_offline_equal"] is None
    )
    assert not replay["online_comparable"]
    assert main(["evaluate", "--artifact", str(path), "--recorded-rules"]) != 0
    assert "no sealed rule specification" in capsys.readouterr().err


@pytest.mark.parametrize(
    "change", ["adjust_rule", "adjust_source", "remove_lock", "downgrade"]
)
def test_sealed_inventory_and_first_record_bind_rule_identity(attempt, change):
    lock = json.loads((attempt / LOCK_FILE).read_bytes())
    manifest_path = attempt / "manifest.final.json"
    manifest = json.loads(manifest_path.read_bytes())
    if change in {"adjust_rule", "adjust_source"}:
        if change == "adjust_rule":
            lock["rules"][0]["id"] += ".changed"
            lock["rules_sha256"] = digest(lock["rules"])
        else:
            lock["evaluator"]["class"] += ".changed"
        write_json(attempt / LOCK_FILE, lock)
        reseal(attempt, [LOCK_FILE])
    else:
        del manifest["files"][LOCK_FILE]
        if change == "downgrade":
            del manifest["schema_version"]
        write_json(manifest_path, manifest)
        reseal(attempt, [])
    with pytest.raises(CellIOError, match="rule lock|inventory"):
        verify_artifact(attempt)
