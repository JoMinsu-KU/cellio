"""Replay compact Cell C quality and routing witnesses without Isaac or USD."""

import hashlib
import itertools
import json
import math
import re
from pathlib import Path

SCHEMA = "cellio.inspection.witness.v1"
PARTS = ("P1", "P2")
CONDITIONS = ("normal", "stale_ok")
BINS = {"OK": (0.500, -0.190), "NG": (0.500, 0.160)}
EVENTS = (
    "infeed_index",
    "grasp",
    "release",
    "inspection",
    "grasp",
    "release",
    "output_settled",
)


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _number(value):
    _require(
        type(value) in (int, float) and math.isfinite(value),
        f"Invalid measurement: {value!r}",
    )
    return value


def _vector(value, size):
    _require(
        isinstance(value, (list, tuple)) and len(value) == size,
        f"Expected a {size}-vector",
    )
    return tuple(_number(v) for v in value)


def _read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _observed_bin(output):
    """Check the physical pose of all four bracket corners and its terminal speed."""
    x, y, z = _vector(output["position_m"], 3)
    w, a, b, c = _vector(output["orientation_wxyz"], 4)
    norm = math.sqrt(w * w + a * a + b * b + c * c)
    _require(
        abs(norm - 1) <= 2e-5, "Measured part orientation is not a unit quaternion"
    )
    w, a, b, c = (v / norm for v in (w, a, b, c))
    linear = _number(output["linear_speed_m_s"])
    angular = _number(output["angular_speed_rad_s"])
    _require(linear >= 0 and angular >= 0, "Measured speeds must be nonnegative")
    if linear >= 0.005 or angular >= 0.10:
        return "outside"
    corners = []
    for dx in (-0.062, 0.062):
        for dy in (-0.041, 0.041):
            corners.append(
                (
                    x + (1 - 2 * (b * b + c * c)) * dx + 2 * (a * b - w * c) * dy,
                    y + 2 * (a * b + w * c) * dx + (1 - 2 * (a * a + c * c)) * dy,
                    z + 2 * (a * c - w * b) * dx + 2 * (b * c + w * a) * dy,
                )
            )
    for name, (cx, cy) in BINS.items():
        if all(
            abs(px - cx) <= 0.068 and abs(py - cy) <= 0.048 and abs(pz - 0.854) < 0.0015
            for px, py, pz in corners
        ):
            return name
    return "outside"


def _sequence(events):
    # A one-place nest is cleared after the first seated output, before indexing P2.
    expected = [
        (part, name)
        for part in PARTS
        for name in (EVENTS + ("external_takeaway",) if part == PARTS[0] else EVENTS)
    ]
    _require(
        [(e["part"], e["event"]) for e in events] == expected,
        "Incomplete or reordered two-part sequence",
    )
    frames = [e["frame"] for e in events]
    _require(
        all(type(f) is int and f >= 0 for f in frames)
        and all(a < b for a, b in itertools.pairwise(frames)),
        "Invalid event frame order",
    )
    for part in PARTS:
        transfers = [
            e["station"]
            for e in events
            if e["part"] == part and e["event"] in ("grasp", "release")
        ]
        _require(
            transfers[:3] == ["infeed", "fixture", "fixture"] and transfers[-1] in BINS,
            f"{part}: incomplete station transfers",
        )


def _dimension_bin(height):
    # The machined USD vertices are single precision. Match the native drawing decision.
    return "OK" if abs(_number(height) - 33.0) <= 0.20 + 0.0001 else "NG"


