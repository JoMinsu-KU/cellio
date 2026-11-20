"""Archive original local Cell C traces and verify every member before publishing its checksum."""

import argparse
import json
import zipfile
from hashlib import sha256
from pathlib import Path

from run_cell_c_extension import PROTOCOL, ROOT, digest, read, write


def files(root, previous, summary_dir):
    entries = {}
    lock = read(root / "allocation.json")
    for name, h in lock["source_sha256"].items():
        path = ROOT / name
        if digest(path) != h:
            raise ValueError(
                f"Current method differs from frozen native source: {name}"
            )
        entries["methods/" + name] = path
    for filename in ("summarize_cell_c_extension.py", "package_cell_c_extension.py"):
        entries["methods/scripts/" + filename] = ROOT / "scripts" / filename
    for filename in (
        "verify_inspection.py",
        "audit_gripper_mesh.py",
        "audit_self_clearance.py",
        "verify_payload.py",
    ):
        path = ROOT / "src" / "cellio" / "inspection" / "study" / filename
        entries["methods/" + path.relative_to(ROOT).as_posix()] = path
    for name, p in (("r2", root), ("r1-startup", previous)):
        if p is None:
            continue
        entries[name + "/allocation.json"] = p / "allocation.json"
        historical = p / "frozen-inputs"
        if historical.is_dir():
            historical_lock = read(p / "allocation.json")["source_sha256"]
            for source in historical.rglob("*"):
                if source.is_file():
                    rel = source.relative_to(historical).as_posix()
                    if digest(source) != historical_lock[rel]:
                        raise ValueError(
                            f"Previous method does not match its freeze: {rel}"
                        )
                    entries[name + "/frozen-inputs/" + rel] = source
        for f in (
            "dispatch.out.log",
            "dispatch.err.log",
            "audit.out.log",
            "audit.err.log",
        ):
            if (p / f).is_file():
                entries[name + "/" + f] = p / f
        for row in read(p / "allocation.json")["rows"]:
            base = p / row["id"]
            entries[f"{name}/{row['id']}/recipe.json"] = base / "recipe.json"
            for attempt in sorted(base.glob("attempt-*")):
                if name == "r2" and not (attempt / "status.json").is_file():
                    raise ValueError(f"Unclosed native attempt: {attempt}")
                if (
                    name == "r2"
                    and read(attempt / "status.json")["status"] == "completed"
                    and not (attempt / "audit.json").is_file()
                ):
                    raise ValueError(f"Unreviewed native attempt: {attempt}")
                for member in sorted(attempt.rglob("*")):
                    if member.is_file():
                        entries[name + "/" + member.relative_to(p).as_posix()] = member
    for name in ("summary.json", "observations.jsonl"):
        entries["compact/" + name] = summary_dir / name
    return entries


def package(root, previous, summary_dir, out):
    if out.exists():
        raise FileExistsError(f"Use a new archive name: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = files(root, previous, summary_dir)
    summary = read(summary_dir / "summary.json")
    if summary["planned_runs"] != summary["started_first_attempts"]:
        raise ValueError("The extension still has unstarted registrations")
    members = {
        name: {"bytes": p.stat().st_size, "sha256": digest(p)}
        for name, p in entries.items()
    }
    manifest = {
        "schema": "cellio.cell-c-local-replay-archive/1",
        "scope": "workstation-only native evidence; generated USD may contain absolute local asset paths",
        "protocol_sha256": digest(PROTOCOL),
        "allocation_sha256": digest(root / "allocation.json"),
        "members": members,
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False).encode()
    partial = out.with_suffix(".partial")
    with zipfile.ZipFile(
        partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for name, path in sorted(entries.items()):
            archive.write(path, name)
        archive.writestr("archive-manifest.json", payload)
    with zipfile.ZipFile(partial) as archive:
        if set(archive.namelist()) != set(members) | {"archive-manifest.json"}:
            raise RuntimeError("Unexpected files in ZIP")
        for name, record in members.items():
            with archive.open(name) as f:
                h = sha256()
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if (
                h.hexdigest() != record["sha256"]
                or archive.getinfo(name).file_size != record["bytes"]
            ):
                raise RuntimeError(f"Checksum or size mismatch for {name}")
        if archive.read("archive-manifest.json") != payload:
            raise RuntimeError("ZIP manifest differs from the source index")
    partial.rename(out)
    receipt = {
        "name": out.name,
        "bytes": out.stat().st_size,
        "sha256": digest(out),
        "member_count": len(entries) + 1,
        "uncompressed_bytes": sum(r["bytes"] for r in members.values()) + len(payload),
        "manifest_sha256": sha256(payload).hexdigest(),
        "scope": manifest["scope"],
    }
    write(out.with_name(out.name + ".json"), receipt)
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--previous", type=Path)
    p.add_argument("--summary-dir", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    package(
        a.root.resolve(),
        a.previous.resolve() if a.previous else None,
        a.summary_dir.resolve(),
        a.out.resolve(),
    )
