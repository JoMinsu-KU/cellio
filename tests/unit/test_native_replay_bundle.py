import gzip
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def packager(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import package_native_replays

    return package_native_replays


def test_excludes_unsealed_worker_files_and_requires_fresh_rule_lock(
    tmp_path, packager, monkeypatch
):
    case = {
        "case": "cell_a_healthy",
        "attempt_id": "trial",
        "run_id": "run",
        "trace_chain_sha256": "chain",
        "task": "SUCCESS",
        "compliance": "PASS",
        "requirement_count": 0,
    }
    checked = {
        "valid": True,
        "completion_status": "COMPLETED",
        "ticks": 7200,
        "artifact_digest": "chain",
        "attempt_id": "trial",
        "run_id": "run",
        "rule_lock_sha256": "lock",
    }
    monkeypatch.setattr(packager, "verify_artifact", lambda _: checked)
    inputs = {
        "requirements.json": [],
        "result.json": {
            "backend": "isaac62",
            "task_outcome": "SUCCESS",
            "compliance_verdict": "PASS",
        },
        "environment.lock.json": {"packages": {"cellio": "1.1.1"}},
    }
    for name, value in inputs.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "manifest.final.json").write_text(
        json.dumps({"files": dict.fromkeys(inputs)})
    )
    (tmp_path / "COMPLETE").write_text("test")
    (tmp_path / "worker.json").write_text('{"path":"local-machine-only"}')
    (tmp_path / "scene.usd").write_bytes(b"unsealed asset")
    with zipfile.ZipFile(tmp_path / "examples.zip", "w") as archive:
        packager.write_case(archive, tmp_path, case)
        assert set(archive.namelist()) == {
            f"cell_a_healthy/{name}"
            for name in [*inputs, "manifest.final.json", "COMPLETE"]
        }
        del checked["rule_lock_sha256"]
        with pytest.raises(ValueError, match="must originate from v1.1.1"):
            packager.write_case(archive, tmp_path, case)


@pytest.mark.parametrize(
    "value",
    ["C:/Users/example/run", r"D:\private", "/home/example/data", "sample@example.org"],
)
def test_checks_decompressed_samples_before_packaging(tmp_path, packager, value):
    path = tmp_path / "trace.jsonl.gz"
    with gzip.open(path, "wb") as stream:
        stream.write(json.dumps(value).encode())
    with pytest.raises(ValueError, match="Sensitive"):
        packager.audit_text(path)
