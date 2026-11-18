"""Public CLI and the supervisor's durable attempt ledger, using CPU-only workers."""

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from cellio.artifacts import verify_artifact
from cellio.cli import main
from cellio.io import digest, file_hash, write_json
from cellio.runtime import supervisor
from cellio.runtime.campaign import initial_state, scenario


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    source = str(Path(__file__).resolve().parents[2] / "src")
    monkeypatch.setenv("CELLIO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((source, os.environ.get("PYTHONPATH", "")))
    )


def invoke(args, capsys, expected=0):
    assert main([str(v) for v in args]) == expected
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def test_T_CLI_01_help_has_timebase_exit_codes_and_modes(capsys):
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert all(
        key in help_text
        for key in ("120", "7200", "130", "--json", "acceptance", "verify-artifact")
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "doctor", "--profile", "unused"],
        ["doctor", "--json", "--profile", "unused"],
        ["doctor", "--profile", "unused", "--json"],
        ["campaign", "--json", "plan", "--suite", "missing-suite"],
    ],
)
def test_T_CLI_02_03_04_json_usage_error_anywhere(args, capsys):
    error, stderr = invoke(args, capsys, 2)
    assert set(error) == {
        "code",
        "severity",
        "module",
        "path",
        "message",
        "suggestion",
        "run_id",
    }
    assert (
        error["severity"] == "ERROR"
        and error["code"] == "CFG_USAGE"
        and error["suggestion"]
    )
    assert error["code"] in stderr and len(stderr.splitlines()) == 1


def test_T_CLI_03_unknown_suite_checked_before_hardware(tmp_path, monkeypatch, capsys):
    from importlib import import_module

    cli = import_module("cellio.cli.main")
    before_hardware = Mock(
        side_effect=AssertionError("hardware lock opened for invalid suite")
    )
    monkeypatch.setattr(cli, "lock_context", before_hardware)
    error, _ = invoke(
        [
            "campaign",
            "plan",
            "--suite",
            "missing-suite",
            "--output",
            tmp_path / "invalid",
            "--json",
        ],
        capsys,
        2,
    )
    assert error["code"].startswith("CFG_")
    before_hardware.assert_not_called()
    assert not (tmp_path / "invalid").exists()


def test_T_CLI_03_environment_missing_evidence_and_cancellation_codes(
    tmp_path, monkeypatch, capsys
):
    from cellio import environment

    with monkeypatch.context() as m:
        m.setattr(
            environment,
            "probe_static",
            lambda profile: {
                "profile": profile,
                "problems": ["fixture missing runtime"],
            },
        )
        report, _ = invoke(
            [
                "doctor",
                "--profile",
                "isaac62",
                "--level",
                "static",
                "--output",
                tmp_path / "probe.json",
            ],
            capsys,
            3,
        )
    assert report["passed"] is False
    # R0 invokes this CLI contract. Exercise the missing-evidence routing without
    # recursively launching the enclosing pytest suite.
    from cellio import acceptance

    monkeypatch.setattr(
        acceptance, "_tests", lambda *_: {"status": "PASS", "test_ids": []}
    )
    evidence, _ = invoke(
        [
            "acceptance",
            "--level",
            "r3",
            "--campaign-root",
            tmp_path / "empty",
            "--report",
            tmp_path / "gate.json",
            "--strict",
        ],
        capsys,
        6,
    )
    assert evidence["passed"] is False and evidence["missing"]
    cfg = scenario("cell_a", profile="mock_lockstep")
    cfg["episode"]["horizon_steps"] = 20
    cfg_path = tmp_path / "cancel.json"
    write_json(cfg_path, cfg)
    original = supervisor.WorkerProcess.__init__

    def cancel(worker, directory, descriptor):
        original(worker, directory, descriptor)
        worker.cancel.touch()

    monkeypatch.setattr(supervisor.WorkerProcess, "__init__", cancel)
    proof, _ = invoke(
        ["run", "--config", cfg_path, "--headless", "--output", tmp_path / "cancelled"],
        capsys,
        130,
    )
    assert verify_artifact(Path(proof["directory"]))["completion_status"] == "CANCELLED"


