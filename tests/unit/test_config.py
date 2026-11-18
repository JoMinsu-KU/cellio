import copy
import pytest
from cellio.config import load, resolve
from cellio.io import CellIOError, digest
from cellio.runtime.campaign import scenario


def test_cfg01_duplicate_keys(tmp_path):
    path = tmp_path / "case.yml"
    path.write_text("scene: true\nscene: false\n", encoding="utf-8")
    with pytest.raises(CellIOError, match="Duplicate"):
        load(path)


@pytest.mark.parametrize(
    ("field", "bad"),
    [("unknown", None), ("seeds", {"scene": True}), ("clock", {"physics_hz": 30})],
)
def test_cfg02_03_unknown_fields_and_types(field, bad):
    cfg = scenario("cell_a")
    cfg[field] = bad
    with pytest.raises(CellIOError):
        resolve(cfg)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("target_id", "gripper.gap_m"),
        ("arm", {"open_step": 11, "close_step": 10}),
        ("trigger", {"kind": "world_rising", "predicate_id": "world.fixture_loaded"}),
    ],
)
def test_cfg04_05_invalid_fault(field, bad):
    cfg = scenario("cell_a", "f1_high")
    cfg["faults"][0][field] = bad
    with pytest.raises(CellIOError):
        resolve(cfg)


def test_cfg06_reference():
    cfg = scenario("cell_a")
    cfg["controller"]["id"] = "module.name"
    with pytest.raises(CellIOError):
        resolve(cfg)


def test_cfg07_freeze():
    cfg = scenario("cell_b", "f6")
    rev = dict(reversed(list(cfg.items())))
    assert digest(resolve(rev)) == digest(resolve(cfg))
    assert cfg == copy.deepcopy(cfg)


@pytest.mark.parametrize(
    "path", ["../data", "a/../../b", "C:/Windows", "\\\\server\\data", "/tmp/data"]
)
def test_cfg08_escape(path):
    cfg = scenario("cell_a")
    cfg["artifacts"]["output_root"] = path
    with pytest.raises(CellIOError):
        resolve(cfg)


def test_cfg08_cycle(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("loop: &a [*a]", encoding="utf-8")
    with pytest.raises(CellIOError):
        load(path)
