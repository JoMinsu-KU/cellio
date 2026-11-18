"""Verify Cell A extension, release inventory and clean physical reproduction evidence."""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

from cellio.artifacts import verify_artifact
from cellio.io import digest, file_hash, write_json

BASELINE = "cell-a-core-v1"
REGRESSIONS = (
    "tests/unit/test_core_contracts.py",
    "tests/unit/test_fault_contracts.py",
    "tests/unit/test_ctrl_reference.py",
    "tests/unit/test_oracle_frames.py",
    "tests/unit/test_oracle_temporal.py",
    "tests/unit/test_oracle_gold.py",
    "tests/integration/test_episode_worker.py",
    "tests/integration/test_scene_plugin.py",
)


def gate(status, sources=(), **details):
    return {"status": status, "sources": list(map(str, sources)), "details": details}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def git(root, *args, check=True):
    p = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
    return p.stdout.strip()


def category(path):
    if "controllers" in path:
        return "FSM"
    if any(s in path for s in ("presets", "examples", "protocols")):
        return "requirements / configuration"
    if "/scenes/" in path or "/adapters/" in path:
        return "geometry / mapper / adapter"
    if path.startswith("src/cellio/core/"):
        return "shared core"
    if path.startswith("tests/"):
        return "regression"
    return "other development"


def concrete_references(source):
    """Flag newly added concrete-cell branches/scene imports in the reusable engine."""
    tree = ast.parse(source)
    out = Counter()
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp)):
            expr = ast.unparse(node.test)
            if re.search(r"['\"]cell_[ab]['\"]", expr):
                out["branch " + expr] += 1
        elif isinstance(node, ast.Match):
            for case in node.cases:
                expr = ast.unparse(case.pattern)
                if re.search(r"['\"]cell_[ab]['\"]", expr):
                    out["match " + expr] += 1
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            expr = ast.unparse(node)
            if "cellio.scenes." in expr or "cellio.adapters." in expr:
                out["import " + expr] += 1
    return out


def audit_reuse(root, folder):
    root = Path(root).resolve()
    if not (root / ".git").exists():
        return gate("NOT_RUN", reason="source history and Cell A baseline tag required")
    base = git(
        root, "rev-parse", "--verify", f"refs/tags/{BASELINE}^{{commit}}", check=False
    )
    head = git(root, "rev-parse", "HEAD")
    if not re.fullmatch("[0-9a-f]{40}", base) or head == base:
        return gate(
            "NOT_RUN",
            reason="tagged Cell A baseline and subsequent Cell B commit required",
            head=head,
        )
    if git(root, "merge-base", base, head) != base:
        return gate("FAIL", reason="Cell A baseline is outside the candidate history")
    tracked_b = git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        head,
        "src/cellio/controllers/stages_b.py",
        "src/cellio/scenes/cell_b.py",
    )
    if len(tracked_b.splitlines()) < 2:
        return gate("NOT_RUN", reason="Cell B is not present in the candidate commit")
    changed = git(root, "diff", "--name-only", base, head).splitlines()
    classified = {path: category(path) for path in changed}
    branches = {}
    for path in changed:
        if not path.endswith(".py") or not path.startswith("src/cellio/core/"):
            continue
        if path.endswith(("/fixtures.py", "/presets.py")):
            continue
        before = git(root, "show", base + ":" + path, check=False)
        after = git(root, "show", head + ":" + path, check=False)
        added = concrete_references(after) - concrete_references(before)
        if added:
            branches[path] = dict(added)
    details = {
        "baseline_tag": BASELINE,
        "baseline_commit": base,
        "candidate_commit": head,
        "changed_files": classified,
        "introduced_scene_specific_core_branches": branches,
        "authoring_hours": None,
        "authoring_hours_reason": "No contemporaneous time record separates scene integration from other development",
    }
    if branches:
        return gate(
            "FAIL", **details, reason="new concrete-cell logic in reusable engine"
        )
    present = set(
        git(root, "ls-tree", "-r", "--name-only", head, *REGRESSIONS).splitlines()
    )
    missing = [p for p in REGRESSIONS if p not in present]
    if missing:
        return gate("NOT_RUN", **details, missing=missing)
    out = Path(folder) / "release-evidence" / "reuse"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as scratch:
        junit = out / "regression.xml"
        checkout = Path(scratch) / "candidate"
        archive = Path(scratch) / "candidate.zip"
        git(root, "archive", "--format=zip", "--output", str(archive.resolve()), head)
        with zipfile.ZipFile(archive) as z:
            z.extractall(checkout)
        test_env = {
            **os.environ,
            "PYTHONPATH": str((checkout / "src").resolve()),
            "PYTHONNOUSERSITE": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *REGRESSIONS,
                "--basetemp",
                str((Path(scratch) / "pytest").resolve()),
                "--junitxml",
                str((Path(scratch) / "results.xml").resolve()),
            ],
            cwd=checkout,
            env=test_env,
            capture_output=True,
            check=False,
            text=True,
            errors="replace",
        )
        raw = Path(scratch) / "results.xml"
        cases = ET.parse(raw).findall(".//testcase") if raw.is_file() else []
        if raw.is_file():
            from cellio.io import atomic_write

            atomic_write(junit, raw.read_bytes())
        clean_tests = (
            bool(cases)
            and result.returncode == 0
            and not any(
                c.find(n) is not None
                for c in cases
                for n in ("failure", "error", "skipped")
            )
        )
    details.update(
        test_count=len(cases),
        junit_sha256=file_hash(junit) if cases else None,
        command=[sys.executable, "-m", "pytest", "-q", *REGRESSIONS],
        tested_tree=git(root, "rev-parse", head + "^{tree}"),
        diagnostics=result.stdout[-1600:] if not clean_tests else None,
    )
    proof = gate("PASS" if clean_tests else "FAIL", (junit,), **details)
    write_json(out / "scene_reuse.json", proof)
    return proof


