from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from cellio.core.oracle.fixtures import evaluate_fixture
from cellio.io import CellIOError, digest

from .campaign import load_campaign
from .replay import read, read_attempt
from .statistics import block_bootstrap, distribution, ratio


def confusion(frozen):
    labels = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")
    matrix = {e: {a: 0 for a in labels} for e in labels}
    reasons = Counter()
    sources = []
    for case in frozen:
        actual = evaluate_fixture(case)
        expected = case["expected"]["verdict"]
        matrix[expected][actual.verdict] += 1
        reasons[actual.reason] += 1
        sources.append(case["id"])
    total = len(frozen)
    return {
        "matrix_3x3": {e: {a: matrix[e][a] for a in labels[:3]} for e in labels[:3]},
        "matrix_4x4": matrix,
        "reason_counts": dict(reasons),
        "detection_yield": ratio(
            matrix["FAIL"]["FAIL"], sum(matrix["FAIL"].values()), sources
        ),
        "false_alarm_rate": ratio(
            matrix["PASS"]["FAIL"], sum(matrix["PASS"].values()), sources
        ),
        "abstention_rate": ratio(
            sum(row["UNKNOWN"] for row in matrix.values()), total, sources
        ),
        "exact_label_accuracy": ratio(
            sum(matrix[v][v] for v in labels), total, sources
        ),
        "false_negatives": matrix["FAIL"]["PASS"],
    }


def state_key(trial):
    r = trial.row
    return (
        r["cell"],
        r["initial_state_id"],
        digest(r["initial_state"]),
        r["repeat_index"],
    )


def mutant_pairs(trials):
    references = {
        (state_key(t), t.row["arm"]): t
        for t in trials
        if t.row["controller_id"] == "reference"
    }
    target_rules = {
        "mutant.c_m1": {"r02"},
        "mutant.c_m2": {"r04"},
        "mutant.c_m3": {"r10"},
        "mutant.c_m4": {"r07", "r08", "r09", "r11", "r12"},
        "mutant.c_m5": {"r05", "r06"},
        "mutant.c_m6": {"r02"},
    }
    rows = {name: [] for name in target_rules}
    for t in trials:
        model = t.row["controller_id"]
        if model not in rows or t.row["arm"] == "healthy":
            continue
        ref = references.get((state_key(t), t.row["arm"]))
        item = {
            "reference": ref.attempt_id if ref else None,
            "candidate": t.attempt_id,
            "run_id": t.row["run_id"],
            "cell": t.row["cell"],
            "state": t.row["initial_state_id"],
            "status": "INELIGIBLE",
            "witnesses": [],
        }
        rows[model].append(item)
        if ref is None or not ref.evaluable or not t.evaluable:
            item["status"] = (
                "INFRA_ERROR"
                if any(x and x.flags()["N_error"] for x in (ref, t))
                else "INELIGIBLE"
            )
            continue
        if not all(x.flags()["N_realized"] for x in (ref, t)):
            item["status"] = "NOT_EXERCISED"
            continue
        ref_results = {r["requirement_id"]: r for r in ref.eval["requirements"]}
        for r in t.eval["requirements"]:
            ident = r["requirement_id"]
            if ident.split(".")[1] not in target_rules[model]:
                continue
            if (
                r["verdict"] == "FAIL"
                and r["first_witness_step"] is not None
                and ref_results.get(ident, {}).get("verdict")
                in ("PASS", "NOT_APPLICABLE")
            ):
                item["witnesses"].append(
                    {"requirement": ident, "step": r["first_witness_step"]}
                )
        item["status"] = "KILLED" if item["witnesses"] else "MUTATION_UNINFORMATIVE"
    killed = sum(any(r["status"] == "KILLED" for r in items) for items in rows.values())
    return {
        "models": rows,
        "distinct_kill_yield": ratio(
            killed, 6, [t.attempt_id for t in trials if t.attempt_id]
        ),
        "nominal_diagnostics": [
            {
                "mutant": t.row["controller_id"],
                "run_id": t.row["run_id"],
                "task": t.outcome,
                "compliance": t.eval.get("compliance_verdict"),
            }
            for t in trials
            if t.row["controller_id"] in rows and t.row["arm"] == "healthy"
        ],
    }


