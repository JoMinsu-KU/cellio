import json
from pathlib import Path

from cellio.analysis import compare, evaluate
from cellio.analysis.statistics import block_bootstrap, plateau, quantile, ratio
from cellio.io import digest, write_json
from cellio.reporting import oracle_corpus, report
from cellio.runtime.campaign import make_row
from cellio.runtime.episode import run_episode


def test_analysis_quantiles_blocks_and_no_denominator():
    assert quantile([0, 10], 0.95) == 9.5
    assert ratio(0, 0)["value"] is None
    data = block_bootstrap([(0, 0), (1, 1)], 1000)
    assert data == block_bootstrap([(0, 0), (1, 1)], 1000)
    assert (
        0 < data["undefined_resample_fraction"] < 1
        and data["lower_95"] == data["upper_95"] == 1
    )
    assert (
        plateau([(i, i * 100) for i in range(50)], "rss")["theil_sen_bytes_per_episode"]
        == 100
    )
    assert oracle_corpus()["exact_label_accuracy"]["numerator"] == 144


def test_verified_trace_to_report_evaluation_pair(tmp_path):
    source = tmp_path / "campaign"
    provenance = {
        k: digest(k)
        for k in ("code_hash", "environment_hash", "protocol_hash", "assets_hash")
    }
    provenance.update(
        environment_hash=digest({"backend": "mock"}),
        assets_hash=digest({}),
        protocol_hash=digest({"version": 1}),
    )
    rows = []
    for repeat in (0, 1):
        candidate = make_row("pilot-v1", provenance, "cell_a", "healthy", 1000, repeat)
        candidate["config"]["profile"] = "mock_lockstep"
        candidate["config"]["episode"]["horizon_steps"] = 12
        candidate["config_hash"] = digest(candidate["config"])
        semantic = {k: v for k, v in candidate.items() if k not in ("run_id", "config")}
        candidate["run_id"] = digest({"row": semantic, "provenance": provenance})[:24]
        rows.append(candidate)
    row = rows[0]
    plan = {
        "runs": rows,
        "suite_id": "pilot-v1",
        "schema_version": "cellio.plan/1.0",
        "provenance": provenance,
        "plan_mode": "finite",
        "protocol_hash": provenance["protocol_hash"],
    }
    plan["plan_hash"] = digest(plan)
    write_json(source / "campaign.plan.json", plan)
    path = source / row["run_id"] / "first"
    result = run_episode(
        row["config"],
        path,
        run_id=row["run_id"],
        attempt_id="first",
        initial_state=row["initial_state"],
        environment={"backend": "mock"},
        assets={},
        protocol={"version": 1},
        requirements_lock="fixture\n",
    )
    assert result["completion_status"] == "COMPLETED", result["errors"]
    with (source / "ledger.jsonl").open("w") as log:
        for state in ("PLANNED", "RUNNING", "COMPLETED"):
            print(
                json.dumps(
                    {
                        "state": state,
                        "run_id": row["run_id"],
                        "attempt_index": 0,
                        "attempt_id": "first",
                    }
                ),
                file=log,
            )
    evaluation = evaluate(path)
    assert evaluation["online_offline_equal"] is True
    assert not Path(evaluation["output"]).is_relative_to(path)
    assert compare(path, path)["task"] == [evaluation["task_outcome"]] * 2
    dest = tmp_path / "report"
    out = report(source, dest)
    assert out["counts"]["N_plan"] == 2 and out["counts"]["N_start"] == 1
    metrics = json.loads((dest / "metrics.json").read_text())
    assert metrics["overall"]["task_distribution"]["NOT_STARTED"]["numerator"] == 1
    assert metrics["overall"]["rates"]["realization_given_activation"]["value"] is None
    assert (
        metrics["performance"]["cell_a.standard"]["critical_path_all"]["denominator"]
        == 3
    )
    assert (dest / "report.html").is_file() and (
        dest / "tables" / "campaign_flow.csv"
    ).is_file()