def raw_index(campaign, enforce_physics=True):
    from cellio.analysis.campaign import (
        attempt_histories,
        lines,
        safe_child,
        verify_plan_inputs,
    )
    from cellio.runtime.campaign import read_plan

    plan = read_plan(campaign / "campaign.plan.json")
    if plan["suite_id"] != "canonical-v1" or len(plan["runs"]) != 820:
        raise ValueError("release raw evidence requires the frozen 820-run campaign")
    histories = attempt_histories(
        lines(campaign / "ledger.jsonl"), {r["run_id"] for r in plan["runs"]}
    )
    rows = []
    for row in plan["runs"]:
        history = histories[row["run_id"]]
        if (
            not history
            or history[0][-1]["state"] != "COMPLETED"
            or history[0][-1].get("error")
        ):
            raise ValueError("Missing completed primary attempt in release ledger")
        ident = history[0][0]["attempt_id"]
        path = safe_child(campaign, row["run_id"], ident)
        verified = verify_artifact(path)
        verify_plan_inputs(path, row, plan["provenance"])
        result = read(path / "result.json")
        if (
            enforce_physics
            and result.get("backend") != "isaac62"
            or verified["completion_status"] != "COMPLETED"
            or verified["ticks"] != 7200
            or row["run_id"] != verified["run_id"]
            or ident != verified["attempt_id"]
        ):
            raise ValueError(
                "release raw evidence is incomplete or uses a different configuration"
            )
        rows.append(
            {
                "run_id": row["run_id"],
                "attempt_id": verified["attempt_id"],
                "manifest_final_sha256": file_hash(path / "manifest.final.json"),
                "trace_chain_sha256": verified["artifact_digest"],
            }
        )
    return {
        "suite_id": plan["suite_id"],
        "plan_hash": plan["plan_hash"],
        "attempts": rows,
    }


