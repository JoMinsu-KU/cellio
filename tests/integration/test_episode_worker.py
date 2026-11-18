import json
from pathlib import Path

import pytest

from cellio.adapters.mock import MockPlant
from cellio.analysis.replay import read_attempt
from cellio.artifacts import AttemptWriter, frames, verify_artifact
from cellio.core.models import CellIOError
from cellio.io import canonical, write_json
from cellio.runtime.campaign import initial_state, scenario
from cellio.runtime.episode import run_episode
from cellio.runtime.worker import execute_job


def job(destination, horizon=24, attempt="test", mode="fresh_process"):
    cfg = scenario("cell_a", profile="mock_lockstep")
    cfg["episode"].update(horizon_steps=horizon, reset_mode=mode)
    return dict(
        directory=str(destination),
        config=cfg,
        run_id="unit",
        attempt_id=attempt,
        initial_state=initial_state("cell_a", 0),
        environment={"backend": "mock"},
        assets={"kind": "mock"},
        protocol={"horizon": horizon},
        requirements_lock="test fixture\n",
    )


def test_T_RUN_01_06_full_h_lifecycle_replay(tmp_path):
    dest = tmp_path / "run"
    r = run_episode(**job(dest))
    assert [e["state"] for e in r["lifecycle"]] == [
        "PLANNED",
        "PREFLIGHT",
        "BOOTING",
        "LOADING",
        "WARMUP",
        "READY",
        "RUNNING",
        "FINALIZING",
        "CLOSED",
    ]
    assert (r["completion_status"], r["end_step"], r["last_successful_advance"]) == (
        "COMPLETED",
        24,
        24,
    )
    v = verify_artifact(dest)
    assert v["ticks"] == 24 and v["artifact_digest"] == r["artifact_digest"]
    seq = list(frames(dest))
    assert [f["step"] for f in seq] == list(range(25))
    assert [f["step"] for f in seq if f["controller_called"]] == list(range(0, 24, 4))
    assert seq[-1]["final_snapshot"] and not seq[-1]["intents"]
    assert (
        seq[0]["previous_advance"] is None
        and seq[-1]["previous_advance"]["to_step"] == 24
    )
    assert all(f["controller_view_hash"] for f in seq if f["controller_called"])
    assert read_attempt(dest)["online_offline_equal"]
    assert r["case_verdict"] == "NOT_SCORED"


def test_T_RUN_02_boot_failure_no_fictitious_snapshot(tmp_path, monkeypatch):
    def cannot_boot(*args):
        raise CellIOError("ENV_BOOT_FAILED", "fixture unavailable")

    import cellio.runtime.episode as episode

    monkeypatch.setattr(episode, "make_plant", cannot_boot)
    dest = tmp_path / "failed"
    r = run_episode(**job(dest))
    assert (r["reason"], r["completion_status"], r["task_outcome"]) == (
        "ENV_BOOT_FAILED",
        "INFRA_ERROR",
        "UNKNOWN",
    )
    assert r["no_snapshot"] and r["end_step"] is None and not list(frames(dest))
    assert verify_artifact(dest)["completion_status"] == "INFRA_ERROR"


def test_T_RUN_03_stalled_advance_retains_last_decision(tmp_path):
    class Stall(MockPlant):
        def advance_one_step(self, render=False):
            if self.step == 3:
                raise CellIOError("SIM_STALL", "no physics progress")
            return super().advance_one_step(render)

    dest = tmp_path / "stalled"
    r = run_episode(**job(dest), hardware_session=Stall())
    seq = list(frames(dest))
    assert (r["reason"], r["end_step"], r["last_successful_advance"]) == (
        "SIM_STALL",
        3,
        3,
    )
    assert [f["step"] for f in seq] == [0, 1, 2, 3, 3]
    assert seq[-1]["reused_snapshot"] and verify_artifact(dest)["ticks"] == 4
    assert read_attempt(dest)["online_offline_equal"]


def test_T_RUN_04_user_cancel_keeps_prefix(tmp_path):
    current = []

    def heartbeat(state, step):
        if state == "RUNNING":
            current.append(step)

    dest = tmp_path / "cancel"
    r = run_episode(
        **job(dest), heartbeat=heartbeat, cancel_requested=lambda: 4 in current
    )
    assert (r["completion_status"], r["reason"], r["end_step"]) == (
        "CANCELLED",
        "USER_CANCELLED",
        4,
    )
    assert verify_artifact(dest)["ticks"] == 4
    assert read_attempt(dest)["online_offline_equal"]


