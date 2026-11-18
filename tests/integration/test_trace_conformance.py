"""Check trace integrity independently of the outer artifact file checksums."""

import gzip
import hashlib
import json

import pytest
from test_episode_worker import job

from cellio.analysis.replay import read_attempt
from cellio.artifacts import AttemptWriter, frames, verify_artifact
from cellio.artifacts.trace import encoded, iter_records
from cellio.io import CellIOError, file_hash, write_json
from cellio.plugins import scene_plugin
from cellio.runtime.episode import run_episode


def reseal(path, changed):
    manifest_path = path / "manifest.final.json"
    manifest = json.loads(manifest_path.read_bytes())
    for name in changed:
        f = path / name
        manifest["files"][name] = {"size": f.stat().st_size, "sha256": file_hash(f)}
    write_json(manifest_path, manifest)
    (path / "COMPLETE").write_text(
        file_hash(manifest_path) + "\n", encoding="ascii", newline="\n"
    )


def test_T_TRACE_01_08_10_chain_and_verdicts_from_raw_frames(tmp_path):
    dest = tmp_path / "good"
    run = run_episode(**job(dest, horizon=12))
    assert verify_artifact(dest)["completion_status"] == "COMPLETED"
    chain = "0" * 64
    for index, record in enumerate(iter_records(dest)):
        claimed = record.pop("record_hash")
        assert record["previous_hash"] == chain and record["record_index"] == index
        chain = hashlib.sha256(bytes.fromhex(chain) + encoded(record)).hexdigest()
        assert claimed == chain
    assert chain == run["artifact_digest"]
    # The oracle sees only decoded raw evidence from the trace and the selected rule preset.
    extension = scene_plugin("cell_a")
    oracle = extension.create_oracle(12)
    oracle.reset(extension.requirement_presets(12), 0)
    raw_only = tmp_path / "raw"
    raw_only.mkdir()
    (raw_only / "trace.jsonl.gz").write_bytes((dest / "trace.jsonl.gz").read_bytes())
    for frame in frames(raw_only):
        oracle.observe(frame)
    rebuilt = [
        (r.requirement_id, str(r.verdict), r.first_witness_step)
        for r in oracle.finalize(12, True)
    ]
    assert rebuilt == [
        (r["requirement_id"], r["verdict"], r["first_witness_step"])
        for r in run["requirements"]
    ]
    assert read_attempt(dest)["online_offline_equal"]


@pytest.mark.parametrize(
    "kind", ["02_delete", "03_modify", "04_reorder", "05_truncate"]
)
def test_T_TRACE_corruption_is_rejected_after_recomputing_manifest(tmp_path, kind):
    dest = tmp_path / "bad"
    run_episode(**job(dest, horizon=8))
    trace = dest / "trace.jsonl.gz"
    records = list(iter_records(dest))
    if kind == "02_delete":
        del records[3]
    elif kind == "03_modify":
        records[3]["truth"]["plant_state"]["conformance_added"] = 7
    elif kind == "04_reorder":
        records[2], records[3] = records[3], records[2]
    else:
        trace.write_bytes(trace.read_bytes()[:-11])
    if kind != "05_truncate":
        trace.write_bytes(gzip.compress(b"".join(encoded(r) + b"\n" for r in records)))
    reseal(dest, [trace.name])
    with pytest.raises(
        CellIOError, match="chain|closure|compressed|end-of-stream|end-of-file"
    ):
        verify_artifact(dest)


def test_T_TRACE_06_07_11_interrupted_commit_differentiated_from_committed_cancellation(
    tmp_path,
):
    class DiskFull(AttemptWriter):
        def _extract(self):
            raise OSError("no space left on device")

    incomplete = tmp_path / "incomplete"
    failed = run_episode(**job(incomplete, horizon=8), writer_factory=DiskFull)
    assert (
        failed["completion_status"] == "INFRA_ERROR"
        and not (incomplete / "COMPLETE").exists()
    )
    with pytest.raises(CellIOError):
        verify_artifact(incomplete)
    cancelled = tmp_path / "cancelled"
    closed = run_episode(**job(cancelled, horizon=8), cancel_requested=lambda: True)
    assert (
        closed["completion_status"] == "CANCELLED"
        and (cancelled / "COMPLETE").is_file()
    )
    assert verify_artifact(cancelled)["ticks"] == 0
    (cancelled / "COMPLETE").write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(CellIOError, match="digest"):
        verify_artifact(cancelled)


def test_T_TRACE_09_retried_attempt_preserves_failed_namespace(tmp_path):
    first = tmp_path / "first"
    run_episode(**job(first, horizon=8, attempt="first"), cancel_requested=lambda: True)
    first_files = {f.name: file_hash(f) for f in first.iterdir() if f.is_file()}
    second = tmp_path / "second"
    run_episode(**job(second, horizon=8, attempt="second"))
    assert first_files == {f.name: file_hash(f) for f in first.iterdir() if f.is_file()}
    assert verify_artifact(first)["completion_status"] == "CANCELLED"
    assert verify_artifact(second)["completion_status"] == "COMPLETED"
    assert {r["attempt_id"] for r in iter_records(first)} == {"first"}
    assert {r["attempt_id"] for r in iter_records(second)} == {"second"}
