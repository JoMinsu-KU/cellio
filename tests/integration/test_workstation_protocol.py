"""Protocol orchestration contracts. Workers here use only the analytic mock plant."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from cellio.environment import lock_context
from cellio.io import CellIOError, digest, file_hash, write_json
from cellio.runtime import supervisor
from cellio.runtime.campaign import initial_state, scenario

script = Path(__file__).resolve().parents[2] / "scripts/run_workstation_protocol.py"
spec = importlib.util.spec_from_file_location("workstation_protocol", script)
workstation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workstation)


def fixture_campaign(tmp_path, monkeypatch, suite):
    monkeypatch.setenv("CELLIO_DATA_ROOT", str(tmp_path / "runtime"))
    ctx, identity = lock_context("mock_lockstep")
    monkeypatch.setattr(
        workstation, "lock_context", lambda *_args, **_kwargs: (ctx, identity)
    )
    monkeypatch.setattr(
        supervisor, "lock_context", lambda *_args, **_kwargs: (ctx, identity)
    )
    design = {"planned_runs": 2, "mode": "finite"}
    monkeypatch.setattr(workstation, "suite_design", lambda name: design)
    rows = []
    for seed in (1, 2):
        cfg = scenario("cell_a", seed=seed, profile="mock_lockstep")
        cfg["episode"]["horizon_steps"] = 8
        semantic = {
            "suite_id": suite,
            "cell": "cell_a",
            "arm": "healthy",
            "initial_state": initial_state("cell_a", seed),
            "config_hash": digest(cfg),
            "test_control": {"kind": "full_h"},
        }
        row = {
            **semantic,
            "run_id": digest({"row": semantic, "provenance": identity})[:24],
            "config": cfg,
        }
        rows.append(row)
    monkeypatch.setattr(workstation, "finite_rows", lambda *_args: rows)
    header = {
        "suite_id": suite,
        "schema_version": "cellio.plan/1.0",
        "plan_mode": "finite",
        "suite_design": design,
        "provenance": identity,
        "runs": rows,
    }
    root = tmp_path / "suites"
    write_json(
        root / suite / "campaign.plan.json", {**header, "plan_hash": digest(header)}
    )
    calls = []

    def cli(self, label, *args):
        assert args[0] == "campaign" and args[1] in ("run", "resume")
        calls.append(args)
        return supervisor.execute_plan(
            Path(args[3]), resume=args[1] == "resume", max_groups=int(args[5])
        )

    monkeypatch.setattr(workstation.Protocol, "cli", cli)
    return root, rows, calls


def test_first_failed_healthy_attempt_stops_before_second_and_is_never_retried(
    tmp_path, monkeypatch
):
    suite = "commission-v1"
    root, rows, calls = fixture_campaign(tmp_path, monkeypatch, suite)
    for _ in range(2):
        with pytest.raises(CellIOError, match="healthy commissioning"):
            workstation.Protocol(root).finite(suite, True)
    assert len(calls) == 1 and calls[0][-1] == "1"
    ledger = supervisor.history(root / suite)
    assert list(ledger) == [rows[0]["run_id"]] and len(ledger[rows[0]["run_id"]]) == 1
    marker = (
        root
        / suite
        / rows[0]["run_id"]
        / ledger[rows[0]["run_id"]][0]["attempt_id"]
        / "COMPLETE"
    )
    assert marker.is_file()


def test_completed_finite_batch_is_replayed_without_launching_again(
    tmp_path, monkeypatch
):
    suite = "test-mock-batch"
    root, _, calls = fixture_campaign(tmp_path, monkeypatch, suite)
    workstation.Protocol(root).finite(suite, True)
    before = file_hash(root / suite / "ledger.jsonl")
    workstation.Protocol(root).finite(suite, False)
    assert len(calls) == 1 and len(supervisor.history(root / suite)) == 2
    assert file_hash(root / suite / "ledger.jsonl") == before


def test_freeze_and_soak_reentry_requires_original_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLIO_DATA_ROOT", str(tmp_path / "data"))
    runner = workstation.Protocol(tmp_path / "suites")

    def no_command(*_args, **_kwargs):
        pytest.fail("An already frozen or started campaign attempted another launch")

    monkeypatch.setattr(runner, "cli", no_command)
    monkeypatch.setattr(runner, "invoke", no_command)
    runner.preflight = {"commission-v1": {"plan_hash": "proof"}}
    ctx = {"protocol": {"evidence": dict(runner.preflight)}}
    ident = {"protocol_hash": "fixed"}
    write_json(runner.frozen, ctx["protocol"])
    monkeypatch.setattr(
        workstation, "lock_context", lambda *_args, **_kwargs: (ctx, ident)
    )
    runner.freeze(True)
    runner.preflight = {}
    with pytest.raises(CellIOError, match="fingerprints"):
        runner.freeze(True)
    directory = runner.root / "soak-v1"
    design = {
        "mode": "soak_generator",
        "generator": {"warmup": 10, "minimum_completed": 200},
    }
    plan = {
        "schema_version": "cellio.plan/1.0",
        "suite_id": "soak-v1",
        "suite_design": design,
        "runs": [],
        "plan_mode": "soak_generator",
        "generator": design["generator"],
        "provenance": ident,
    }
    write_json(directory / "campaign.plan.json", {**plan, "plan_hash": digest(plan)})
    monkeypatch.setattr(workstation, "suite_design", lambda _: design)
    (directory / "ledger.jsonl").touch()
    with pytest.raises(CellIOError, match="interrupted soak"):
        runner.soak()
    write_json(directory / "soak.result.json", {"fixture": True})
    monkeypatch.setattr(
        workstation,
        "load_campaign",
        lambda _: (plan, [SimpleNamespace(error=None, verified_decision=True)], []),
    )
    monkeypatch.setattr(
        workstation, "soak_evidence", lambda *_: {"status": "PASS", "worker_pid": 17}
    )
    runner.soak()


def test_completed_retry_does_not_hide_failed_first_attempt(tmp_path, monkeypatch):
    suite = "test-mock-batch"
    root, rows, calls = fixture_campaign(tmp_path, monkeypatch, suite)
    row = rows[0]
    ctx, _ = workstation.lock_context("mock_lockstep")
    directory = root / suite
    first = supervisor.attempt(directory, row, ctx)
    supervisor.transition(directory, first, "PLANNED")
    proof = supervisor.run_group(
        directory, [{**row, "test_control": {"kind": "fixture_invalid"}}], [first], ctx
    )
    assert proof[0]["completion_status"] == "INFRA_ERROR"
    retry = supervisor.attempt(
        directory, row, ctx, 1, first["attempt_id"], "fixture recovery"
    )
    supervisor.transition(directory, retry, "PLANNED")
    proof = supervisor.run_group(directory, [row], [retry], ctx)
    assert proof[0]["completion_status"] == "COMPLETED"
    before = file_hash(directory / "ledger.jsonl")
    with pytest.raises(CellIOError, match=first["attempt_id"]):
        workstation.Protocol(root).finite(suite, True)
    assert not calls and file_hash(directory / "ledger.jsonl") == before


def test_console_interrupt_waits_for_supervisor_to_seal_attempts(tmp_path, monkeypatch):
    runner = workstation.Protocol(tmp_path)

    class InterruptedChild:
        pid = 123
        returncode = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert self.returncode == 130

        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt()
            self.returncode = 130
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        workstation.subprocess, "Popen", lambda *_args, **_kwargs: InterruptedChild()
    )
    with pytest.raises(KeyboardInterrupt):
        runner.invoke("fixture", ["-V"])
    journal = list(workstation.lines(runner.logs / "journal.jsonl"))
    assert [e["event"] for e in journal] == [
        "launch",
        "process",
        "awaiting_child_shutdown",
        "child_exit",
    ]
    assert (
        journal[-1]["code"] == 130 and journal[-1]["unix_ns"] >= journal[0]["unix_ns"]
    )


def test_resumed_batch_budgets_only_the_remaining_steps(tmp_path, monkeypatch):
    root, _, _ = fixture_campaign(tmp_path, monkeypatch, "canonical-v1")
    plan = root / "canonical-v1/campaign.plan.json"
    counted = []
    monkeypatch.setattr(
        supervisor, "check_storage", lambda _d, _ctx, s: counted.append(s)
    )
    for resume in (False, True, True):
        supervisor.execute_plan(plan, resume=resume, max_groups=1)
    assert counted == [16, 8, 0]


def test_full_or_partial_disk_allowance_and_zero_scheduled_work(tmp_path, monkeypatch):
    ctx = {"protocol": {"pilot_bytes_per_step": 2**20}}
    threshold = 10 * 2**30 + 3 * 2**20
    monkeypatch.setattr(
        supervisor.shutil, "disk_usage", lambda _p: SimpleNamespace(free=threshold)
    )
    supervisor.check_storage(tmp_path, ctx, 2)
    with pytest.raises(CellIOError, match="requires at least"):
        supervisor.check_storage(tmp_path, ctx, 3)
    supervisor.check_storage(tmp_path, {}, 0)


def test_optional_reuse_phase_only_runs_its_frozen_variant(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        workstation.Protocol,
        "finite",
        lambda _r, s, execute: calls.append((s, execute)),
    )
    monkeypatch.setattr(
        workstation.Protocol,
        "freeze",
        lambda _r, execute: calls.append(("freeze", execute)),
    )
    assert (
        workstation.main(["--campaign-root", str(tmp_path), "--phase", "reuse-v1"]) == 0
    )
    assert calls == [(s, False) for s in (*workstation.PREFREEZE, "freeze")] + [
        ("reuse-v1", True)
    ]
