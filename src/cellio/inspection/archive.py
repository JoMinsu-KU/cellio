"""Seal historical Cell C observations for the common offline evaluator."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cellio.io import CellIOError, atomic_write, digest, file_hash, write_json

from .replay import extract_witness, replay_witness

SCHEMA = "cellio.inspection.archived-observations/1"
FILES = (
    "witness.json",
    "config.resolved.json",
    "manifest.start.json",
    "requirements.json",
    "result.json",
)
SOURCE_KINDS = ("historical_native", "portable_witness")


def _metadata(witness):
    # The trace has native event timestamps, and no intermediate substep observations.
    # The source finish marker is the second settled output in both native formats.
    identity = digest(witness)
    start = {
        "run_id": "archive-" + identity[:24],
        "attempt_id": "import-" + identity[:24],
        "episode_id": "source-" + identity[:24],
        "generation": 0,
    }
    metadata = {
        "config.resolved.json": {
            "profile": "archived_observations",
            "scene": {"cell_id": "cell_c"},
            "requirements": {"preset": "cell_c"},
            "episode": {"horizon_steps": witness["events"][-1]["frame"] * 4},
        },
        "manifest.start.json": start,
        # The historical controller did not run the common online oracle.
        "requirements.json": [],
        "result.json": {
            "completion_status": "COMPLETED",
            "case_verdict": "NOT_SCORED",
            "source_sha256": witness["source_sha256"],
        },
    }
    return identity, metadata


def _ensure(test, message):
    if not test:
        raise CellIOError("TRACE_INTEGRITY", message, exit_code=4)


def _read(path):
    return json.loads(path.read_bytes())


def import_archive(source, output):
    """Validate the input and publish a complete immutable observation artifact."""
    source, output = Path(source), Path(output)
    if output.exists():
        raise CellIOError("CFG_INSPECTION_OUTPUT", f"Output already exists: {output}")
    kind = "historical_native" if source.is_dir() else "portable_witness"
    if kind == "historical_native":
        # extract_witness verifies the original event/result/trace bytes and terminal poses.
        witness = extract_witness(source)
        native = _read(source / "result.json")
        _ensure(
            native["fps"] == 30
            and native["physics"]["step_hz"] == 120
            and native["physics"]["steps_per_pose"] == 4,
            "Unknown native inspection timebase",
        )
    else:
        witness = _read(source)
        replay_witness(witness)
    identity, metadata = _metadata(witness)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".cellio-import-", dir=output.parent) as temp:
        stage = Path(temp) / "attempt"
        stage.mkdir()
        for name, value in {"witness.json": witness, **metadata}.items():
            write_json(stage / name, value)
        manifest = {
            "schema_version": SCHEMA,
            "source_kind": kind,
            "artifact_digest": identity,
            "files": {name: file_hash(stage / name) for name in FILES},
        }
        write_json(stage / "manifest.final.json", manifest)
        atomic_write(
            stage / "COMPLETE",
            (file_hash(stage / "manifest.final.json") + "\n").encode("ascii"),
        )
        receipt = verify_archive(stage)
        stage.rename(output)
    return {**receipt, "artifact": str(output.resolve())}


def verify_archive(path):
    """Check the captured source identity and safety of every sealed member."""
    path = Path(path).resolve()
    for name in (*FILES, "manifest.final.json", "COMPLETE"):
        member = path / name
        _ensure(
            member.resolve() == member and not member.is_symlink(),
            f"Unsafe member: {name}",
        )
    final = path / "manifest.final.json"
    _ensure(
        (path / "COMPLETE").read_bytes() == (file_hash(final) + "\n").encode("ascii"),
        "Archived manifest differs from COMPLETE",
    )
    manifest = _read(final)
    _ensure(
        manifest["schema_version"] == SCHEMA, "Unknown archived observations schema"
    )
    _ensure(manifest["source_kind"] in SOURCE_KINDS, "Unknown archive origin")
    _ensure(set(manifest["files"]) == set(FILES), "Incomplete archive inventory")
    for name, expected in manifest["files"].items():
        _ensure(
            file_hash(path / name) == expected, f"Archive checksum mismatch: {name}"
        )
    witness = _read(path / "witness.json")
    replay_witness(witness)
    identity, metadata = _metadata(witness)
    _ensure(
        identity == manifest["artifact_digest"], "Archived source identity mismatch"
    )
    for name, expected in metadata.items():
        _ensure(
            _read(path / name) == expected,
            f"Archive metadata differs from evidence: {name}",
        )
    return {
        "valid": True,
        "run_id": metadata["manifest.start.json"]["run_id"],
        "attempt_id": metadata["manifest.start.json"]["attempt_id"],
        "completion_status": "COMPLETED",
        "records": len(witness["events"]),
        "end_step": metadata["config.resolved.json"]["episode"]["horizon_steps"],
        "artifact_digest": identity,
        "source_kind": manifest["source_kind"],
        "coverage": "recorded_events",
    }


def archived_frames(path):
    """Expose only the events actually recorded at their native physical timestamps."""
    from .framework_oracle import witness_observations

    path = Path(path)
    start = _read(path / "manifest.start.json")
    for step, state in witness_observations(_read(path / "witness.json")):
        tick = {
            "episode_id": start["episode_id"],
            "generation": start["generation"],
            "step": step,
            "hz": 120,
        }
        yield {
            "step": step,
            "tick": tick,
            "truth": {"tick": tick, "plant_state": state},
            "fault_audit": {},
            "source_events": [],
            "controller_output": None,
            "actuation": None,
            "final_snapshot": False,
        }
