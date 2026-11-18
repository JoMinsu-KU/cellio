"""Secondary descriptive analysis of the registered canonical scene seeds."""

from collections import Counter, defaultdict

from cellio.io import CellIOError, digest

from .statistics import ratio


def _summary(trials):
    counts = Counter()
    for trial in trials:
        counts.update(trial.flags())
    first_attempts = [t for t in trials if t.attempt_id is not None]
    evaluated = [t for t in trials if t.evaluable]
    successes = [t for t in evaluated if t.outcome == "SUCCESS"]
    return {
        "counts": {
            **counts,
            "N_plan": len(trials),
            "N_first_attempt": len(first_attempts),
            "N_start": counts["N_start"],
            "N_eval": len(evaluated),
            "N_distinct_seeds": len({t.row["initial_state_id"] for t in trials}),
            "N_distinct_cell_states": len(
                {(t.row["cell"], digest(t.row["initial_state"])) for t in trials}
            ),
        },
        "first_attempt_statuses": dict(Counter(t.status for t in first_attempts)),
        "task_outcomes_per_plan": dict(Counter(t.outcome for t in trials)),
        "evaluable_rate": ratio(
            len(evaluated), len(trials), [t.attempt_id for t in evaluated]
        ),
        "task_success_per_plan": ratio(
            counts["N_success"],
            len(trials),
            [t.attempt_id for t in trials if t.outcome == "SUCCESS"],
        ),
        "task_success_per_evaluable": ratio(
            len(successes),
            len(evaluated),
            [t.attempt_id for t in evaluated],
            dict(Counter(t.error or t.status for t in trials if not t.evaluable)),
        ),
    }


def canonical_seed_sensitivity(plan, first_trials):
    cohorts = {"1000": [], "1001–1019": []}
    sources = []
    for t in first_trials:
        row = t.row
        seed = row["initial_state_id"]
        if type(seed) is not int or not 1000 <= seed <= 1019:
            raise CellIOError(
                "RUN_PLAN_SEED", f"Unexpected canonical scene seed {seed!r}"
            )
        key = "1000" if seed == 1000 else "1001–1019"
        cohorts[key].append(t)
        sources.append(
            {
                "cohort": key,
                "run_id": row["run_id"],
                "seed": seed,
                "cell": row["cell"],
                "initial_state_hash": digest(row["initial_state"]),
                "arm": row["arm"],
                "controller_id": row["controller_id"],
                "repeat_index": row["repeat_index"],
                "first_attempt_id": t.attempt_id,
                "status": t.status,
                "started": t.started,
                "evaluable": t.evaluable,
                "outcome": t.outcome,
                "artifact_digest": t.eval.get("source_digest"),
            }
        )
    summaries = {}
    for key, trials in cohorts.items():
        cells, conditions = defaultdict(list), defaultdict(list)
        for t in trials:
            row = t.row
            cells[row["cell"]].append(t)
            conditions[row["cell"], row["controller_id"], row["arm"]].append(t)
        summaries[key] = {
            **_summary(trials),
            "by_cell": {c: _summary(ts) for c, ts in sorted(cells.items())},
            "by_condition": [
                {"cell": c, "controller_id": ctrl, "arm": a, **_summary(ts)}
                for (c, ctrl, a), ts in sorted(conditions.items())
            ],
        }
    return {
        "plan_hash": plan["plan_hash"],
        "protocol_hash": plan["protocol_hash"],
        "development_record": "docs/adr/002-gripper-commissioning.md",
        "interpretation": (
            "Developers observed scene seed 1000 while commissioning both cells. "
            "Seeds 1001–1019 were unseen during development. The primary analysis "
            "includes every registered row. This secondary analysis uses the first "
            "ledger attempt for each row and retains unstarted rows in planned denominators. "
            "Arms, controllers and repeats share the same initial state within a cell and seed. "
            "These are repeated observations. Mutants cover seeds 1000–1004 only. "
            "Compare like conditions using the cell, controller and arm breakdown."
        ),
        "cohorts": summaries,
        "source_rows": sources,
    }