def test_T_RUN_05_writer_error_is_not_success(tmp_path):
    class BrokenWriter(AttemptWriter):
        def record(self, frame, **kwargs):
            if frame.tick.step == 3 and not getattr(self, "fault_seen", False):
                self.fault_seen = True
                raise CellIOError("TRACE_WRITE", "injected write error")
            return super().record(frame, **kwargs)

    dest = tmp_path / "writer-failed"
    r = run_episode(**job(dest), writer_factory=BrokenWriter)
    assert (
        r["completion_status"] == "INFRA_ERROR"
        and r["errors"][0]["code"] == "TRACE_WRITE"
    )
    assert verify_artifact(dest)["ticks"] == 4 and r["last_successful_advance"] == 3
    assert (
        list(frames(dest))[-1]["reused_snapshot"]
        and read_attempt(dest)["online_offline_equal"]
    )


def test_T_RUN_05_persistent_writer_failure_preserves_diagnostics_without_complete(
    tmp_path,
):
    class BrokenWriter(AttemptWriter):
        def record(self, frame, **kwargs):
            if frame.tick.step == 3:
                raise CellIOError("TRACE_WRITE", "persistent fixture error")
            return super().record(frame, **kwargs)

    dest = tmp_path / "partial"
    r = run_episode(**job(dest), writer_factory=BrokenWriter)
    assert (
        r["completion_status"],
        r["reason"],
        r["end_step"],
        r["artifact_digest"],
    ) == ("INFRA_ERROR", "TRACE_WRITE", 3, None)
    assert (dest / "trace.jsonl.gz.partial").is_file()
    assert (dest / "manifest.start.json").is_file() and (
        dest / "errors.jsonl"
    ).is_file()
    assert (
        not (dest / "COMPLETE").exists() and not (dest / "manifest.final.json").exists()
    )


def test_missing_last_snapshot_uses_its_own_previous_advance(tmp_path):
    class LostView(MockPlant):
        def snapshot(self, tick):
            if tick.step >= 3:
                raise CellIOError("SIM_STATE_INVALID", "fixture expired physics handle")
            return super().snapshot(tick)

    dest = tmp_path / "lost"
    r = run_episode(**job(dest), hardware_session=LostView())
    assert (r["end_step"], r["last_successful_advance"]) == (2, 3)
    assert list(frames(dest))[-1]["previous_advance"]["to_step"] == 2
    assert verify_artifact(dest)["ticks"] == 3


def test_mock_worker_group_isolates_generation_and_routes_outputs(tmp_path):
    a, b = (
        job(tmp_path / "a", attempt="a", mode="hard_reload"),
        job(tmp_path / "b", attempt="b", mode="hard_reload"),
    )
    path = tmp_path / "job.json"
    write_json(path, {"episodes": [a, b]})
    assert execute_job(path) == 0
    summary = json.loads(path.with_suffix(".result.json").read_bytes())
    assert summary["episode_count"] == 2 and summary["completion_status"] == "COMPLETED"
    first, second = (list(frames(Path(x["directory"]))) for x in (a, b))
    assert first[0]["generation"] == 0 and second[0]["generation"] == 1
    assert first[0]["truth"]["plant_state"] == second[0]["truth"]["plant_state"]
    assert first[0]["observed_signals"]["gripper.open"]["source_seq"] == 0
    assert second[0]["observed_signals"]["gripper.open"]["transport_seq"] == 0
    assert (
        not second[0]["intents"] and not second[0]["actuation"]["active_override_ids"]
    )
    assert second[4]["intents"][0]["command_id"].startswith("b:cmd:0")
    assert first[0]["episode_id"] != second[0]["episode_id"]
    assert all(event["event_type"] != "reset.compare" for event in first[0]["events"])
    comparison = next(
        event["payload"]
        for event in second[0]["events"]
        if event["event_type"] == "reset.compare"
    )
    assert comparison["state_cleared"] is True and all(comparison["checks"].values())
    assert comparison["predecessor"]["episode_id"] == first[0]["episode_id"]
    assert (
        comparison["predecessor"]["manifest_sha256"]
        == (tmp_path / "a" / "COMPLETE").read_text().strip()
    )
    assert read_attempt(Path(b["directory"]))["online_offline_equal"]