def audit_release(root, folder):
    root, folder = Path(root).resolve(), Path(folder).resolve()
    candidates = (
        folder / "release-evidence" / "release_manifest.json",
        root / "results" / "release_manifest.json",
    )
    path = next((f for f in candidates if f.is_file()), None)
    if path is None:
        return gate(
            "NOT_RUN",
            required_evidence="versioned release manifest with source tag, package lock, assets, and raw archive checksums",
        )
    manifest = read(path)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    tag = manifest.get("tag", "")
    if (
        not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag)
        or tag.removeprefix("v") != version
    ):
        return gate("FAIL", (path,), reason="release tag and package version differ")
    commit = git(
        root, "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}", check=False
    )
    if (
        not commit
        or commit != manifest.get("source_commit")
        or commit != git(root, "rev-parse", "HEAD")
    ):
        return gate(
            "FAIL",
            (path,),
            reason="source tag does not identify this candidate",
            tag=tag,
        )
    required = {
        "LICENSE.txt",
        "THIRD_PARTY_NOTICES.md",
        "README.md",
        "docs/installation.md",
        "docs/quickstart_cell_a.md",
        "docs/quickstart_cell_b.md",
        "docs/limitations.md",
        "docs/add_a_cell.md",
        "fixtures/oracle/gold.json",
        "scripts/reproduce_clean_install.py",
    }
    inventory = manifest.get("source_files", {})
    if not required.issubset(inventory):
        return gate("FAIL", (path,), missing=sorted(required - inventory.keys()))
    for relative, stated in inventory.items():
        target = (root / relative).resolve()
        if (
            not target.is_relative_to(root)
            or target.is_symlink()
            or file_hash(target) != stated
        ):
            return gate(
                "FAIL", (path,), reason="source file checksum differs", file=relative
            )
    from cellio.adapters.isaac62.assets import verify_assets
    from cellio.environment import data_root

    assets = verify_assets(data_root() / "assets")
    if (
        digest(assets) != manifest.get("assets_digest")
        or not assets.get("source")
        or not assets.get("files")
    ):
        return gate(
            "FAIL", (path,), reason="asset lock or local dependency closure differs"
        )
    package_lock = data_root() / "locks" / "requirements.lock"
    if (
        file_hash(package_lock) != manifest.get("requirements_sha256")
        or "--hash=sha256:" not in package_lock.read_text()
    ):
        return gate("FAIL", (path,), reason="runtime dependency lock differs")
    index = raw_index(folder / "canonical-v1")
    if digest(index) != manifest.get("raw_index_sha256"):
        return gate("FAIL", (path,), reason="raw campaign inventory differs")
    # The specification permits publishing the raw data themselves. Local packaged
    # raw evidence is sufficient only when its full archived bytes can be verified.
    if "raw_archive" in manifest:
        archive = Path(manifest["raw_archive"]).resolve()
        if not archive.is_file() or file_hash(archive) != manifest.get(
            "raw_archive_sha256"
        ):
            return gate("FAIL", (path,), reason="raw archive missing or corrupt")
        if not check_archived_index(archive, index):
            return gate(
                "FAIL",
                (path, archive),
                reason="raw archive does not contain all verified attempts",
            )
    else:
        archive = Path(manifest["raw_directory"]).resolve()
        if (
            archive != (folder / "canonical-v1").resolve()
            and raw_index(archive) != index
        ):
            return gate(
                "FAIL",
                (path, archive),
                reason="versioned raw directory differs from canonical evidence",
            )
    return gate(
        "PASS",
        (path, archive, package_lock),
        version=version,
        tag=tag,
        source_commit=commit,
        raw_attempts=len(index["attempts"]),
        raw_index_sha256=digest(index),
        availability="locally verified release package",
        declared_publication_url=manifest.get("published_url"),
    )


def check_archived_index(path, index):
    import hashlib

    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            return False
        for attempt in index["attempts"]:
            prefix = attempt["run_id"] + "/" + attempt["attempt_id"] + "/"
            manifest = archive.read(prefix + "manifest.final.json")
            if hashlib.sha256(manifest).hexdigest() != attempt["manifest_final_sha256"]:
                return False
            if archive.read(prefix + "COMPLETE") != (
                attempt["manifest_final_sha256"] + "\n"
            ).encode("ascii"):
                return False
            for name, expected in json.loads(manifest)["files"].items():
                member = prefix + name
                if archive.getinfo(member).file_size != expected["size"]:
                    return False
                with archive.open(member) as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != expected["sha256"]
                    ):
                        return False
    return True


def wheel_sources(wheel, package, checkout):
    wheel_ids = {}
    with zipfile.ZipFile(wheel) as archive:
        import hashlib

        for name in archive.namelist():
            if not name.startswith("cellio/") or not name.endswith(".py"):
                continue
            relative = Path(name).relative_to("cellio")
            installed = (package / relative).resolve()
            source = (checkout / "src" / "cellio" / relative).resolve()
            if not installed.is_relative_to(package) or not source.is_relative_to(
                checkout / "src" / "cellio"
            ):
                raise ValueError("wheel source path escapes package")
            raw = archive.read(name)
            wheel_sha = hashlib.sha256(raw).hexdigest()
            if file_hash(installed) != wheel_sha or file_hash(source) != wheel_sha:
                raise ValueError(f"source, wheel or installed bytes differ for {name}")
            wheel_ids[name] = wheel_sha
    if set(wheel_ids) != {
        p.relative_to(checkout / "src").as_posix()
        for p in (checkout / "src" / "cellio").rglob("*.py")
    }:
        raise ValueError("wheel code inventory differs from checkout")
    return wheel_ids


