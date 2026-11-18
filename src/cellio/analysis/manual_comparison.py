"""Match the raw evidence and independently scored results of a manual comparison."""

from itertools import zip_longest

from cellio.analysis.replay import read, read_attempt
from cellio.artifacts import frames


def normalized(frame):
    observed = {
        k: tuple(
            v.get(field)
            for field in (
                "dtype",
                "value",
                "quality",
                "source_step",
                "source_seq",
                "publish_step",
                "deliver_step",
                "transport_seq",
            )
        )
        for k, v in frame["observed_signals"].items()
    }
    raw = {
        k: tuple(
            v.get(field)
            for field in (
                "dtype",
                "value",
                "quality",
                "source_step",
                "source_seq",
                "publish_step",
            )
        )
        for k, v in frame["raw_signals"].items()
    }
    commands = [
        (
            c["sequence"],
            c["actuator_id"],
            c["kind"],
            c["value"],
            c["issued_step"],
            c["operation_epoch"],
        )
        for c in frame["intents"]
    ]
    applied = [
        (
            c["command_id"].rsplit(":cmd:", 1)[-1],
            c["actuator_id"],
            c["applied_step"],
            c["status"],
            c["effective_value"],
        )
        for c in frame["applied"]
    ]
    return {"raw": raw, "observed": observed, "intents": commands, "applied": applied}


def compare(left, right):
    a, b = read_attempt(left), read_attempt(right)
    invariants = (
        "config.resolved.json",
        "initial_state.json",
        "environment.lock.json",
        "assets.lock.json",
        "protocol.lock.json",
    )
    if any(read(left / file) != read(right / file) for file in invariants):
        raise ValueError(
            "manual parity comparison requires identical input and environment locks"
        )
    one, two = read(left / "result.json"), read(right / "result.json")
    if (
        "ManualObservationFault" not in two.get("fault_implementation", "")
        or one.get("fault_implementation") == two["fault_implementation"]
    ):
        raise ValueError("distinct modular and manual implementation identity required")
    mismatch = {}
    records = 0
    volumes = [
        {
            "fields": 0,
            "raw_samples": 0,
            "observed_samples": 0,
            "intents": 0,
            "applied": 0,
        }
        for _ in range(2)
    ]
    for fa, fb in zip_longest(frames(left), frames(right)):
        if fa is None or fb is None or fa["step"] != fb["step"]:
            mismatch["boundary_count"] = records
            break
        records += 1
        for f, v in zip((fa, fb), volumes):
            v["fields"] += len(f)
            for measured, key in (
                ("raw_samples", "raw_signals"),
                ("observed_samples", "observed_signals"),
                ("intents", "intents"),
                ("applied", "applied"),
            ):
                v[measured] += len(f[key])
        ga, gb = normalized(fa), normalized(fb)
        for key in ga:
            if ga[key] != gb[key]:
                mismatch.setdefault(key, fa["step"])

    def decisions(x):
        return {
            r["requirement_id"]: (r["verdict"], r["first_witness_step"])
            for r in x["requirements"]
        }

    labels_equal = decisions(a) == decisions(b)
    exercised = all(
        r.get("faults") and r["faults"][0].get("first_effect_step") is not None
        for r in (one, two)
    )
    complete = all(
        x["completion_status"] == "COMPLETED" and x["online_offline_equal"] is True
        for x in (a, b)
    )
    status = (
        "INCOMPLETE"
        if not complete
        else "NOT_EXERCISED"
        if not exercised
        else "DIFFERENT_NOMINAL_INPUT"
        if "raw" in mismatch
        else "PASS"
        if not mismatch and labels_equal and volumes[0] == volumes[1]
        else "FAIL"
    )
    return {
        "schema_version": "cellio.manual_comparison/1.0",
        "status": status,
        "backend": one["backend"],
        "scope": "researcher-authored sensor-side scripts; shared lifecycle recording, controller, oracle, serializer and plant",
        "supported_families": ["F1", "F2", "F3", "F5"],
        "records_compared": records,
        "applied_and_observed_match": not mismatch,
        "first_difference_by_channel": mismatch,
        "labels_and_witnesses_equal": labels_equal,
        "recording_volume": volumes,
        "compressed_bytes": [
            (path / "trace.jsonl.gz").stat().st_size for path in (left, right)
        ],
        "attempts": [a["attempt_id"], b["attempt_id"]],
        "source_files": ["src/cellio/analysis/manual.py", "scripts/compare_manual.py"],
        "authoring_hours": None,
        "authoring_hours_reason": "No independently recorded development-time counter",
    }
