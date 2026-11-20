"""The supplementary study is available on an ordinary CPU-only installation."""

import json

import pytest

from cellio.cli import main
from cellio.inspection.replay import replay_witness


def example(capsys, tmp_path, condition):
    destination = tmp_path / f"{condition}.json"
    assert (
        main(
            [
                "inspection",
                "replay",
                "--example",
                condition,
                "--export",
                str(destination),
            ]
        )
        == 0
    )
    return json.loads(capsys.readouterr().out), json.loads(destination.read_bytes())


def test_packaged_witnesses_reproduce_physical_bins_and_result_owner(tmp_path, capsys):
    healthy, _ = example(capsys, tmp_path, "normal")
    mutant, witness = example(capsys, tmp_path, "stale_ok")
    assert [p["observed_bin"] for p in healthy["parts"]] == ["OK", "NG"]
    assert healthy["verdict"] == "PASS"
    second = mutant["parts"][1]
    assert (second["result_part_id"], second["observed_bin"], second["violations"]) == (
        "P1",
        "OK",
        ["STALE_RESULT", "WRONG_DESTINATION"],
    )
    assert (
        main(["inspection", "replay", str(tmp_path / "stale_ok.json"), "--assert-pass"])
        == 5
    )
    assert json.loads(capsys.readouterr().out) == mutant
    witness["condition"] = "normal"
    assert replay_witness(witness)["verdict"] == "FAIL"


def test_stale_read_on_two_conforming_parts_is_detectable_without_a_wrong_bin(
    tmp_path, capsys
):
    _, witness = example(capsys, tmp_path, "stale_ok")
    witness["parts"][1].update(geometry_height_mm=33.12, probe_height_mm=33.12)
    second = replay_witness(witness)["parts"][1]
    assert second["violations"] == ["STALE_RESULT"]


@pytest.mark.parametrize(
    "change",
    [
        {"orientation_wxyz": [0.7071067811865476, 0.0, 0.0, 0.7071067811865476]},
        {"linear_speed_m_s": 0.006},
    ],
)
def test_final_pose_requires_the_whole_part_seated_and_stopped(
    tmp_path, capsys, change
):
    _, witness = example(capsys, tmp_path, "normal")
    witness["parts"][1]["physical_output"].update(change)
    assert replay_witness(witness)["parts"][1]["observed_bin"] == "outside"


def test_malformed_sequence_and_divergent_release_station_are_rejected(
    tmp_path, capsys
):
    _, witness = example(capsys, tmp_path, "normal")
    final_release = witness["events"][-2]
    final_release["station"] = "OK"
    with pytest.raises(ValueError, match="inconsistent routing"):
        replay_witness(witness)
    witness["events"].pop()
    with pytest.raises(ValueError, match="Incomplete or reordered"):
        replay_witness(witness)


def test_native_cli_forwards_parameters_without_loading_kit(
    tmp_path, monkeypatch, capsys
):
    from cellio.inspection import commands

    calls = []

    def dispatch(script, args):
        calls.append((script, args))

    monkeypatch.setattr(commands, "_execute", dispatch)
    dest = tmp_path / "new scene"
    cache = tmp_path / "robot assets"
    assert (
        main(["inspection", "prepare", "--output", str(dest), "--assets", str(cache)])
        == 0
    )
    assert calls == [("prepare.py", ["--out", dest, "--assets", cache])]
    assert json.loads(capsys.readouterr().out)["scene"] == str(
        dest / "cell_c_inspection.usda"
    )