def audit_clean(root, folder):
    from cellio.analysis.campaign import load_campaign

    directory = Path(folder).resolve() / "release-evidence" / "clean-install"
    proof_file = directory / "reproduction.json"
    if not proof_file.is_file():
        return gate(
            "NOT_RUN",
            required_evidence="execute scripts/reproduce_clean_install.py on the physical Windows profile",
        )
    proof = read(proof_file)
    root = Path(root).resolve()
    work, prefix = (
        Path(proof["working_directory"]).resolve(),
        Path(proof["prefix"]).resolve(),
    )
    if (
        work.is_relative_to(root)
        or not prefix.is_relative_to(work)
        or proof.get("source_commit") != git(root, "rev-parse", "HEAD")
    ):
        return gate(
            "FAIL",
            (proof_file,),
            reason="reproduction has a different candidate or a reused checkout",
        )
    commands = proof.get("commands", [])
    keys = [item["key"] for item in commands]
    required = [
        "create_env",
        "torch",
        "isaac",
        "dependencies",
        "build",
        "install",
        "runtime_identity",
        "doctor",
        "assets",
        "plan",
        "run",
    ]
    for item in commands:
        log = (directory / item["log"]).resolve()
        if (
            not log.is_relative_to(directory)
            or file_hash(log) != item["log_sha256"]
            or item["exit_code"] != 0
            or Path(item["cwd"]).resolve() != work
        ):
            return gate(
                "FAIL",
                (proof_file, log),
                reason="command failed or recorded bytes/cwd differ",
                command=item["key"],
            )
    if keys != required:
        return gate(
            "NOT_RUN",
            (proof_file,),
            reason="incomplete clean-environment command sequence",
            completed=keys,
        )
    identity = read(directory / "runtime.json")
    exe = Path(identity["executable"]).resolve()
    pkg = Path(identity["package"]).resolve().parent
    if (
        Path(identity["prefix"]).resolve() != prefix
        or not exe.is_relative_to(prefix)
        or not pkg.is_relative_to(prefix)
        or identity["os"] != "Windows"
        or not identity["python"].startswith("3.12.")
        or identity["versions"].get("isaacsim") != "6.2.0.0"
    ):
        return gate(
            "FAIL",
            (proof_file,),
            reason="isolated Python and Isaac runtime identities differ",
        )
    if any(Path(c["argv"][0]).resolve() != exe for c in commands[1:]):
        return gate(
            "FAIL", (proof_file,), reason="a recorded command used another interpreter"
        )
    check_arguments = {
        "create_env": ("create", "--prefix", str(prefix)),
        "dependencies": ("--require-hashes", "-r"),
        "install": ("pip", "install", "--no-deps"),
        "assets": ("assets", "verify", "--offline"),
        "doctor": ("doctor", "--profile", "isaac62", "smoke"),
        "plan": ("plan", "--suite", "clean-install"),
        "run": ("campaign", "run", "--plan"),
    }
    for c in commands:
        if not all(arg in c["argv"] for arg in check_arguments.get(c["key"], ())):
            return gate(
                "FAIL",
                (proof_file,),
                reason="recorded command does not match its operation",
                command=c["key"],
            )
    lock = Path(proof["requirements_lock"])
    if file_hash(lock) != proof["requirements_sha256"]:
        return gate("FAIL", (proof_file,), reason="requirements lock changed")
    wheel = Path(proof["wheel"])
    if (
        not wheel.resolve().is_relative_to(work)
        or file_hash(wheel) != proof["wheel_sha256"]
    ):
        return gate("FAIL", (proof_file,), reason="wheel location or digest differs")
    ids = wheel_sources(wheel, pkg, root)
    if read(directory / "doctor.json").get("passed") is not True:
        return gate(
            "FAIL", (proof_file,), reason="clean-environment Isaac doctor failed"
        )
    campaign = Path(proof["campaign"]).resolve()
    if campaign != Path(folder).resolve() / "clean-install":
        return gate(
            "FAIL", (proof_file,), reason="clean-install campaign path is inconsistent"
        )
    plan, trials, _ = load_campaign(campaign)
    if plan["provenance"]["code_hash"] != identity.get("source_hash"):
        return gate(
            "FAIL",
            (proof_file,),
            reason="campaign code identity differs from installed package",
        )
    cells = Counter()
    for t in trials:
        raw = read(t.path / "result.json") if t.path and t.eval else {}
        if (
            t.row["arm"] != "healthy"
            or t.row["controller_id"] != "reference"
            or not t.started
            or t.status != "COMPLETED"
            or t.outcome != "SUCCESS"
            or t.eval.get("compliance_verdict") != "PASS"
            or t.eval.get("online_offline_equal") is not True
            or t.eval.get("end_step") != 7200
            or raw.get("backend") != "isaac62"
            or t.row["config"]["profile"] != "isaac62_lockstep"
        ):
            return gate(
                "FAIL",
                (proof_file,),
                reason="verified full healthy quickstart did not pass",
                run_id=t.row["run_id"],
            )
        cells[t.row["cell"]] += 1
    if plan["suite_id"] != "clean-install" or dict(cells) != {"cell_a": 5, "cell_b": 5}:
        return gate(
            "FAIL",
            (proof_file,),
            reason="both five-run quickstarts are required",
            cells=dict(cells),
        )
    return gate(
        "PASS",
        (proof_file, campaign / "campaign.plan.json", wheel),
        cells=dict(cells),
        wheel_source_files=len(ids),
        working_directory=str(work),
        source_commit=proof["source_commit"],
        attempt_ids=[t.attempt_id for t in trials],
    )


