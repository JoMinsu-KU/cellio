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
        for state in ("RUNNING", "COMPLETED"):
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