def test_T_CLI_05_doctor_starts_app_before_first_kit_import(monkeypatch):
    from cellio import kit_probe
    from cellio.adapters.isaac62 import application

    calls = []
    app = object()

    def start(config):
        assert config["headless"] is True
        calls.append("app")
        return app

    monkeypatch.setattr(application, "start_application", start)
    monkeypatch.setattr(
        application,
        "release_application",
        lambda value: calls.append("closed" if value is app else "wrong"),
    )
    original = builtins.__import__

    class FirstKitImport(Exception):
        pass

    def track(name, *args, **kwargs):
        if name.partition(".")[0] in ("isaacsim", "omni", "pxr", "warp"):
            calls.append(name)
            raise FirstKitImport(name)
        return original(name, *args, **kwargs)

    with monkeypatch.context() as ctx:
        ctx.setattr(builtins, "__import__", track)
        with pytest.raises(FirstKitImport):
            kit_probe.probe("import")
    assert calls[0] == "app" and calls[-1] == "closed" and len(calls) == 3


def test_T_CLI_07_offline_commands_and_oracle_plan_without_kit(tmp_path, capsys):
    cfg = scenario("cell_a", profile="mock_lockstep")
    cfg["episode"]["horizon_steps"] = 8
    config_path = tmp_path / "mock.json"
    write_json(config_path, cfg)
    proof, _ = invoke(
        ["run", "--config", config_path, "--headless", "--output", tmp_path / "runs"],
        capsys,
    )
    assert proof["completion_status"] == "COMPLETED"
    code = """
import json,sys
from pathlib import Path
class ForbiddenKit:
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] in ('omni','pxr','isaacsim','warp'):
            raise AssertionError(fullname)
sys.meta_path.insert(0,ForbiddenKit())
from cellio.cli import main
root,artifact=sys.argv[1:]
campaign=Path(root)/'oracle'
for args in (
    ['validate','--config',str(Path(root)/'mock.json')],
    ['schema','export','--output',str(Path(root)/'schemas')],
    ['verify-artifact','--artifact',artifact],
    ['evaluate','--artifact',artifact],
    ['campaign','plan','--suite','oracle-v1','--output',str(campaign)],
    ['campaign','run','--plan',str(campaign/'campaign.plan.json')],
):
    assert main(args)==0,args
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), proof["directory"]],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    docs = [json.loads(line) for line in completed.stdout.splitlines()]
    assert (
        docs[2]["valid"]
        and docs[3]["online_offline_equal"]
        and docs[5]["cases"] == 144
        and docs[5]["passed"]
    )
    from jsonschema import Draft202012Validator
    from cellio.artifacts.trace import iter_records

    bundle = Path(proof["directory"])
    for key, records in {
        "config": [docs[0]["config"]],
        "result": [json.loads((bundle / "result.json").read_bytes())],
        "trace": iter_records(bundle),
    }.items():
        exported = json.loads(
            (tmp_path / "schemas" / f"{key}.schema.json").read_bytes()
        )
        Draft202012Validator.check_schema(exported)
        validator = Draft202012Validator(exported)
        for record in records:
            validator.validate(record)


def test_T_CLI_03_08_invalid_config_data_and_expectations(tmp_path, capsys):
    bad = tmp_path / "tagged.yaml"
    bad.write_text("!!python/tuple [1,2]", encoding="utf-8")
    error, _ = invoke(["validate", "--config", bad, "--json"], capsys, 2)
    assert error["code"] == "CFG_PARSE"
    cfg = scenario("cell_a", profile="mock_lockstep")
    cfg["episode"]["horizon_steps"] = 8
    cfg["expected"] = {"completion_status": "CANCELLED"}
    write_json(bad, cfg)
    proof, _ = invoke(
        [
            "run",
            "--config",
            bad,
            "--headless",
            "--output",
            tmp_path / "run",
            "--assert-expectations",
        ],
        capsys,
        5,
    )
    assert proof["completion_status"] == "COMPLETED" and proof["case_verdict"] == "FAIL"
    cfg["scene"]["cell_id"] = "sample_module:make_scene"
    write_json(bad, cfg)
    error, _ = invoke(["validate", "--config", bad, "--json"], capsys, 2)
    assert error["code"] == "CFG_SCHEMA"
    missing, _ = invoke(
        ["verify-artifact", "--artifact", tmp_path / "absent", "--json"], capsys, 4
    )
    assert missing["module"] == "artifacts"


def reset_plan(tmp_path, monkeypatch):
    ctx = {
        "environment": {"profile": "mock"},
        "assets": {},
        "protocol": {},
        "requirements_lock": "mock\n",
    }
    provenance = {"protocol_hash": digest(ctx["protocol"])}
    monkeypatch.setattr(
        supervisor, "lock_context", lambda *_args, **_kwargs: (ctx, provenance)
    )
    rows = []
    for slot in ("predecessor", "post_reset"):
        cfg = scenario("cell_a", profile="mock_lockstep")
        cfg["episode"].update(horizon_steps=12, reset_mode="hard_reload")
        control = {"kind": "full_h"}
        if slot == "predecessor":
            cfg["faults"] = [
                {
                    "fault_id": "registered",
                    "family": "F5",
                    "model_id": "explicit_invalid_v1",
                    "target_id": "gripper.closed",
                    "arm": {"open_step": 0, "close_step": 12},
                    "trigger": {"kind": "at_step", "at_step": 0},
                    "offset_steps": 0,
                    "duration_steps": 12,
                    "parameters": {},
                    "require_exercised": True,
                }
            ]
            control = {"kind": "fault_start_offset", "steps": 4}
        row = {
            "suite_id": "commission-v1",
            "chain_slot": slot,
            "chain_id": "fixture_chain",
            "test_control": control,
            "config_hash": digest(cfg),
            "initial_state": initial_state("cell_a", 1),
        }
        rows.append(
            {
                **row,
                "config": cfg,
                "run_id": digest({"row": row, "provenance": provenance})[:24],
            }
        )
    dest = tmp_path / "campaign"
    plan = {
        "schema_version": "cellio.plan/1.0",
        "suite_id": "commission-v1",
        "plan_mode": "finite",
        "runs": rows,
        "provenance": provenance,
    }
    path = dest / "campaign.plan.json"
    write_json(path, {**plan, "plan_hash": digest(plan)})
    return path, rows, ctx


def test_T_CLI_06_T_RUN_07_scheduled_cancel_and_success_are_skipped_on_resume(
    tmp_path, monkeypatch, capsys
):
    path, rows, _ = reset_plan(tmp_path, monkeypatch)
    invoke(["campaign", "run", "--plan", path], capsys)
    done = supervisor.history(path.parent)
    assert [
        (done[r["run_id"]][0]["state"], done[r["run_id"]][0]["reason"]) for r in rows
    ] == [("CANCELLED", "TEST_RESET_CANCEL"), ("COMPLETED", None)]
    previous = file_hash(path.parent / "ledger.jsonl")
    invoke(["campaign", "resume", "--plan", path, "--retry-failed"], capsys)
    assert file_hash(path.parent / "ledger.jsonl") == previous


def test_T_RUN_08_retry_retains_pair_lineage_and_prior_artifacts(
    tmp_path, monkeypatch, capsys
):
    path, rows, _ = reset_plan(tmp_path, monkeypatch)
    original = supervisor.job

    def bad_job(directory, row, entry, ctx):
        job = original(directory, row, entry, ctx)
        if row["chain_slot"] == "post_reset":
            job["test_control"] = {"kind": "invalid_test_fixture"}
        return job

    with monkeypatch.context() as m:
        m.setattr(supervisor, "job", bad_job)
        invoke(["campaign", "run", "--plan", path, "--json"], capsys, 4)
    first = supervisor.history(path.parent)
    for r in rows:
        assert len(first[r["run_id"]]) == 1
    predecessor = first[rows[0]["run_id"]][0]
    failed = first[rows[1]["run_id"]][0]
    assert (predecessor["state"], failed["state"]) == ("CANCELLED", "INFRA_ERROR")
    saved = path.parent / predecessor["run_id"] / predecessor["attempt_id"] / "COMPLETE"
    before = file_hash(saved)
    invoke(["campaign", "resume", "--plan", path], capsys)
    assert supervisor.history(path.parent) == first
    invoke(["campaign", "resume", "--plan", path, "--retry-failed"], capsys)
    final = supervisor.history(path.parent)
    assert file_hash(saved) == before
    for r, why in zip(rows, ("paired hard-reload replay", "requested"), strict=True):
        past, new = final[r["run_id"]]
        assert (
            past == first[r["run_id"]][0]
            and new["parent_attempt_id"] == past["attempt_id"]
        )
        assert (
            new["attempt_id"] != past["attempt_id"]
            and new["retry_reason"] == why
            and new["attempt_index"] == 1
        )


def test_group_later_failure_preserves_earlier_committed_success(tmp_path, monkeypatch):
    path, rows, ctx = reset_plan(tmp_path, monkeypatch)
    first, last = rows
    first["test_control"] = {"kind": "full_h"}
    last["test_control"] = {"kind": "invalid_test_fixture"}
    entries = [supervisor.attempt(path.parent, r, ctx) for r in rows]
    proof = supervisor.run_group(path.parent, rows, entries, ctx)
    assert [r["completion_status"] for r in proof] == ["COMPLETED", "INFRA_ERROR"]
    directory = path.parent / first["run_id"] / entries[0]["attempt_id"]
    assert verify_artifact(directory)["completion_status"] == "COMPLETED"
    history = supervisor.history(path.parent)
    assert history[first["run_id"]][0]["state"] == "COMPLETED"
    assert history[last["run_id"]][0]["state"] == "INFRA_ERROR"


def test_worker_lease_covers_the_native_exit_and_is_released_by_the_os(tmp_path):
    from cellio.runtime.campaign import exclusive
    from cellio.runtime.worker import execute_job

    lease = tmp_path / "plant"
    lease.mkdir()
    job = tmp_path / "job.json"
    write_json(job, {"plant_lease_directory": str(lease)})
    with exclusive(lease):
        assert execute_job(job) == 4
        assert (
            json.loads(job.with_suffix(".result.json").read_bytes())["reason"]
            == "RUN_PLANT_LOCKED"
        )
    code = """
