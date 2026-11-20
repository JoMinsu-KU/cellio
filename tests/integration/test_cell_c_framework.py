"""Exercise the shared tools with a registered Cell C scene and historical evidence."""

import json
import os
from importlib import resources
from pathlib import Path

import pytest

from cellio.artifacts import frames
from cellio.cli import main
from cellio.inspection.framework_oracle import DEST, OWNER
from cellio.io import file_hash
from cellio.plugins import scene_plugin


def cli(capsys, *args, code=0):
    actual = main(["--json", *map(str, args)])
    output = capsys.readouterr()
    assert actual == code, output
    return json.loads(output.out)


def import_sample(capsys, tmp_path, sample, edit=None):
    raw = resources.files("cellio.inspection").joinpath("examples", sample + ".json")
    original = json.loads(raw.read_bytes())
    if edit is not None:
        edit(original)
    source, output = tmp_path / "input.json", tmp_path / "attempt"
    source.write_text(json.dumps(original), encoding="utf-8")
    imported = cli(capsys, "inspection", "import", source, "--output", output)
    assert imported["source_kind"] == "portable_witness"
    return original, source, output


@pytest.mark.parametrize("sample", ["normal", "stale_ok"])
def test_archived_observations_use_common_verify_evaluate_at_original_ticks(
    capsys, tmp_path, sample
):
    original, _, artifact = import_sample(capsys, tmp_path, sample)
    verified = cli(capsys, "verify-artifact", "--artifact", artifact)
    assert verified["coverage"] == "recorded_events" and verified["records"] == 15
    assert [f["step"] for f in frames(artifact)] == [
        4 * e["frame"] for e in original["events"]
    ]
    evaluation = cli(capsys, "evaluate", "--artifact", artifact)
    assert evaluation["source_digest"] == verified["artifact_digest"]
    assert evaluation["online_offline_equal"] is None
    assert evaluation["source_kind"] == "portable_witness"
    expected = "PASS" if sample == "normal" else "FAIL"
    assert evaluation["compliance_verdict"] == expected
    rows = evaluation["scene_evidence"]["parts"]
    assert [p["part"] for p in rows] == ["P1", "P2"]
    assert [p["result_part_id"] for p in rows] == [
        p["result_part_id"] for p in original["parts"]
    ]
    assert rows[1]["observed_bin"] == ("NG" if sample == "normal" else "OK")
    rules = {r["requirement_id"]: r for r in evaluation["requirements"]}
    assert rules[OWNER]["verdict"] == rules[DEST]["verdict"] == expected
    p2 = {e["event"]: 4 * e["frame"] for e in original["events"] if e["part"] == "P2"}
    assert rules[OWNER]["first_witness_step"] == p2["inspection"]
    assert rules[DEST]["first_witness_step"] == p2["output_settled"]


def test_common_oracle_separates_a_stale_tag_from_correct_sorting(capsys, tmp_path):
    def two_good(witness):
        witness["condition"] = "normal"  # The label must not select the verdict.
        witness["parts"][1].update(geometry_height_mm=33.12, probe_height_mm=33.12)

    _, _, artifact = import_sample(capsys, tmp_path, "stale_ok", two_good)
    evidence = cli(capsys, "evaluate", "--artifact", artifact)
    rules = {r["requirement_id"]: r["verdict"] for r in evidence["requirements"]}
    assert (rules[OWNER], rules[DEST]) == ("FAIL", "PASS")
    assert evidence["scene_evidence"]["parts"][1]["violations"] == ["STALE_RESULT"]


def test_common_evaluator_rejects_tampered_archive_and_import_preserves_existing_output(
    capsys, tmp_path
):
    _, source, artifact = import_sample(capsys, tmp_path, "normal")
    marker = artifact / "COMPLETE"
    checksum = file_hash(marker)
    assert (
        cli(capsys, "inspection", "import", source, "--output", artifact, code=2)[
            "code"
        ]
        == "CFG_INSPECTION_OUTPUT"
    )
    assert file_hash(marker) == checksum
    (artifact / "requirements.json").write_text(
        '[{"verdict":"PASS"}]', encoding="utf-8"
    )
    for operation in ("verify-artifact", "evaluate"):
        assert (
            cli(capsys, operation, "--artifact", artifact, code=4)["code"]
            == "TRACE_INTEGRITY"
        )


def test_builtin_scene_identity_and_controller_profiles():
    scene = scene_plugin("cell_c")
    assert scene.scene_id == "cell_c"
    assert {"reference", "mutant.stale_ok"} <= set(scene.controller_presets())
    assert scene.create_oracle(120).horizon == 120


@pytest.mark.parametrize(
    ("controller", "expected", "second_id", "second_bin"),
    [("reference", "PASS", "P2", "NG"), ("mutant.stale_ok", "FAIL", "P1", "OK")],
)
def test_fresh_mock_sorting_uses_shared_run_and_applied_command_receipts(
    capsys, tmp_path, monkeypatch, controller, expected, second_id, second_bin
):
    src = str(Path(__file__).resolve().parents[2] / "src")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([src, os.getenv("PYTHONPATH", "")])
    )
    monkeypatch.setenv("CELLIO_DATA_ROOT", str(tmp_path / "data"))
    config = scene_plugin("cell_c").scenario_presets(
        controller=controller, profile="mock_lockstep"
    )
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    validated = cli(capsys, "validate", "--config", path)
    assert validated["config"]["scene"]["cell_id"] == "cell_c"
    attempt = cli(
        capsys, "run", "--config", path, "--output", tmp_path / "runs", "--headless"
    )
    directory = Path(attempt["directory"])
    verified = cli(capsys, "verify-artifact", "--artifact", directory)
    assert verified["ticks"] == config["episode"]["horizon_steps"]
    outcome = cli(capsys, "evaluate", "--artifact", directory)
    assert outcome["online_offline_equal"] and outcome["compliance_verdict"] == expected
    second = outcome["scene_evidence"]["parts"][1]
    assert (second["result_part_id"], second["observed_bin"]) == (second_id, second_bin)
    inspected = [
        f
        for f in frames(directory)
        if f["truth"]["plant_state"].get("event") == "inspection"
    ]
    assert len(inspected) == 2
    # The live oracle reads the common command receipts, independently of the later tray pose.
    for f in inspected:
        assert "commanded_bin" not in f["truth"]["plant_state"]
        applied = f["actuation"]["applied_commands"]
        assert {cmd["actuator_id"] for cmd in applied} == {
            "inspection.route_ok",
            "inspection.source_p2",
        }