def overhead(trials):
    pairs = defaultdict(dict)
    for t in trials:
        if t.row.get("benchmark_mode"):
            pairs[state_key(t)][t.row["benchmark_mode"]] = t
    by_cell = defaultdict(list)
    for key, group in pairs.items():
        a, b = group.get("B0"), group.get("B1")
        first = (
            a.performance.get("running_wall_ns")
            if a and a.status == "COMPLETED"
            else None
        )
        second = (
            b.performance.get("running_wall_ns")
            if b and b.status == "COMPLETED"
            else None
        )
        by_cell[key[0]].append(
            {
                "initial_state": key[1],
                "repeat_index": key[-1],
                "b0": a.attempt_id if a else None,
                "b1": b.attempt_id if b else None,
                "overhead_percent": (second - first) * 100 / first
                if first and second is not None
                else None,
            }
        )
    output = {}
    for cell, rows in by_cell.items():
        available = [r for r in rows if r["overhead_percent"] is not None]
        blocks = defaultdict(list)
        for r in available:
            blocks[r["initial_state"]].append(r["overhead_percent"])
        output[cell] = {
            "pairs": rows,
            "statistics": distribution(
                [r["overhead_percent"] for r in available],
                "%",
                [v for r in available for v in (r["b0"], r["b1"])],
                {"incomplete_pair": len(rows) - len(available)},
            ),
            "paired_mean_by_state_interval": block_bootstrap(
                [(mean(v), 1) for v in blocks.values()]
            ),
        }
    return output


def compare(reference, candidate):
    a, b = Path(reference), Path(candidate)
    if (a / "COMPLETE").is_file() and (b / "COMPLETE").is_file():
        for name in ("protocol.lock.json", "initial_state.json"):
            if read(a / name) != read(b / name):
                raise CellIOError(
                    "COMPARE_INCOMPATIBLE", f"Paired artifacts differ in {name}"
                )
        ra, rb = read_attempt(a), read_attempt(b)
        left, right = (
            {r["requirement_id"]: r for r in x["requirements"]} for x in (ra, rb)
        )
        return {
            "protocol_hash": digest(read(a / "protocol.lock.json")),
            "reference": ra["attempt_id"],
            "candidate": rb["attempt_id"],
            "requirements": [
                {"id": key, "reference": left.get(key), "candidate": right.get(key)}
                for key in sorted(left.keys() | right.keys())
            ],
            "task": [ra["task_outcome"], rb["task_outcome"]],
        }
    plan_a, ta, _ = load_campaign(a)
    plan_b, tb, _ = load_campaign(b)
    if plan_a["protocol_hash"] != plan_b["protocol_hash"]:
        raise CellIOError("COMPARE_INCOMPATIBLE", "Campaign protocols differ")

    def key(t):
        return (
            *state_key(t),
            t.row["controller_id"],
            t.row["arm"],
            t.row.get("chain_id"),
            t.row.get("chain_slot"),
            t.row.get("benchmark_mode"),
        )

    amap, bmap = ({key(t): t for t in ts} for ts in (ta, tb))
    pairs = []
    cells = defaultdict(lambda: defaultdict(list))
    for k in sorted(amap.keys() | bmap.keys(), key=str):
        l, r = amap.get(k), bmap.get(k)
        value = (
            (int(r.outcome == "SUCCESS") - int(l.outcome == "SUCCESS"))
            if l and r and l.evaluable and r.evaluable
            else None
        )
        pairs.append(
            {
                "cell": k[0],
                "state": k[1],
                "reference": l.attempt_id if l else None,
                "candidate": r.attempt_id if r else None,
                "task_success_difference": value,
            }
        )
        if value is not None:
            cells[k[0]][k[1]].append(value)
    return {
        "protocol_hash": plan_a["protocol_hash"],
        "pairs": pairs,
        "within_cell_paired_success_difference": {
            c: block_bootstrap([(mean(v), 1) for v in blocks.values()])
            for c, blocks in cells.items()
        },
    }