def test_ledger_supervisor_failure_after_valid_artifact_and_parent_link(tmp_path):
    import pytest

    from cellio.analysis.campaign import load_campaign
    from cellio.io import CellIOError
    from cellio.runtime.campaign import append_line

    provenance = {
        k: digest(k)
        for k in ("code_hash", "assets_hash", "environment_hash", "protocol_hash")
    }
    provenance.update(
        environment_hash=digest({"backend": "mock"}),
        assets_hash=digest({}),
        protocol_hash=digest({"version": 1}),
    )
    row = make_row("pilot-v1", provenance, "cell_a", "healthy", 1000, 0)
    row["config"]["profile"] = "mock_lockstep"
    row["config"]["episode"]["horizon_steps"] = 12
    row["config_hash"] = digest(row["config"])
    semantic = {k: v for k, v in row.items() if k not in ("run_id", "config")}
    row["run_id"] = digest({"row": semantic, "provenance": provenance})[:24]
    plan = {
        "runs": [row],
        "suite_id": "pilot-v1",
        "schema_version": "cellio.plan/1.0",
        "provenance": provenance,
        "plan_mode": "finite",
        "protocol_hash": provenance["protocol_hash"],
    }
    plan["plan_hash"] = digest(plan)
    write_json(tmp_path / "campaign.plan.json", plan)
    ledger = tmp_path / "ledger.jsonl"
    for i, ident in enumerate(("initial", "retry")):
        result = run_episode(
            row["config"],
            tmp_path / row["run_id"] / ident,
            run_id=row["run_id"],
            attempt_id=ident,
            initial_state=row["initial_state"],
            environment={"backend": "mock"},
            assets={},
            protocol={"version": 1},
            requirements_lock="fixture\n",
        )
        assert result["completion_status"] == "COMPLETED"
        for state in ("PLANNED", "RUNNING", "INFRA_ERROR" if i == 0 else "COMPLETED"):
            e = {
                "state": state,
                "run_id": row["run_id"],
                "attempt_id": ident,
                "attempt_index": i,
                "parent_attempt_id": "initial" if i else None,
                "retry_reason": "requested" if i else None,
            }
            if state == "INFRA_ERROR":
                e["error"] = "WORKER_EXIT_ERROR"
            append_line(ledger, e)
    _, (primary,), retries = load_campaign(tmp_path)
    assert primary.eval["completion_status"] == "COMPLETED"
    assert (
        primary.status == "INFRA_ERROR"
        and primary.error == "WORKER_EXIT_ERROR"
        and not primary.evaluable
    )
    assert (
        retries[0]["status"] == "COMPLETED"
        and retries[0]["integrity"]
        and retries[0]["parent_attempt_id"] == "initial"
    )
    (tmp_path / row["run_id"] / "retry" / "COMPLETE").unlink()
    assert load_campaign(tmp_path)[2][0]["status"] == "INTEGRITY_ERROR"
    third = {
        "state": "PLANNED",
        "run_id": row["run_id"],
        "attempt_id": "bad",
        "attempt_index": 2,
        "parent_attempt_id": "initial",
        "retry_reason": "requested",
    }
    append_line(ledger, third)
    with pytest.raises(CellIOError, match="parent"):
        load_campaign(tmp_path)


def test_soak_generator_rejects_rehashed_row_from_wrong_position(tmp_path):
    import pytest

    from cellio.analysis.campaign import load_campaign
    from cellio.io import CellIOError
    from cellio.runtime.campaign import append_line, create_plan, soak_row

    provenance = {
        k: digest(k)
        for k in ("code_hash", "assets_hash", "environment_hash", "protocol_hash")
    }
    plan = create_plan("soak-v1", provenance, tmp_path)
    wrong = soak_row(provenance, 1, plan["suite_design"])
    append_line(tmp_path / "generator.rows.jsonl", {**wrong, "row_hash": digest(wrong)})
    with pytest.raises(CellIOError, match="frozen generator"):
        load_campaign(tmp_path)


def test_standalone_oracle_campaign_matches_recorded_results(tmp_path):
    from cellio.acceptance import evaluate_gold
    from cellio.runtime.campaign import create_plan

    provenance = {
        k: digest(k)
        for k in ("code_hash", "assets_hash", "environment_hash", "protocol_hash")
    }
    create_plan("oracle-v1", provenance, tmp_path)
    dest = tmp_path / "report"
    assert report(tmp_path, dest)["status"] == "NOT_RUN"
    gold = evaluate_gold()
    write_json(tmp_path / "oracle.results.json", gold)
    assert report(tmp_path, dest)["status"] == "PASS"
    assert len((dest / "tables/oracle_witnesses.csv").read_text().splitlines()) == 145
    changed = {**gold, "passed": False}
    write_json(tmp_path / "oracle.results.json", changed)
    assert report(tmp_path, dest)["status"] == "FAIL"


