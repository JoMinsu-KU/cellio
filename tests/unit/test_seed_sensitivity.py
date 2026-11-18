import json

from cellio import reporting
from cellio.analysis.campaign import Trial
from cellio.analysis.seed_sensitivity import canonical_seed_sensitivity
from cellio.io import digest
from cellio.runtime.campaign import finite_rows, make_row


def test_registered_canonical_cohorts_include_every_planned_row():
    plan = {"plan_hash": "synthetic-plan", "protocol_hash": "synthetic-protocol"}
    rows = finite_rows("canonical-v1", {"protocol_hash": plan["protocol_hash"]})
    result = canonical_seed_sensitivity(plan, [Trial(row) for row in rows])
    assert result["plan_hash"] == plan["plan_hash"]
    assert result["protocol_hash"] == plan["protocol_hash"]
    assert {row["run_id"] for row in result["source_rows"]} == {
        row["run_id"] for row in rows
    }
    assert len(result["source_rows"]) == 820
    for seed_range, planned, seeds, references in (
        ("1000", 68, 1, 32),
        ("1001–1019", 752, 19, 608),
    ):
        cohort = result["cohorts"][seed_range]
        counts = cohort["counts"]
        assert counts["N_plan"] == planned
        assert counts["N_distinct_seeds"] == seeds
        assert counts["N_distinct_cell_states"] == 2 * seeds
        assert {v["counts"]["N_plan"] for v in cohort["by_cell"].values()} == {
            planned // 2
        }
        assert (
            sum(
                g["counts"]["N_plan"]
                for g in cohort["by_condition"]
                if g["controller_id"] == "reference"
            )
            == references
        )
        assert counts["N_first_attempt"] == counts["N_start"] == counts["N_eval"] == 0
        assert cohort["first_attempt_statuses"] == {}
        assert cohort["task_outcomes_per_plan"] == {"NOT_STARTED": planned}
        assert cohort["task_success_per_evaluable"]["value"] is None


def test_partial_first_attempts_and_repeats_in_both_report_formats(
    tmp_path, monkeypatch
):
    plan = {"suite_id": "canonical-v1", "plan_hash": "p", "protocol_hash": "s"}
    trials = [
        Trial(
            make_row(
                "canonical-v1", {"protocol_hash": "s"}, "cell_a", "healthy", seed, r
            )
        )
        for seed, r in ((1000, 0), (1000, 1), (1001, 0), (1001, 1), (1002, 0))
    ]
    first = trials[0]
    first.attempt_id, first.started, first.status = "a0", True, "COMPLETED"
    first.eval = {
        "task_outcome": "SUCCESS",
        "compliance_verdict": "PASS",
        "online_offline_equal": True,
        "requirements": [],
        "source_digest": "sealed-a0",
    }
    first.path = tmp_path / "campaign" / first.row["run_id"] / "a0"
    trials[1].attempt_id, trials[1].started, trials[1].status = (
        "a1",
        True,
        "INFRA_ERROR",
    )
    trials[2].attempt_id, trials[2].status = "a2", "SKIPPED_PRECONDITION"
    retry = {"attempt_id": "a1-retry", "parent_attempt_id": "a1", "status": "COMPLETED"}
    monkeypatch.setattr(reporting, "load_campaign", lambda _: (plan, trials, [retry]))
    out = tmp_path / "report"
    reporting.report(tmp_path / "campaign", out)
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    result = json.loads(
        (out / "tables/seed_sensitivity.json").read_text(encoding="utf-8")
    )
    assert metrics["seed_sensitivity"] == result
    assert metrics["overall"]["counts"]["N_plan"] == 5 and metrics["retries"] == [retry]
    seen, unseen = [result["cohorts"][k] for k in ("1000", "1001–1019")]
    assert seen["counts"]["N_first_attempt"] == seen["counts"]["N_start"] == 2
    assert seen["counts"]["N_distinct_cell_states"] == seen["counts"]["N_eval"] == 1
    assert seen["task_success_per_plan"]["value"] == 0.5
    assert seen["task_success_per_evaluable"]["value"] == 1.0
    assert seen["task_success_per_evaluable"]["source_artifact_ids"] == ["a0"]
    assert unseen["counts"]["N_first_attempt"] == 1 and unseen["counts"]["N_start"] == 0
    assert unseen["evaluable_rate"]["denominator"] == 3
    assert unseen["task_success_per_evaluable"]["value"] is None
    assert result["source_rows"][0]["artifact_digest"] == "sealed-a0"
    assert result["source_rows"][0]["initial_state_hash"] == digest(
        first.row["initial_state"]
    )
    assert [r["first_attempt_id"] for r in result["source_rows"]] == [
        "a0",
        "a1",
        "a2",
        None,
        None,
    ]
    for name in ("report.md", "report.html"):
        text = (out / name).read_text(encoding="utf-8")
        for value in (
            "Secondary sensitivity",
            "1001–1019",
            "repeated observations",
            "cell_a",
            "1/2",
            "1/1",
            "Undefined (0 evaluable)",
            "tables/seed_sensitivity.json",
        ):
            assert value in text
