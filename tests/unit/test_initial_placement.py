import hashlib
import json
import random
from dataclasses import replace

import pytest

from cellio.io import CellIOError
from cellio.runtime.campaign import initial_state, make_row
from cellio.scenes import get_scene
from cellio.scenes.definition import Box


@pytest.mark.parametrize(
    "cell,bounds", [("cell_a", (0.001, 0.004)), ("cell_b", (0.002, 0.002))]
)
def test_canonical_sample_retains_first_draw_and_signed_metric_clearances(cell, bounds):
    seed = 3
    key = hashlib.sha256(f"cellio/1.0/{cell}/{seed}/scene".encode()).digest()
    rng = random.Random(int.from_bytes(key[:16], "big"))
    state = initial_state(cell, seed)
    expected = {
        "x_offset_m": rng.uniform(-bounds[0], bounds[0]),
        "y_offset_m": rng.uniform(-bounds[1], bounds[1]),
        "yaw_rad": rng.uniform(-0.03, 0.03),
        "mass_kg": 0.05,
    }
    assert {k: state[k] for k in expected} == expected
    [draw] = state["placement"]["proposals"]
    assert draw["state"] == expected and draw["geometry"]["accepted"]
    assert draw["geometry"]["support_vertical_gap_m"] == pytest.approx(0.005)
    assert draw["geometry"]["support_xy_margin_m"] > 0.04
    assert draw["geometry"]["separating_axis_gaps_m"]["Table"] == pytest.approx(0.045)


def test_real_overlap_rejects_all_twenty_recorded_proposals(monkeypatch):
    import cellio.scenes

    scene = get_scene("cell_a")
    obstacle = Box("test_obstacle", scene.part_center, (0.02, 0.02, 0.02))
    blocked = replace(scene, boxes=(*scene.boxes, obstacle))
    monkeypatch.setattr(cellio.scenes, "get_scene", lambda c: blocked)
    with pytest.raises(CellIOError) as result:
        initial_state("cell_a", 1)
    assert result.value.code == "INIT_SAMPLING_FAILED"
    proposals = json.loads(str(result.value).partition("proposals: ")[2])
    assert len(proposals) == 20
    assert len({p["state"]["x_offset_m"] for p in proposals}) == 20
    assert all(
        p["geometry"]["overlapping_solids"] == ["test_obstacle"]
        and not p["geometry"]["accepted"]
        for p in proposals
    )


def test_tangential_support_margin_and_frozen_placement_without_resampling():
    row = make_row(
        "commission-v1",
        {"protocol_hash": "fixture"},
        "cell_a",
        "healthy",
        0,
        state={"x_offset_m": 0.0, "y_offset_m": 0.0, "yaw_rad": 0.03, "mass_kg": 0.05},
    )
    checked = row["initial_state"]
    assert checked["x_offset_m"] == 0 and checked["placement"]["mode"] == "fixed"
    bad = {**checked, "x_offset_m": 0.053}
    with pytest.raises(CellIOError, match="Frozen cell_a placement rejected"):
        make_row(
            "commission-v1",
            {"protocol_hash": "fixture"},
            "cell_a",
            "healthy",
            0,
            state=bad,
        )
