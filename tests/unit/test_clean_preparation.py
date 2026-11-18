import importlib.util
import os
from pathlib import Path

import pytest

from cellio.io import file_hash, write_json

path = Path(__file__).resolve().parents[2] / "scripts/reproduce_clean_install.py"
spec = importlib.util.spec_from_file_location("clean_preparation", path)
clean = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean)


def test_failed_download_retained_and_existing_physical_plan_never_restarted(tmp_path):
    out = tmp_path / "proof"
    work = tmp_path / "external"
    campaign = tmp_path / "clean-install"
    out.mkdir()
    assert clean.preparation(out, work, campaign) == (work, [])
    work.mkdir()
    original_log = out / "torch.log"
    original_log.write_text("network failure")
    proof = {
        "working_directory": str(work),
        "campaign": str(campaign),
        "commands": [
            {
                "key": "torch",
                "exit_code": 1,
                "log": original_log.name,
                "log_sha256": file_hash(original_log),
            }
        ],
    }
    write_json(out / "reproduction.json", proof)
    before = (out / "reproduction.json").read_bytes()
    fresh, [prior] = clean.preparation(out, work, campaign)
    assert fresh.parent == work and not fresh.exists()
    assert (out / prior["log"]).read_bytes() == before
    assert original_log.read_text() == "network failure"
    campaign.mkdir()
    (campaign / "ledger.jsonl").write_text("first trial")
    with pytest.raises(ValueError, match="first attempts"):
        clean.preparation(out, work, campaign)
    assert (campaign / "ledger.jsonl").read_text() == "first trial"
    campaign = tmp_path / "other-campaign"
    with pytest.raises(ValueError, match="before campaign"):
        clean.preparation(out, work, campaign)


def test_prefix_dll_search_removes_inherited_conda_environment(tmp_path):
    base = tmp_path / "conda"
    old = base / "envs" / "old"
    fresh = tmp_path / "clean" / "env"
    system = tmp_path / "system"
    env = clean.prefix_environment(
        fresh,
        tmp_path,
        {
            "PYTHONPATH": "old modules",
            "CONDA_PREFIX": str(old),
            "CONDA_PREFIX_1": str(base),
            "CONDA_EXE": str(base / "Scripts" / "conda.exe"),
            "PATH": os.pathsep.join(
                map(str, (old / "Library" / "bin", base / "Scripts", system))
            ),
        },
    )
    entries = env["PATH"].split(os.pathsep)
    assert entries[0] == env["CONDA_PREFIX"] == str(fresh)
    assert str(fresh / "Library" / "bin") in entries and str(system) in entries
    assert (
        str(old / "Library" / "bin") not in entries
        and str(base / "Scripts") not in entries
    )
    assert "CONDA_PREFIX_1" not in env and "PYTHONPATH" not in env