def release_gates(checkout_root, campaign_root):
    gates = {}
    for label, check in (
        ("G12", audit_reuse),
        ("G13", audit_release),
        ("G14", audit_clean),
    ):
        try:
            gates[label] = check(checkout_root, campaign_root)
        except (
            OSError,
            ValueError,
            KeyError,
            subprocess.CalledProcessError,
            zipfile.BadZipFile,
        ) as exc:
            gates[label] = gate("FAIL", reason=f"invalid or incomplete evidence: {exc}")
    return gates


def build_release_manifest(root, folder, tag, raw_directory, output):
    from cellio.adapters.isaac62.assets import verify_assets
    from cellio.environment import data_root

    root, folder, raw = (
        Path(root).resolve(),
        Path(folder).resolve(),
        Path(raw_directory).resolve(),
    )
    if not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("supply a final semantic version tag")
    source_commit = git(root, "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}")
    if git(root, "rev-parse", "HEAD") != source_commit:
        raise ValueError("candidate checkout must match the release tag")
    source_files = git(root, "ls-tree", "-r", "--name-only", source_commit).splitlines()
    inventory = {
        name: file_hash(root / name)
        for name in source_files
        if name.startswith(
            ("docs/", "scripts/", "examples/", "protocols/", "schemas/", "fixtures/")
        )
        or name
        in (
            "README.md",
            "LICENSE.txt",
            "THIRD_PARTY_NOTICES.md",
            "CITATION.cff",
            "pyproject.toml",
        )
    }
    index = raw_index(raw)
    manifest = {
        "schema_version": "cellio.release_manifest/1.0",
        "tag": tag,
        "source_commit": source_commit,
        "source_files": inventory,
        "assets_digest": digest(verify_assets(data_root() / "assets")),
        "requirements_sha256": file_hash(data_root() / "locks" / "requirements.lock"),
        "raw_directory": str(raw),
        "raw_index_sha256": digest(index),
    }
    write_json(Path(output).with_name("raw_index.json"), index)
    write_json(Path(output), manifest)
    return manifest


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("manifest", "reuse", "verify"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--raw-directory", type=Path)
    args = parser.parse_args()
    if args.operation == "manifest":
        if not args.tag:
            parser.error("manifest requires --tag")
        out = args.campaign_root / "release-evidence" / "release_manifest.json"
        build_release_manifest(
            args.checkout,
            args.campaign_root,
            args.tag,
            args.raw_directory or args.campaign_root / "canonical-v1",
            out,
        )
        print(out)
    elif args.operation == "reuse":
        result = audit_reuse(args.checkout, args.campaign_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "PASS" else 6
    else:
        result = release_gates(args.checkout, args.campaign_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if all(g["status"] == "PASS" for g in result.values()) else 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