def test_hard_reload_without_predecessor_does_not_count_reset_coverage(tmp_path):
    dest = tmp_path / "fresh"
    result = run_episode(**job(dest, mode="hard_reload"), generation=9)
    assert (
        result["completion_status"] == "COMPLETED"
        and result["reset_comparison"] is None
    )
    assert all(
        e["event_type"] != "reset.compare" for f in frames(dest) for e in f["events"]
    )


@pytest.mark.parametrize(
    ("family", "target", "parameters", "dirty_field"),
    [
        ("F3", "gripper.closed", {"delay_steps": 12}, "observation_queue_empty"),
        ("F4", "actuator.gripper", {"delay_steps": 12}, "command_queue_empty"),
        (
            "F6",
            "actuator.gripper",
            {"hold_tolerance_m": 0.0005, "conflicting_travel_min_m": 0.010},
            "fault_reset",
        ),
    ],
)
def test_T_RESET_01_12_committed_dirty_predecessor_and_clean_successor(
    tmp_path, family, target, parameters, dirty_field
):
    from cellio.core.faults import MODEL_IDS

    first = job(tmp_path / "abnormal", horizon=40, attempt="source", mode="hard_reload")
    first["test_control"] = {"kind": "fault_start_offset", "steps": 8}
    first["config"]["faults"] = [
        {
            "fault_id": "fault",
            "family": family,
            "model_id": MODEL_IDS[family],
            "target_id": target,
            "arm": {"open_step": 0, "close_step": 40},
            "trigger": {"kind": "at_step", "at_step": 0},
            "offset_steps": 0,
            "duration_steps": 24,
            "parameters": parameters,
            "require_exercised": True,
        }
    ]
    second = job(
        tmp_path / "successor", horizon=12, attempt="successor", mode="hard_reload"
    )
    path = tmp_path / "job.json"
    write_json(path, {"episodes": [first, second]})
    assert execute_job(path) == 0
    first_result = json.loads((tmp_path / "abnormal" / "result.json").read_bytes())
    second_result = json.loads((tmp_path / "successor" / "result.json").read_bytes())
    plan = first_result["compiled_fault_plan"]
    assert plan["resolved_resource"]["id"] == target and plan["fault_id"] == "fault"
    assert (
        len(plan["realization_rule_sha256"]) == 64
        and plan["recovery_window_steps"] == 12
    )
    assert plan["queue_limit"] == {"F3": 14, "F4": 256, "F6": 0}[family]
    if family == "F6":
        assert plan["resolved_resource"]["driver_ids"] == ["panda_finger_joint1"]
    assert (
        first_result["completion_status"] == "CANCELLED"
        and first_result["end_step"] == 8
    )
    compare = second_result["reset_comparison"]
    assert (
        compare["predecessor"]["isolation_before_teardown"]
        == first_result["isolation_before_teardown"]
    )
    assert all(
        compare["predecessor"]["isolation_before_teardown"][key] is False
        for key in (
            dirty_field,
            "signals_reset",
            "oracle_reset",
            "events_reset",
            "controller_reset",
            "operation_reset",
        )
    )
    assert compare["state_cleared"] is True and all(compare["checks"].values())
    assert read_attempt(tmp_path / "successor")["online_offline_equal"]


@pytest.mark.parametrize(
    "bad", ["duplicate_generation", "duplicate_attempt", "duplicate_directory"]
)
def test_worker_preserves_prior_attempt_and_stops_on_invalid_group_identity(
    bad, tmp_path
):
    first = job(tmp_path / "first", horizon=8, attempt="first", mode="hard_reload")
    second = job(tmp_path / "second", horizon=8, attempt="second", mode="hard_reload")
    if bad == "duplicate_generation":
        first["generation"] = second["generation"] = 3
    elif bad == "duplicate_attempt":
        second["attempt_id"] = first["attempt_id"]
    else:
        second["directory"] = first["directory"]
    path = tmp_path / "job.json"
    write_json(path, {"episodes": [first, second]})
    assert execute_job(path) == 4
    assert verify_artifact(tmp_path / "first")["completion_status"] == "COMPLETED"
    assert not (tmp_path / "second").exists()
    summary = json.loads(path.with_suffix(".result.json").read_bytes())
    assert (
        summary["completion_status"] == "INFRA_ERROR" and summary["episode_count"] == 1
    )


