from collections import Counter
import pytest
from cellio.io import CellIOError, digest
from cellio.runtime.campaign import (
    create_plan,
    finite_rows,
    initial_state,
    read_plan,
    soak_row,
)


PROVENANCE = {
    i + "_hash": digest(i) for i in ("assets", "protocol", "environment", "code")
}


def test_canonical_blocks_and_pairing(tmp_path):
    plan = create_plan("canonical-v1", PROVENANCE, tmp_path)
    rows = plan["runs"]
    assert len(rows) == len({r["run_id"] for r in rows}) == 820
    counts = Counter(
        "reference" if r["controller_id"] == "reference" else r["arm"] == "healthy"
        for r in rows
    )
    assert counts == {"reference": 640, True: 60, False: 120}
    assert read_plan(tmp_path / "campaign.plan.json") == plan
    assert rows == finite_rows("canonical-v1", PROVENANCE)
    assert len({digest(initial_state("cell_a", s)) for s in range(1000, 1020)}) == 20


def test_separate_suites():
    reset = finite_rows("reset-v1", PROVENANCE)
    assert len(reset) == 144 and len({r["chain_id"] for r in reset}) == 48
    assert [r["chain_slot"] for r in reset[:3]] == [
        "control",
        "predecessor",
        "post_reset",
    ]
    assert len(finite_rows("overhead-v1", PROVENANCE)) == 180
    assert len(finite_rows("boundaries-v1", PROVENANCE)) == 16
    assert len(finite_rows("clean-install", PROVENANCE)) == 10
    reuse = finite_rows("reuse-v1", PROVENANCE)
    assert len(reuse) == 40
    assert {r["initial_state_id"] for r in reuse} == set(range(2000, 2005))
    assert {r["config"]["scene"]["variant_id"] for r in reuse} == {
        "conveyor_speed_plus_10pct"
    }
    assert soak_row(PROVENANCE, 0)["run_id"] != soak_row(PROVENANCE, 10)["run_id"]
    assert soak_row(PROVENANCE, 330)["repeat_index"] == 1


def test_lock_required(tmp_path):
    with pytest.raises(CellIOError):
        create_plan("canonical-v1", {}, tmp_path)


def test_boundaries_publish_the_registered_corner_coordinates(tmp_path):
    import json

    plan = create_plan("boundaries-v1", PROVENANCE, tmp_path)
    corners = json.loads((tmp_path / "initial_boundaries.json").read_bytes())
    assert corners["plan_hash"] == plan["plan_hash"]
    assert len(corners["cases"]) == 16
    for case, row in zip(corners["cases"], plan["runs"], strict=True):
        assert case == {key: row[key] for key in case}
        assert abs(case["initial_state"]["yaw_rad"]) == 0.03


def test_pinned_oracle_and_soak_design(tmp_path):
    for suite in ("oracle-v1", "soak-v1"):
        plan = create_plan(suite, PROVENANCE, tmp_path / suite)
        assert read_plan(tmp_path / suite / "campaign.plan.json") == plan
        design = plan["suite_design"]
        if suite == "oracle-v1":
            assert len(plan["runs"]) == design["planned_runs"] == 144
        else:
            assert plan["generator"] == design["generator"]
            assert plan["runs"] == []
