import hashlib
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