def test_soak_evidence_binds_elapsed_time_single_process_and_final_shutdown(tmp_path):
    from cellio.analysis.campaign import Trial
    from cellio.analysis.suites import soak_evidence

    trials = []
    ledger = []
    rows = []
    worker = tmp_path / ".workers" / "process"
    worker.mkdir(parents=True)
    base = 1000000000
    spacing = 36_000_000_000
    for i in range(211):
        ident = f"attempt{i}"
        run = f"run{i}"
        path = tmp_path / run / ident
        path.mkdir(parents=True)
        (path / "manifest.start.json").write_text(
            json.dumps({"generation": i}), encoding="utf-8"
        )
        trial = Trial(
            {
                "run_id": run,
                "chain_slot": "warmup" if i < 10 else "measurement",
                "config": {"faults": [], "episode": {"horizon_steps": 7200}},
            },
            started=True,
            status="COMPLETED",
            attempt_id=ident,
            path=path,
            eval={"task_outcome": "FAILED", "compliance_verdict": "FAIL"},
        )
        trials.append(trial)
        for offset, state in enumerate(("PLANNED", "BOOTING", "COMPLETED")):
            ledger.append(
                {
                    "attempt_id": ident,
                    "state": state,
                    "monotonic_ns": base + i * spacing + offset,
                    **({"pid": 42} if state == "BOOTING" else {}),
                }
            )
        rows.append(
            {
                "run_id": run,
                "attempt_id": ident,
                "generation": i,
                "completion_status": "COMPLETED",
            }
        )
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in ledger), encoding="utf-8"
    )
    (worker / "results.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in rows), encoding="utf-8"
    )
    write_json(worker / "process.json", {"pid": 42})
    write_json(
        worker / "result.json", {"completion_status": "COMPLETED", "episode_count": 211}
    )
    start = base + 10 * spacing - 1
    end = ledger[-1]["monotonic_ns"] + 1
    summary = {
        "completed": 201,
        "warmup": 10,
        "worker_pid": 42,
        "measurement_start_ns": start,
        "measurement_end_ns": end,
        "wall_seconds": (end - start) / 1e9,
    }
    write_json(tmp_path / "soak.result.json", summary)
    heartbeat = {"pid": 42, "state": "EXITED", "monotonic_ns": end + 5}
    write_json(worker / "heartbeat.json", heartbeat)
    assert soak_evidence(tmp_path, trials)["status"] == "PASS"
    write_json(worker / "heartbeat.json", {**heartbeat, "state": "CLOSED"})
    assert soak_evidence(tmp_path, trials)["status"] == "FAIL"
    write_json(worker / "heartbeat.json", heartbeat)
    write_json(tmp_path / "soak.result.json", {**summary, "wall_seconds": 12345})
    assert soak_evidence(tmp_path, trials)["status"] == "FAIL"


def test_unverified_scoring_does_not_count_success_except_frozen_b0_offline_evaluation():
    from cellio.analysis.campaign import Trial, group_metrics

    row = {
        "config": {"faults": [], "episode": {"horizon_steps": 12}},
        "initial_state_id": 1,
    }
    evaluation = {
        "task_outcome": "SUCCESS",
        "compliance_verdict": "PASS",
        "online_offline_equal": None,
    }
    trial = Trial(row, started=True, status="COMPLETED", eval=evaluation)
    assert trial.outcome == "UNKNOWN" and not trial.evaluable
    assert (
        group_metrics([trial])["compliance_distribution"]["UNAVAILABLE"]["numerator"]
        == 1
    )
    row["benchmark_mode"] = "B0"
    assert trial.outcome == "SUCCESS" and trial.evaluable
    evaluation["online_offline_equal"] = False
    assert trial.outcome == "UNKNOWN" and not trial.evaluable