from pathlib import Path
import os,sys
from cellio.adapters.isaac62 import application
from cellio.io import CellIOError
from cellio.runtime import worker
from cellio.runtime.campaign import exclusive
job,lease=map(Path,sys.argv[1:])
class NativeExit:
    def close(self,*,exit_code,wait_for_replicator):
        assert exit_code==0
        try:
            with exclusive(lease):
                os._exit(91)
        except CellIOError as exc:
            assert exc.code=='RUN_PLAN_LOCKED'
            os._exit(47)
def run(*args):
    application._app=NativeExit()
    return 0
worker._execute_job=run
worker.execute_job(job,process_boundary=True)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, str(job), str(lease)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 47, proc.stderr
    with exclusive(lease):
        assert (lease / ".campaign.lock").is_file()


def test_supervisor_keeps_progress_deadline_when_heartbeat_replacement_denies_read(monkeypatch):
    from cellio.io import CellIOError
    w=supervisor.WorkerProcess.__new__(supervisor.WorkerProcess)
    w.proc=Mock()
    w.proc.poll.return_value=None
    w.heartbeat=Mock()
    beat={"state":"RUNNING","step":3,"attempt_id":"live"}
    w.heartbeat.read_text.side_effect=[json.dumps(beat),PermissionError(13,"fixture sharing violation"),FileNotFoundError()]
    w.started=w.last_progress=w.state_at=0
    w.last_state=w.last_seen=w.running_at=None
    w.last_beat={}
    monkeypatch.setattr(supervisor.time,"monotonic",Mock(side_effect=(30.,35.,91.)))
    assert w.poll()==beat and w.poll()==beat
    assert w.running_at==w.last_progress==30.
    with pytest.raises(CellIOError,match="No physics progress"):
        w.poll()
