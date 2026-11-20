"""Package and replay all first-attempt Cell C inspection witnesses without Isaac Sim."""

import argparse
import json
from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from run_cell_c_extension import PROTOCOL, schedule

from cellio.inspection.replay import extract_witness, replay_witness

DEFAULT = (
    Path(__file__).resolve().parents[1]
    / "results/publication/softwarex-extension/cell-c-witnesses.zip"
)
SCHEMA = "cellio.inspection.witness-archive/1"


def require(test, message):
    if not test:
        raise ValueError(message)


def digest(data):
    return sha256(data).hexdigest()


def encoded(value):
    return (
        json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def read(path):
    return json.loads(path.read_bytes())


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def same_part(case, row, witness_part, observed):
    """Compare independently audited values to the output of the portable replay."""
    prefix = f"{case}/{row['part']}"
    pairs = (
        (row["part"], observed["part_id"]),
        (row["geometry_height_mm"], observed["geometry_reference"]["height_mm"]),
        (row["probe_height_mm"], observed["probe"]["height_mm"]),
        (row["result_part_id"], observed["decision"]["result_part_id"]),
        (row["commanded_bin"], observed["decision"]["route"]),
        (row["expected_bin"], observed["true_bin"]),
        (row["observed_bin"], observed["physical_output"]["region"]),
        (row["fresh"], observed["decision"]["fresh"]),
        (not row["fresh"], observed["stale_result"]),
        (not row["correct_destination"], observed["physical_misroute"]),
    )
    require(
        all(a == b for a, b in pairs),
        f"{prefix}: replay differs from recorded metrology, ownership or bin",
    )
    for field, value in witness_part["physical_output"].items():
        require(
            observed["physical_output"][field] == value,
            f"{prefix}: tray pose or speed differs at {field}",
        )
    require(
        abs(row["release_to_bin_drop_mm"] - observed["release_to_tray_mm"]) < 1e-6,
        f"{prefix}: independent drop differs",
    )


def observations_by_part(path, plan):
    rows = {}
    expected = {(case["id"], p) for case in plan for p in ("P1", "P2")}
    for line in path.read_bytes().splitlines():
        rec = json.loads(line)
        case = rec["case_id"]
        key = (case, rec["part_id"])
        require(key in expected, f"Observation outside protocol: {key}")
        if rec["attempt_id"] != case + "/attempt-01":
            continue
        require(key not in rows, f"Duplicate first-attempt observation: {key}")
        rows[key] = rec
    require(rows.keys() == expected, "Missing first-attempt observation(s)")
    return rows


def observation_digest(records):
    """Independent of OS line endings and the export order."""
    return digest(encoded([records[key] for key in sorted(records)]))


def validate_case(case, witness, protocol):
    require(
        set(witness) == {"schema", "condition", "source_sha256", "events", "parts"},
        "Unexpected top-level fields in portable witness",
    )
    for event in witness["events"]:
        require(
            set(event)
            in ({"event", "frame", "part"}, {"event", "frame", "part", "station"})
            and event.get("station") in (None, "OK", "NG", "infeed", "fixture"),
            "Unexpected event data in portable witness",
        )
    for part in witness["parts"]:
        require(
            set(part)
            == {
                "part",
                "geometry_height_mm",
                "probe_height_mm",
                "result_part_id",
                "commanded_bin",
                "release_position_m",
                "physical_output",
            }
            and set(part["physical_output"])
            == {
                "position_m",
                "orientation_wxyz",
                "linear_speed_m_s",
                "angular_speed_rad_s",
            },
            "Unexpected part data in portable witness",
        )
    result = replay_witness(witness)
    cid = case["id"]
    require(
        result["condition"] == case["condition"], f"{cid}: controller label mismatch"
    )
    truth = protocol["sequences"][case["sequence"]]["truth"]
    for i, row in enumerate(result["parts"]):
        target = protocol["nominal_mm"] + case["errors_mm"][i]
        require(
            abs(row["geometry_height_mm"] - target)
            <= protocol["reference_geometry_error_limit_mm"]
            and row["expected_bin"] == truth[i],
            f"{cid}: geometry does not match the allocation",
        )
    return result


def extract_case(root, case, protocol, allocation_sha):
    cid = case["id"]
    folder = root / cid
    first = folder / "attempt-01"
    status = read(first / "status.json")
    recipe = read(folder / "recipe.json")
    require(
        status["id"] == cid
        and status["attempt"] == first.name
        and status["status"] == "completed"
        and status["exit_code"] == 0
        and status["allocation_sha256"] == allocation_sha
        and status["recipe_sha256"] == digest((folder / "recipe.json").read_bytes()),
        f"{cid}: incomplete or unlinked first attempt",
    )
    require(
        recipe == {**case, "protocol_sha256": digest(PROTOCOL.read_bytes())},
        f"{cid}: native recipe differs from the protocol",
    )
    audit = read(first / "audit.json")
    require(
        audit["case_id"] == cid
        and audit["core_verified"] is True
        and audit["geometry_verified"] is True,
        f"{cid}: missing validated native geometry or trace",
    )
    episode = first / "episode"
    require(
        read(episode / "result.json")["recipe"] == recipe,
        f"{cid}: result belongs to another recipe",
    )
    witness = extract_witness(episode)
    require(
        all(
            audit["sha256"][name] == value
            for name, value in witness["source_sha256"].items()
        ),
        f"{cid}: native source digest differs from its physical audit",
    )
    result = validate_case(case, witness, protocol)
    for actual, compact, native in zip(
        result["parts"], witness["parts"], audit["parts"], strict=True
    ):
        same_part(cid, actual, compact, native)
    return witness


def review(archive_bytes, protocol, plan, records=None):
    """Replay every allocated case and require a one-to-one archive membership."""
    names = [row["id"] + ".json" for row in plan]
    cases = {}
    identity = {}
    with ZipFile(BytesIO(archive_bytes)) as source:
        require(
            sorted(source.namelist()) == sorted(names),
            "Missing, additional or duplicate ZIP member",
        )
        for case, name in zip(plan, names, strict=True):
            cid = case["id"]
            content = source.read(name)
            witness = json.loads(content)
            result = validate_case(case, witness, protocol)
            if records is not None:
                for part, compact in zip(
                    result["parts"], witness["parts"], strict=True
                ):
                    obs = records[cid, part["part"]]
                    require(
                        all(
                            obs[key] == case[key]
                            for key in ("sequence", "placement", "condition")
                        ),
                        f"{cid}: published observation labels differ",
                    )
                    same_part(cid, part, compact, obs)
            cases[cid] = result
            identity[cid] = {
                "member_sha256": digest(content),
                "source_sha256": result["source_sha256"],
            }
    require(
        len({r["source_sha256"]["result.json"] for r in identity.values()})
        == len(plan),
        "Same native result reused for multiple allocations",
    )
    second = [case["parts"][1] for case in cases.values()]
    summary = {
        "verified": True,
        "archive_sha256": digest(archive_bytes),
        "archive_bytes": len(archive_bytes),
        "planned_cases": len(plan),
        "replayed_cases": len(cases),
        "parts": sum(len(v["parts"]) for v in cases.values()),
        "verdicts": dict(sorted(Counter(v["verdict"] for v in cases.values()).items())),
        "second_part_false_accepts": sum(
            p["expected_bin"] == "NG" and p["observed_bin"] == "OK" for p in second
        ),
        "second_part_stale_results": sum(not p["fresh"] for p in second),
        "observations_checked": len(records) if records is not None else 0,
        "replay_sha256": digest(encoded(cases)),
    }
    return summary, cases, identity


def pack(root, archive, receipt, observations, protocol, plan):
    allocation = root / "allocation.json"
    allocation_sha = digest(allocation.read_bytes())
    require(
        read(allocation)["rows"] == plan,
        "Native run allocation differs from the registered design",
    )
    memory = BytesIO()
    with ZipFile(memory, "w", compression=ZIP_STORED) as out:
        for case in plan:
            witness = extract_case(root, case, protocol, allocation_sha)
            info = ZipInfo(case["id"] + ".json", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            out.writestr(info, encoded(witness))
    data = memory.getvalue()
    summary, _, identities = review(data, protocol, plan, observations)
    manifest = {
        **summary,
        "schema": SCHEMA,
        "protocol_sha256": digest(PROTOCOL.read_bytes()),
        "allocation_sha256": allocation_sha,
        "method_sha256": {
            "package_cell_c_witnesses.py": digest(Path(__file__).read_bytes()),
            "cellio/inspection/replay.py": digest(
                Path(extract_witness.__code__.co_filename).read_bytes()
            ),
        },
        "first_attempt_observations_sha256": observation_digest(observations)
        if observations is not None
        else None,
        "attempt": "attempt-01",
        "zip_method": "STORED; fixed member metadata; protocol allocation order",
        "members": identities,
    }
    write(archive, data)
    write(
        receipt, (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("pack", "verify"):
        command = sub.add_parser(action)
        command.add_argument("archive", nargs="?", type=Path, default=DEFAULT)
        command.add_argument(
            "--receipt",
            type=Path,
            help="Companion JSON; defaults to <archive>.receipt.json",
        )
        command.add_argument(
            "--observations",
            type=Path,
            help="Check every part against the published observations JSONL",
        )
        if action == "pack":
            command.add_argument("--raw-root", type=Path, required=True)
        else:
            command.add_argument(
                "--output",
                type=Path,
                help="Write the full cross-platform replay and case decisions",
            )
    args = p.parse_args()
    receipt = args.receipt or args.archive.with_suffix(".receipt.json")
    paths = [
        v.resolve()
        for v in (
            args.archive,
            receipt,
            args.observations,
            getattr(args, "output", None),
        )
        if v
    ]
    require(
        len(paths) == len(set(paths)),
        "Archive, receipt, observations and report must be distinct files",
    )
    protocol = read(PROTOCOL)
    plan = schedule(protocol)
    records = (
        observations_by_part(args.observations, plan) if args.observations else None
    )
    if args.action == "pack":
        summary = pack(args.raw_root, args.archive, receipt, records, protocol, plan)
    else:
        summary, cases, identities = review(
            args.archive.read_bytes(), protocol, plan, records
        )
        manifest = read(receipt)
        require(
            manifest["schema"] == SCHEMA
            and manifest["attempt"] == "attempt-01"
            and manifest["protocol_sha256"] == digest(PROTOCOL.read_bytes())
            and manifest["members"] == identities,
            "Receipt does not match protocol, case provenance or ZIP members",
        )
        if (
            records is not None
            and manifest["first_attempt_observations_sha256"] is not None
        ):
            require(
                observation_digest(records)
                == manifest["first_attempt_observations_sha256"],
                "Receipt differs from the supplied first-attempt observations",
            )
        for k, v in summary.items():
            if k != "observations_checked":
                require(manifest[k] == v, f"Receipt differs from rechecked {k}")
        if args.output:
            write(
                args.output,
                (
                    json.dumps({**summary, "cases": cases}, sort_keys=True, indent=2)
                    + "\n"
                ).encode("utf-8"),
            )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