def test_soak_inbox_switches_cells_in_one_runtime_and_rejects_late_callbacks(
    tmp_path, monkeypatch
):
    import cellio.runtime.worker as worker
    from cellio.core.clock import StepClock

    plants = []

    class CountingPlant(MockPlant):
        def __init__(self):
            self.closed = 0
            plants.append(self)
            super().__init__()

        def snapshot(self, tick):
            if self.generation == 1 and self.step == 0:
                stale = StepClock("previous", 0, 8).at()
                with pytest.raises(CellIOError):
                    super().snapshot(stale)
            return super().snapshot(tick)

        def close(self):
            super().close()
            self.closed += 1

    monkeypatch.setattr(worker, "make_plant", lambda *_: CountingPlant())
    first = job(tmp_path / "a", horizon=8, attempt="a", mode="hard_reload")
    second = job(tmp_path / "b", horizon=8, attempt="b", mode="hard_reload")
    second["config"] = scenario("cell_b", profile="mock_lockstep")
    second["config"]["episode"].update(horizon_steps=8, reset_mode="hard_reload")
    second["initial_state"] = initial_state("cell_b", 1)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(
        b"".join(
            canonical(row) + b"\n"
            for row in ({"episode": first}, {"episode": second}, {"stop": True})
        )
    )
    path = tmp_path / "job.json"
    write_json(path, {"inbox_path": str(inbox)})
    assert execute_job(path) == 0
    assert len(plants) == 1 and plants[0].closed == 1
    result = json.loads((tmp_path / "b" / "result.json").read_bytes())
    assert result["reset_comparison"]["state_cleared"] is True
    assert read_attempt(tmp_path / "b")["online_offline_equal"]


def test_reset_control_hits_deterministic_P0_and_B0_is_offline(tmp_path):
    j = job(tmp_path / "reset", horizon=50, mode="hard_reload")
    c = j["config"]
    c["faults"] = [
        {
            "fault_id": "f",
            "family": "F5",
            "model_id": "explicit_invalid_v1",
            "target_id": "gripper.closed",
            "arm": {"open_step": 0, "close_step": 50},
            "trigger": {"kind": "at_step", "at_step": 4},
            "offset_steps": 0,
            "duration_steps": 24,
            "parameters": {},
            "require_exercised": True,
        }
    ]
    r = run_episode(**j, test_control={"kind": "fault_start_offset", "steps": 4})
    assert (r["end_step"], r["reason"], r["completion_status"]) == (
        8,
        "TEST_RESET_CANCEL",
        "CANCELLED",
    )
    assert r["faults"][0]["actual_start"] == 4
    dest = tmp_path / "baseline"
    baseline = run_episode(**job(dest), benchmark_mode="B0")
    assert baseline["requirements"] == [] and baseline["task_outcome"] == "UNKNOWN"
    assert read_attempt(dest)["online_comparable"] is False


def test_reuse_variant_changes_cell_a_belt_setpoint_and_rejects_cell_b(tmp_path):
    from cellio.config import resolve
    from cellio.io import CellIOError as ConfigError

    for variant, expected in (
        ("canonical", 0.060),
        ("conveyor_speed_plus_10pct", 0.066),
    ):
        dest = tmp_path / variant
        experiment = job(dest, horizon=64, attempt=variant)
        experiment["config"]["scene"]["variant_id"] = variant
        result = run_episode(**experiment)
        assert result["plant_provenance"]["scene_variant"] == variant
        recorded = [
            f["truth"]["plant_state"]["belt.surface_speed_mps"] for f in frames(dest)
        ]
        assert max(recorded) == pytest.approx(expected)
    cfg = scenario("cell_b", profile="mock_lockstep")
    cfg["scene"]["variant_id"] = "conveyor_speed_plus_10pct"
    with pytest.raises(ConfigError):
        resolve(cfg)
