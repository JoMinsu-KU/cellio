from types import SimpleNamespace

import pytest

from cellio.adapters.mock import MockPlant
from cellio.analysis.replay import read_attempt
from cellio.config import resolve
from cellio.io import CellIOError
from cellio.plugins import _resolve, allowed_scenes, scene_plugin
from cellio.plugins.reference import reference_scene
from cellio.runtime.campaign import scenario
from cellio.runtime.episode import run_episode


class ExtensionPlant(MockPlant):
    def load(self, config):
        assert config["cell_id"] == "lab_cell"
        super().load(dict(config, cell_id="cell_a"))


class Extension:
    scene_id = "lab_cell"
    revision = "1.0.1"

    def __getattr__(self, key):
        return getattr(reference_scene("cell_a"), key)

    def create_plant(self, profile, options):
        assert profile == "mock"
        return ExtensionPlant()


def test_installed_allowlist_and_shared_robot_ports(monkeypatch, tmp_path):
    import cellio.plugins as plugins

    old = plugins.metadata.entry_points
    calls = []

    class Entry:
        dist = SimpleNamespace(name="lab-cell-fixture", version="0.1")

        def load(self):
            calls.append("loaded")
            return Extension

    monkeypatch.setattr(
        plugins.metadata,
        "entry_points",
        lambda **k: (Entry(),) if k.get("name") == "lab_cell" else old(**k),
    )
    monkeypatch.setenv("CELLIO_SCENE_ALLOWLIST", "")
    _resolve.cache_clear()
    with pytest.raises(CellIOError, match="allowlist"):
        scene_plugin("lab_cell")
    assert calls == []
    monkeypatch.setenv("CELLIO_SCENE_ALLOWLIST", "lab_cell")
    cfg = scenario("cell_a", profile="mock_lockstep")
    cfg["scene"]["cell_id"] = cfg["requirements"]["preset"] = "lab_cell"
    cfg["episode"]["horizon_steps"] = 8
    assert resolve(cfg)["scene"]["cell_id"] == "lab_cell"
    r = run_episode(
        cfg,
        tmp_path / "ext",
        run_id="ext",
        attempt_id="ext",
        initial_state={},
        environment={"backend": "mock"},
        assets={},
        protocol={},
        requirements_lock="",
    )
    assert r["completion_status"] == "COMPLETED" and r["end_step"] == 8
    assert (
        r["scene_plugin"]["provider"] == "lab-cell-fixture"
        and len(r["scene_plugin"]["implementation_sha256"]) == 64
    )
    assert read_attempt(tmp_path / "ext")["online_offline_equal"]
    assert len(calls) == 1
    monkeypatch.setenv("CELLIO_SCENE_ALLOWLIST", "os:system")
    with pytest.raises(CellIOError, match="allowlist"):
        allowed_scenes()
    _resolve.cache_clear()
