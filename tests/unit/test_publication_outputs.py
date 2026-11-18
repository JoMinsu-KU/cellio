"""Check figure denominators and missing-evidence panels without running Isaac."""

from collections import Counter
from pathlib import Path

import pytest


def test_publication_counts_include_all_controllers_and_pending(monkeypatch):
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    from matplotlib import pyplot as plt

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from publication_panels import coverage, stability

    def group(controller, task, compliance, activated, realized):
        return {
            "group": {"cell": "cell_a", "arm": "f1_low", "controller": controller},
            "counts": {
                "N_plan": 1,
                "N_faultplan": 1,
                "N_start": 1,
                "N_activate": activated,
                "N_realized": realized,
            },
            "task_distribution": {task: {"numerator": 1}},
            "compliance_distribution": {compliance: {"numerator": 1}},
        }

    measured = {
        "groups": [
            group("reference", "CONTROLLED_ABORT", "PASS", 1, 1),
            group("mutant.c_m2", "ERROR", "UNAVAILABLE", 0, 0),
        ]
    }
    captured = {}

    def save(fig, name):
        captured[name] = fig

    assert coverage(plt, save, measured)
    fig = captured["figure4_realization_task_compliance"]
    assert [p.get_width() for p in fig.axes[0].patches] == [2, 1, 1]
    for axis in fig.axes[1:]:
        assert sum(p.get_width() for p in axis.patches) == 2
    assert not coverage(plt, save, {"groups": []})
    assert not stability(
        plt,
        save,
        {"soak-v1": {"soak": {"status": "FAIL", "elapsed_wall_seconds": 7200}}},
    )
    chain = {"source_artifact_ids": ["verified-control"], "status": "INCOMPLETE"}
    assert stability(plt, save, {"reset-v1": {"reset_chains": {"chains": [chain]}}})
    fig = captured["figure6_overhead_reset_soak"]
    assert [p.get_width() for p in fig.axes[1].patches] == [1]
    assert "INCOMPLETE" in {t.get_text() for t in fig.axes[1].get_yticklabels()}
    plt.close("all")


def test_publication_sources_bind_workstation_versions_and_original_frames(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from publication_sources import ROOT, environment, file_hash, modules, ports, read

    assert len(modules()) == 16
    assert {
        r["id"] for r in modules() if r["source_status"] == "definition present"
    } == {f"M{k:02}" for k in range(1, 16)}
    assert len(ports()["controller_view_fields"]) == 4
    lock_rows = environment()
    for scope in ("windows-isaac62", "wsl-audit"):
        assert any(
            row["scope"] == scope and row["item"] == "python" for row in lock_rows
        )
    assert all(row["sha256"] == file_hash(ROOT / row["source"]) for row in lock_rows)
    directory = ROOT / "results/workstation/figures"
    shots = read(directory / "fixed-camera.json")["panels"]
    assert Counter((p["cell"], p["stage"]) for p in shots) == {
        ("cell_a", "transfer"): 1,
        ("cell_b", "process"): 1,
    }
    assert all(file_hash(directory / p["png"]) == p["png_sha256"] for p in shots)