def replay_witness(witness):
    """Evaluate a single two-part sequence. The condition label does not select verdicts."""
    _require(witness["schema"] == SCHEMA, "Unsupported Cell C witness schema")
    _require(witness["condition"] in CONDITIONS, "Unknown Cell C controller condition")
    hashes = witness["source_sha256"]
    _require(
        set(hashes) == {"result.json", "events.json", "motion.jsonl"}
        and all(
            isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
            for v in hashes.values()
        ),
        "Incomplete witness provenance",
    )
    events = witness["events"]
    _sequence(events)
    _require(
        [p["part"] for p in witness["parts"]] == list(PARTS), "Invalid part identities"
    )
    rows = []
    for piece in witness["parts"]:
        part = piece["part"]
        owner = piece["result_part_id"]
        _require(owner in PARTS, f"{part}: unknown consumed result ID")
        route = piece["commanded_bin"]
        route_event = next(
            e
            for e in events
            if e["part"] == part and e["event"] == "release" and e["station"] in BINS
        )
        _require(
            route in BINS and route == route_event["station"],
            f"{part}: inconsistent routing record",
        )
        expected_bin = _dimension_bin(piece["geometry_height_mm"])
        probe_bin = _dimension_bin(piece["probe_height_mm"])
        actual = _observed_bin(piece["physical_output"])
        released = _vector(piece["release_position_m"], 3)
        height_drop = 1000 * (released[2] - piece["physical_output"]["position_m"][2])
        violations = []
        if (
            probe_bin != expected_bin
            or abs(piece["geometry_height_mm"] - piece["probe_height_mm"]) > 0.020
        ):
            violations.append("MEASUREMENT_DISAGREEMENT")
        if owner != part:
            violations.append("STALE_RESULT")
        if actual != route:
            violations.append("OUTPUT_MISSED_COMMAND")
        if actual != expected_bin:
            violations.append("WRONG_DESTINATION")
        rows.append(
            {
                "part": part,
                "geometry_height_mm": piece["geometry_height_mm"],
                "probe_height_mm": piece["probe_height_mm"],
                "result_part_id": owner,
                "expected_bin": expected_bin,
                "commanded_bin": route,
                "observed_bin": actual,
                "release_to_bin_drop_mm": height_drop,
                "fresh": owner == part,
                "correct_destination": actual == expected_bin,
                "violations": violations,
            }
        )
    return {
        "condition": witness["condition"],
        "verdict": "FAIL" if any(p["violations"] for p in rows) else "PASS",
        "parts": rows,
        "source_sha256": hashes,
    }


def extract_witness(directory):
    """Derive a portable witness from the native run, binding it to the original trace bytes."""
    folder = Path(directory)
    result, result_hash = _read_json(folder / "result.json")
    events, event_hash = _read_json(folder / "events.json")
    _require(
        result["status"] == "completed" and result.get("diagnostic") is False,
        "Expected a completed Cell C run with clearance checking",
    )
    _sequence(events)
    _require(
        [p["part"] for p in result["parts"]] == list(PARTS), "Invalid native part list"
    )
    frames = {e["frame"]: e for e in events if e["event"] == "output_settled"}
    observed_parts = {}
    trace_hash = hashlib.sha256()
    count = 0
    with (folder / "motion.jsonl").open("rb") as stream:
        for count, raw in enumerate(stream, 1):
            trace_hash.update(raw)
            sample = json.loads(raw)
            _require(sample["frame"] == count - 1, "Incomplete native motion trace")
            if event := frames.get(sample["frame"]):
                part = event["part"]
                observed_parts[part] = sample["parts"][part]
    _require(
        trace_hash.hexdigest() == result["motion_trace_sha256"]
        and count == result["frames"] + 1,
        "Native motion trace count or digest mismatch",
    )
    _require(set(observed_parts) == set(PARTS), "Missing settled output samples")
    rows = []
    for row in result["parts"]:
        part = row["part"]
        inspection = next(
            e for e in events if e["event"] == "inspection" and e["part"] == part
        )
        _require(
            inspection["reference"] == row["geometry_reference"]
            and inspection["probe"] == row["probe"]
            and inspection["decision"] == row["decision"]
            and row["decision"]["part_id"] == part,
            f"{part}: disagreement between result and inspection event",
        )
        settled = row["physical_output"]
        pose = observed_parts[part]
        _require(
            pose["visibility"] == "inherited" and settled["part"] == part,
            f"{part}: unobserved output",
        )
        # q and -q represent the same physical orientation.
        q, r = _vector(pose["wxyz"], 4), _vector(settled["orientation_wxyz"], 4)
        _require(
            math.dist(_vector(pose["xyz"], 3), _vector(settled["position_m"], 3))
            <= 1e-6
            and min(math.dist(q, r), math.dist(q, [-v for v in r])) <= 2e-5,
            f"{part}: trace/output pose mismatch",
        )
        release = next(
            e
            for e in events
            if e["part"] == part and e["event"] == "release" and e["station"] in BINS
        )
        rows.append(
            {
                "part": part,
                "geometry_height_mm": row["geometry_reference"]["height_mm"],
                "probe_height_mm": row["probe"]["height_mm"],
                "result_part_id": row["decision"]["result_part_id"],
                "commanded_bin": row["decision"]["route"],
                "release_position_m": release["position_m"],
                "physical_output": {
                    k: settled[k]
                    for k in (
                        "position_m",
                        "orientation_wxyz",
                        "linear_speed_m_s",
                        "angular_speed_rad_s",
                    )
                },
            }
        )
    witness = {
        "schema": SCHEMA,
        "condition": result["condition"],
        "source_sha256": {
            "result.json": result_hash,
            "events.json": event_hash,
            "motion.jsonl": trace_hash.hexdigest(),
        },
        "events": [
            {k: e[k] for k in ("frame", "event", "part", "station") if k in e}
            for e in events
        ],
        "parts": rows,
    }
    replay_witness(witness)
    return witness
