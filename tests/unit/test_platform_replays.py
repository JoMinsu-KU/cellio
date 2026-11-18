import importlib.util
from pathlib import Path

import pytest

from cellio.analysis.campaign import Trial
from cellio.io import digest, file_hash, write_json
from cellio.reporting import attempt_replay
from cellio.runtime.campaign import append_line, make_row

script = Path(__file__).resolve().parents[2] / "scripts/compare_platform_replays.py"
spec = importlib.util.spec_from_file_location("compare_platform_replays", script)
replays = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replays)


def test_cross_platform_witnesses_match_sealed_first_attempt(tmp_path, monkeypatch):
    provenance = {"code_hash": digest("code"), "protocol_hash": digest("protocol")}
    row = make_row("canonical-v1", provenance, "cell_a", "healthy", 1000)
    design = {"mode": "finite"}
    plan = {
        "schema_version": "cellio.plan/1.0",
        "plan_mode": "finite",
        "suite_id": "canonical-v1",
        "suite_design": design,
        "provenance": provenance,
        "protocol_hash": provenance["protocol_hash"],
        "runs": [row],
    }
    plan["plan_hash"] = digest(plan)
    write_json(tmp_path / "campaign.plan.json", plan)
    monkeypatch.setattr(replays, "suite_design", lambda _: design)
    monkeypatch.setattr(replays, "finite_rows", lambda *args: [row])
    t = Trial(
        row,
        started=True,
        status="COMPLETED",
        attempt_id="first",
        eval={
            "source_digest": digest("trace"),
            "rules_digest": digest("rules"),
            "end_step": 7200,
            "task_outcome": "SUCCESS",
            "online_offline_equal": True,
            "requirements": [
                {"requirement_id": "r1", "verdict": "PASS", "first_witness_step": 42}
            ],
        },
    )
    for state in ("PLANNED", "RUNNING", "COMPLETED"):
        append_line(
            tmp_path / "ledger.jsonl",
            {
                "run_id": row["run_id"],
                "attempt_id": "first",
                "attempt_index": 0,
                "state": state,
            },
        )
    folder = tmp_path / row["run_id"] / "first"
    manifest = folder / "manifest.final.json"
    write_json(manifest, {"last_record_hash": t.eval["source_digest"]})
    (folder / "COMPLETE").write_bytes((file_hash(manifest) + "\n").encode("ascii"))
    headers = {k: plan[k] for k in ("plan_hash", "protocol_hash", "suite_id")}
    native = {
        **headers,
        "attempts": [attempt_replay(t)],
        "replay_environment": {
            "os": "Windows",
            "kernel": "11",
            "source_hash": provenance["code_hash"],
        },
    }
    wsl = {
        **headers,
        "attempts": [attempt_replay(t)],
        "replay_environment": {
            "os": "Linux",
            "kernel": "6.8-microsoft-standard-WSL2",
            "source_hash": provenance["code_hash"],
        },
    }
    assert replays.compare(tmp_path, native, wsl)["status"] == "PASS"
    t.eval["requirements"][0]["first_witness_step"] += 1
    wsl["attempts"] = [attempt_replay(t)]
    assert replays.compare(tmp_path, native, wsl)["differences"][0]["fields"] == [
        "witness_digest"
    ]
    wsl["attempts"] = native["attempts"]
    (folder / "COMPLETE").write_text("corrupt")
    assert replays.compare(tmp_path, native, wsl)["differences"][0]["fields"] == [
        "sealed_first_attempt"
    ]
    wsl["replay_environment"]["kernel"] = "generic"
    with pytest.raises(ValueError, match="Windows and WSL"):
        replays.compare(tmp_path, native, wsl)
