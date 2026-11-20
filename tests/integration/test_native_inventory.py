import json

import pytest

from cellio.artifacts import verify_artifact
from cellio.io import CellIOError, file_hash, write_json
from cellio.plugins import scene_plugin
from cellio.runtime.campaign import scenario
from cellio.runtime.episode import run_episode


def test_cell_c_native_files_are_mandatory_and_sealed(tmp_path, monkeypatch):
    # Exercise the native writer/reader contract with explicit fixture bytes and no GPU.
    scene = scene_plugin("cell_c")
    plant = scene.create_plant("mock", {})
    bind = plant.configure_attempt
    declared = scene.evidence_manifest()["native_artifacts"]

    def supply_fixture(directory, config):
        bind(directory, config)
        (directory / "native").mkdir()
        for name in declared:
            (directory / name).write_bytes(b'{"fixture":true}\n')

    monkeypatch.setattr(plant, "configure_attempt", supply_fixture)
    directory = tmp_path / "attempt"
    config = scenario("cell_c")
    config["episode"]["horizon_steps"] = 32
    result = run_episode(
        config,
        directory,
        run_id="fixture",
        attempt_id="fixture",
        initial_state={},
        environment={"backend": "test_double"},
        assets={},
        protocol={},
        requirements_lock="",
        hardware_session=plant,
    )
    assert result["completion_status"] == "COMPLETED", result["errors"]
    assert verify_artifact(directory)["valid"]

    for name in declared:
        path = directory / name
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with pytest.raises(CellIOError, match="checksum mismatch"):
            verify_artifact(directory)
        path.write_bytes(original)

    manifest_path = directory / "manifest.final.json"
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["files"]["native/events.json"]
    write_json(manifest_path, manifest)
    (directory / "COMPLETE").write_bytes((file_hash(manifest_path) + "\n").encode())
    with pytest.raises(CellIOError, match="inventory is incomplete"):
        verify_artifact(directory)
