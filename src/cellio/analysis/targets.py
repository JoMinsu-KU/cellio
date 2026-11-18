"""Assess the specification's recommended targets on verified first attempts."""

from operator import eq, ge, le

from cellio.analysis.comparisons import mutant_pairs
from cellio.analysis.statistics import quantile
from cellio.analysis.suites import performance
from cellio.plugins import scene_plugin


def target_readout(main, benchmark, reset, pairs, soak):
    rows = []

    def add(name, population, suite, n, value, sign, threshold, unit):
        assessment = "NOT_MEASURED"
        if value is not None:
            assessment = (
                "MEETS_TARGET"
                if {"<=": le, ">=": ge, "==": eq}[sign](value, threshold)
                else "MISSES_TARGET"
            )
        rows.append(
            {
                "metric": name,
                "population": population,
                "suite": suite,
                "sample_count": n,
                "value": value,
                "unit": unit,
                "comparison": sign,
                "target": threshold,
                "assessment": assessment,
            }
        )

    timing = performance(benchmark)
    for cell in ("cell_a", "cell_b"):
        group = [t for t in main if t.row["cell"] == cell]
        healthy = [
            t
            for t in group
            if t.row["controller_id"] == "reference" and t.row["arm"] == "healthy"
        ]
        success = sum(t.outcome == "SUCCESS" for t in healthy)
        finished = any(t.started for t in healthy)
        add(
            "nominal_task_success",
            cell,
            "canonical-v1",
            len(healthy),
            success / len(healthy) if finished else None,
            ">=",
            0.95,
            "successes / planned",
        )
        for family in (f"F{i}" for i in range(1, 7)):
            flags = [
                t.flags()
                for t in group
                if any(f["family"] == family for f in t.row["config"]["faults"])
            ]
            activated = sum(f["N_activate"] for f in flags)
            realized = sum(f["N_realized"] for f in flags)
            add(
                "conditional_fault_realization",
                cell + "/" + family,
                "canonical-v1",
                activated,
                realized / activated if activated else None,
                ">=",
                0.98,
                "realized / activated",
            )
        for mode in ("B1", "B2"):
            population = f"{cell}.{mode}"
            metrics = timing.get(population, {})
            for key, stat, sign, threshold, unit in (
                ("critical_path_all", "p99", "<=", 3.33, "ms per four steps"),
                ("critical_path_active", "p99", "<=", 3.33, "ms per four steps"),
                ("rtf", "median", ">=", 0.5, "simulated s / wall s"),
                ("startup_wall_ns", "p95", "<=", 120, "s"),
            ):
                metric = metrics.get(key, {})
                add(
                    key + "_" + stat,
                    population,
                    "overhead-v1",
                    metric.get("denominator", 0),
                    metric.get(stat),
                    sign,
                    threshold,
                    unit,
                )
        paired = pairs.get(cell, {}).get("statistics", {})
        for stat, threshold in (("median", 10), ("p95", 20)):
            add(
                "paired_runtime_overhead_" + stat,
                cell,
                "overhead-v1",
                paired.get("denominator", 0),
                paired.get(stat),
                "<=",
                threshold,
                "%",
            )
        reloads = [
            t.performance["hard_reload_wall_ns"] / 1e9
            for t in reset
            if t.row["cell"] == cell
            and t.verified_decision
            and t.performance.get("hard_reload_wall_ns") is not None
        ]
        add(
            "hard_reload_p95",
            cell,
            "reset-v1",
            len(reloads),
            quantile(reloads, 0.95),
            "<=",
            15,
            "s",
        )
        rules = {
            r["id"]
            for r in scene_plugin(cell).requirement_presets(7200)
            if r["kind"] in ("invariant", "admission")
        }
        lags = [
            delay
            for t in group
            if t.verified_decision and t.measurement
            for rule, delay in t.measurement.first_detection_lag.items()
            if rule in rules
        ]
        add(
            "invariant_admission_detection_max",
            cell,
            "canonical-v1",
            len(lags),
            max(lags, default=None),
            "==",
            0,
            "physics steps",
        )
    mutants = mutant_pairs(main)
    measured_pairs = sum(
        pair["status"] in {"KILLED", "MUTATION_UNINFORMATIVE"}
        for pairs_for_model in mutants["models"].values() for pair in pairs_for_model
    )
    add(
        "distinct_mutants_killed",
        "six registered controllers",
        "canonical-v1",
        6,
        mutants["distinct_kill_yield"]["numerator"] if measured_pairs else None,
        ">=",
        5,
        "mutants",
    )
    rows[-1]["usable_pairs"] = measured_pairs
    for memory in ("rss", "vram"):
        trend = soak.get(memory, {})
        add(
            "memory_plateau",
            memory,
            "soak-v1",
            trend.get("count", 0),
            trend.get("delta_bytes"),
            "<=",
            trend.get("threshold_bytes"),
            "bytes between first and last 20 medians",
        )
        rows[-1]["theil_sen_bytes_per_episode"] = trend.get(
            "theil_sen_bytes_per_episode"
        )
    add(
        "third_party_quickstarts",
        "independent researchers",
        "external",
        0,
        None,
        ">=",
        1,
        "researchers",
    )
    return rows
