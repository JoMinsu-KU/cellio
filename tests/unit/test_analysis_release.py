import hashlib
import json
import zipfile

from cellio.io import canonical
from cellio.release import (
    audit_clean,
    audit_release,
    check_archived_index,
    concrete_references,
)


def test_unperformed_release_gates_do_not_pass(tmp_path):
    assert audit_clean(tmp_path, tmp_path)["status"] == "NOT_RUN"
    assert audit_release(tmp_path, tmp_path)["status"] == "NOT_RUN"
    assert concrete_references("if scene == 'cell_b':\n    f()")
    assert not concrete_references("if profile.has_fixture:\n    f()")


def test_r3_requires_every_registered_gate(tmp_path, monkeypatch):
    from cellio import acceptance

    measured = {f"G{i:02d}": {"status": "PASS"} for i in range(4, 15)}
    monkeypatch.setattr(acceptance, "_tests", lambda *args: {"status": "PASS"})
    monkeypatch.setattr(acceptance, "_campaign_gates", lambda *args: measured)
    monkeypatch.setattr(
        acceptance,
        "evaluate_gold",
        lambda: {"corpus_sha256": "test", "labels": {}, "passed": True},
    )
    report = tmp_path / "acceptance.json"
    incomplete = acceptance.assess("r3", tmp_path, report)
    assert not incomplete["passed"] and incomplete["missing"] == ["G15"]
    assert "engineering_status" not in incomplete
    measured["G15"] = {"status": "FAIL"}
    assert "engineering_status" not in acceptance.assess("r3", tmp_path, report)
    measured["G15"]["status"] = "PASS"
    assert (
        acceptance.assess("r3", tmp_path, report)["engineering_status"] == "R3_COMPLETE"
    )
    assert "engineering_status" not in acceptance.assess("r0", tmp_path, report)


def test_check_packaged_raw_bytes(tmp_path):
    data = b"the raw data"
    raw = canonical(
        {
            "files": {
                "result.json": {
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            }
        }
    )
    sha = hashlib.sha256(raw).hexdigest()
    index = {
        "attempts": [
            {"run_id": "run", "attempt_id": "attempt", "manifest_final_sha256": sha}
        ]
    }
    for changed in (False, True):
        dest = tmp_path / ("changed.zip" if changed else "archive.zip")
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("run/attempt/manifest.final.json", raw)
            z.writestr("run/attempt/COMPLETE", sha + "\n")
            z.writestr(
                "run/attempt/result.json", data if not changed else b"the bad data"
            )
        assert check_archived_index(dest, index) == (not changed)


def test_packaging_keeps_plan_and_attempt_contents(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import shutil
    from cellio.io import file_hash, write_json

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "cellio_pack_test", root / "scripts/package_raw_campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = tmp_path / "raw"
    example = root / "examples/traces/mock-36"
    manifest = json.loads((example / "manifest.final.json").read_bytes())
    run, attempt = manifest["run_id"], manifest["attempt_id"]
    shutil.copytree(example, raw / run / attempt)
    write_json(raw / "campaign.plan.json", {"runs": [{"run_id": run}]})
    row = {
        "run_id": run,
        "attempt_id": attempt,
        "attempt_index": 0,
        "parent_attempt_id": None,
        "retry_reason": None,
    }
    (raw / "ledger.jsonl").write_text(
        "".join(
            json.dumps({**row, "state": state}) + "\n"
            for state in ("PLANNED", "RUNNING", "COMPLETED")
        )
    )
    index = {
        "attempts": [
            {
                "run_id": run,
                "attempt_id": attempt,
                "manifest_final_sha256": file_hash(example / "manifest.final.json"),
            }
        ],
        "campaign_files": {
            n: file_hash(raw / n) for n in ("ledger.jsonl", "campaign.plan.json")
        },
    }
    # This tests the archive transfer with a small real record. Production raw_index enforces all 820 rows.
    monkeypatch.setattr(module, "raw_index", lambda _: index)
    monkeypatch.setattr(module, "read_plan", lambda p: json.loads(p.read_bytes()))
    target = tmp_path / "campaign.zip"
    assert module.package(raw, target) == file_hash(target)
    assert check_archived_index(target, index)
    with zipfile.ZipFile(target) as z:
        assert z.read("ledger.jsonl") == (raw / "ledger.jsonl").read_bytes()
        assert (
            z.read(f"{run}/{attempt}/trace.jsonl.gz")
            == (example / "trace.jsonl.gz").read_bytes()
        )


def test_clean_runtime_uses_frozen_marker_and_extra_dependency_graph(tmp_path):
    import pytest

    from cellio.release_runtime import frozen_constraints, verify_closure

    site = tmp_path / "Lib/site-packages"
    pkgs = {
        "torch": "2.0+cu130",
        "isaacsim": "6.2",
        "warp-lang": "1.2",
        "windows-lib": "7",
        "setuptools": "78.1",
        "pip": "26",
        "cellio": "1.0",
        "conda-libmamba-solver": "1.0",
    }
    inventory = {"python": "3.12.16", "packages": pkgs}
    constraints = frozen_constraints(inventory)
    assert set(constraints.splitlines()) == {
        "torch==2.0+cu130",
        "isaacsim==6.2",
        "warp-lang==1.2",
        "windows-lib==7",
    }

    def distribution(name, dependencies):
        directory = site / (name.replace("-", "_") + "-" + pkgs[name] + ".dist-info")
        directory.mkdir(parents=True)
        metadata = (
            "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + pkgs[name] + "\n"
        )
        (directory / "METADATA").write_text(
            metadata + "".join(f"Requires-Dist: {r}\n" for r in dependencies),
            encoding="utf-8",
        )

    distribution(
        "isaacsim",
        [
            'warp-lang>=1; extra == "all"',
            'windows-lib; sys_platform == "win32"',
            'unused; extra == "unselected"',
        ],
    )
    distribution("torch", ["setuptools<82"])
    for name in ("setuptools", "warp-lang", "windows-lib"):
        distribution(name, [])
    marker = {"sys_platform": "win32"}
    closure = verify_closure(inventory, prefix=tmp_path, marker_environment=marker)
    assert set(closure) == {
        "isaacsim",
        "torch",
        "windows-lib",
        "warp-lang",
        "setuptools",
    }
    pkgs["windows-lib"] = "8"
    with pytest.raises(ValueError, match="windows-lib"):
        verify_closure(inventory, prefix=tmp_path, marker_environment=marker)