def test_reset_chain_requires_the_actual_predecessor_and_matching_control_verdicts(
    tmp_path,
):
    from cellio.analysis.campaign import Trial
    from cellio.analysis.measurements import Measurements
    from cellio.analysis.suites import reset_chains
    from cellio.io import file_hash

    body = {
        "valid": True,
        "position_m": [0, 0, 0],
        "orientation_wxyz": [1, 0, 0, 0],
        "linear_velocity_mps": [0, 0, 0],
        "angular_velocity_rps": [0, 0, 0],
    }
    snap = {
        "complete": True,
        "bodies": {"part": body},
        "joints": {"j": {"valid": True, "position": 0, "kind": "revolute"}},
    }
    items = []
    for slot in ("control", "predecessor", "post_reset"):
        directory = tmp_path / slot
        row = {
            "chain_id": "chain",
            "chain_slot": slot,
            "test_control": {"kind": "full_h"},
            "initial_state": {"x": 0},
            "config": {"episode": {"horizon_steps": 12}, "faults": []},
        }
        ev = {
            "requirements": [
                {"requirement_id": "cell_a.r14.goal", "verdict": "PASS"},
                {
                    "requirement_id": "cell_a.r16.reset",
                    "verdict": "PASS" if slot == "post_reset" else "NOT_APPLICABLE",
                },
            ],
            "online_offline_equal": True,
            "task_outcome": "SUCCESS",
            "compliance_verdict": "PASS",
            "end_step": 12,
            "source_digest": digest(slot),
        }
        generation = int(slot == "post_reset")
        write_json(directory / "manifest.start.json", {"generation": generation})
        write_json(directory / "manifest.final.json", {"attempt_id": slot})
        write_json(directory / "initial_snapshot.json", snap)
        items.append(
            Trial(
                row, True, "COMPLETED", slot, directory, ev, measurement=Measurements()
            )
        )
    control, prior, post = items
    ref = {
        "attempt_id": "predecessor",
        "generation": 0,
        "manifest_sha256": file_hash(prior.path / "manifest.final.json"),
        "artifact_digest": prior.eval["source_digest"],
    }
    link = {
        "predecessor": ref,
        "attempt_id": "post_reset",
        "generation": 1,
        "initial_state_hash": digest(control.row["initial_state"]),
    }
    post.measurement.reset_events = [(0, link)]
    assert reset_chains(items)["chains"][0]["status"] == "PASS"
    ref["manifest_sha256"] = digest("other")
    assert reset_chains(items)["chains"][0]["status"] == "FAIL"
    ref["manifest_sha256"] = file_hash(prior.path / "manifest.final.json")
    post.eval["requirements"][0]["verdict"] = "FAIL"
    assert reset_chains(items)["chains"][0]["status"] == "FAIL"


def test_abort_reaction_uses_first_observable_packet_and_independent_deadline():
    from cellio.analysis.measurements import Measurements

    def frame(step, events=(), bad=False):
        return {
            "step": step,
            "controller_called": True,
            "observed_signals": {
                "gripper.closed": {
                    "quality": "BAD" if bad else "GOOD",
                    "dtype": "bool",
                    "value": None if bad else False,
                    "source_step": None if bad else step,
                }
            },
            "intents": [],
            "applied": [],
            "deliveries": [],
            "events": events,
            "final_snapshot": False,
        }

    def stage(name):
        return {"event_type": "controller.stage_enter", "payload": {"stage_id": name}}

    def abort(reason):
        return {"event_type": "controller.abort", "payload": {"reason": reason}}

    meter = Measurements("cell_a")
    meter.observe(frame(0, [stage("grasp")]), [])
    meter.observe(frame(4, bad=True), [])
    meter.observe(
        frame(8, [abort("INVALID_INPUT:gripper.closed"), stage("aborted")], bad=True),
        [],
    )
    meter.finish(12, {"requirements": []})
    assert meter.latencies["invalid_to_abort"] == {4: 1}
    meter = Measurements("cell_a")
    meter.observe(frame(0, [stage("descend")]), [])
    command = frame(4)
    command["intents"] = [
        {
            "kind": "POSE",
            "command_id": "pose",
            "actuator_id": "actuator.robot",
            "sequence": 1,
            "issued_step": 4,
            "value": [0, 0, 0],
        }
    ]
    meter.observe(command, [])
    receipt = frame(10)
    receipt["applied"] = [
        {
            "status": "APPLIED",
            "applied_step": 10,
            "issued_step": 4,
            "command_id": "pose",
        }
    ]
    meter.observe(receipt, [])
    meter.observe(frame(972, [abort("RESPONSE_TIMEOUT"), stage("aborted")]), [])
    meter.finish(976, {"requirements": []})
    assert meter.latencies["deadline_to_abort"] == {2: 1}
