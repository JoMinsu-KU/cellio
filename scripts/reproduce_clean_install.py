"""Reproduce both five-run quickstarts in a new Conda prefix from a separate working directory."""

import argparse
import hashlib
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4


def hash_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    staged = path.with_name(f"{path.name}.{uuid4()}.partial")
    staged.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(staged, path)


def prefix_environment(prefix, data, original):
    env = dict(original)
    old = {Path(sys.prefix).resolve()}
    for k, value in list(env.items()):
        if k == "CONDA_PREFIX" or k.startswith("CONDA_PREFIX_"):
            if value:
                old.add(Path(value).resolve())
            env.pop(k)
    if env.get("CONDA_EXE"):
        old.add(Path(env["CONDA_EXE"]).resolve().parents[1])
    paths = [
        str(prefix / suffix)
        for suffix in (
            "",
            "Library/mingw-w64/bin",
            "Library/usr/bin",
            "Library/bin",
            "Scripts",
            "bin",
        )
    ]
    for entry in env.get("PATH", "").split(os.pathsep):
        if entry and not any(
            Path(entry.strip('"')).resolve().is_relative_to(p) for p in old
        ):
            paths.append(entry)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env.update(
        PATH=os.pathsep.join(paths),
        CONDA_PREFIX=str(prefix),
        CONDA_SHLVL="1",
        CONDA_DEFAULT_ENV=str(prefix),
        PYTHONNOUSERSITE="1",
        CELLIO_DATA_ROOT=str(data),
    )
    return env


def preparation(out, work, campaign):
    """Keep failed download logs and isolate each subsequent prefix from its predecessors."""
    if campaign.exists():
        raise ValueError(
            "The clean-install campaign already exists; its first attempts are preserved"
        )
    existing = out / "reproduction.json"
    if not existing.exists():
        if work.exists() or (
            out.exists() and any(p.name != ".campaign.lock" for p in out.iterdir())
        ):
            raise ValueError(
                "Select a new external work directory with a new preparation record"
            )
        return work, []
    previous = json.loads(existing.read_bytes())
    commands = previous["commands"]
    if (
        not commands
        or commands[-1]["exit_code"] == 0
        or commands[-1]["key"] not in {"create_env", "torch", "isaac", "dependencies"}
        or any(
            c["key"] not in {"create_env", "torch", "isaac", "dependencies"}
            for c in commands
        )
        or Path(previous["campaign"]).resolve() != campaign
    ):
        raise ValueError(
            "Only a recorded dependency download failure before campaign planning is recoverable"
        )
    for item in commands:
        log = (out / item["log"]).resolve()
        if not log.is_relative_to(out) or hash_file(log) != item["log_sha256"]:
            raise ValueError("Prior preparation log is missing or changed")
    ident = str(uuid4())
    if work.exists():
        if (
            work
            != Path(
                previous.get("requested_work_directory", previous["working_directory"])
            ).resolve()
        ):
            raise ValueError("Select the original external work directory or a new one")
        work = work / ("preparation-" + ident)
    archive = out / f"reproduction.preparation-{ident}.json"
    with archive.open("xb") as stream:
        stream.write(existing.read_bytes())
    return work, [
        *previous.get("prior_preparations", []),
        {"log": archive.name, "sha256": hash_file(archive)},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        required=True,
        type=Path,
        help="New directory outside the source checkout",
    )
    parser.add_argument(
        "--campaign-root",
        required=True,
        type=Path,
        help="Parent of the clean-install suite and release-evidence",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        type=Path,
        help="Existing locked local asset and protocol cache",
    )
    parser.add_argument("--conda", default="conda", help="Conda executable")
    args = parser.parse_args()
    from cellio.runtime.campaign import exclusive

    out = args.campaign_root.resolve() / "release-evidence" / "clean-install"
    out.mkdir(parents=True, exist_ok=True)
    with exclusive(out):
        reproduce(args, parser, out)


def reproduce(args, parser, out):
    root = Path(__file__).resolve().parents[1]
    work = args.work_dir.resolve()
    campaign_root = args.campaign_root.resolve()
    data = args.data_root.resolve()
    if platform.system() != "Windows":
        parser.error("Isaac 6.2 workstation profile requires Windows")
    if work.is_relative_to(root) or root.is_relative_to(work):
        parser.error("--work-dir must be outside the checkout")
    campaign = campaign_root / "clean-install"
    if campaign.exists():
        parser.error("select a campaign root without an existing clean-install suite")
    lock = data / "locks" / "requirements.lock"
    if not lock.is_file():
        parser.error("missing hashed CellIO package lock")
    inventory_path = root / "locks/windows-isaac62/python-environment.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("requirements_sha256") != hash_file(lock):
        parser.error("CellIO requirements do not match the frozen Windows lock")
    from_source = runpy.run_path(str(root / "src/cellio/release_runtime.py"))
    constraint_text = from_source["frozen_constraints"](inventory)
    exe = shutil.which(
        os.environ.get("CONDA_EXE", args.conda) if args.conda == "conda" else args.conda
    )
    if not exe:
        parser.error("Conda executable not found")
    requested = work
    work, archived = preparation(out, work, campaign)
    work.mkdir(parents=True)
    prefix = work / "env"
    wheel_dir = work / "wheelhouse"
    wheel_dir.mkdir()
    constraints = out / "runtime.constraints.txt"
    if constraints.exists() and constraints.read_bytes() != constraint_text.encode(
        "ascii"
    ):
        parser.error("Frozen constraints changed after the previous preparation")
    if not constraints.exists():
        constraints.write_bytes(constraint_text.encode("ascii"))
    env = prefix_environment(prefix, data, os.environ)
    python = str(prefix / "python.exe")
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    proof = {
        "schema_version": "cellio.clean_reproduction/1.0",
        "checkout": str(root),
        "source_commit": commit,
        "working_directory": str(work),
        "requested_work_directory": str(requested),
        "prior_preparations": archived,
        "prefix": str(prefix),
        "requirements_lock": str(lock),
        "requirements_sha256": hash_file(lock),
        "runtime_inventory_sha256": hash_file(inventory_path),
        "runtime_constraints_sha256": hash_file(constraints),
        "campaign": str(campaign),
        "commands": [],
    }

    def invoke(key, command):
        stamp = perf_counter_ns()
        started = out / (f"{uuid4()}-{key}.log")
        print("Running " + " ".join(map(str, command)), flush=True)
        with started.open("x", encoding="utf-8") as stream:
            proc = subprocess.run(
                list(map(str, command)),
                cwd=work,
                env=os.environ.copy() if key == "create_env" else env,
                check=False,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        proof["commands"].append(
            {
                "key": key,
                "argv": list(map(str, command)),
                "cwd": str(work),
                "exit_code": proc.returncode,
                "wall_ns": perf_counter_ns() - stamp,
                "log": started.name,
                "log_sha256": hash_file(started),
            }
        )
        save(out / "reproduction.json", proof)
        if proc.returncode:
            raise SystemExit(proc.returncode)

    invoke(
        "create_env",
        [
            exe,
            "create",
            "--prefix",
            prefix,
            "-c",
            "conda-forge",
            "python=" + inventory["python"],
            "pip",
            "setuptools=" + inventory["packages"]["setuptools"],
            "wheel",
            "-y",
        ],
    )
    invoke(
        "torch",
        [
            python,
            "-m",
            "pip",
            "install",
            "-c",
            constraints,
            "torch==" + inventory["packages"]["torch"],
            "--index-url",
            "https://download.pytorch.org/whl/cu130",
            "--extra-index-url",
            "https://pypi.org/simple",
        ],
    )
    invoke(
        "isaac",
        [
            python,
            "-m",
            "pip",
            "install",
            "-c",
            constraints,
            "isaacsim[all,extscache]==" + inventory["packages"]["isaacsim"],
            "--extra-index-url",
            "https://pypi.nvidia.com",
        ],
    )
    invoke(
        "dependencies", [python, "-m", "pip", "install", "--require-hashes", "-r", lock]
    )
    invoke(
        "build",
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            wheel_dir,
            root,
        ],
    )
    built = list(wheel_dir.glob("cellio-*.whl"))
    if len(built) != 1:
        raise RuntimeError("expected one freshly built CellIO wheel")
    proof.update(wheel=str(built[0]), wheel_sha256=hash_file(built[0]))
    invoke("install", [python, "-m", "pip", "install", "--no-deps", built[0]])
    identity = out / "runtime.json"
    invoke(
        "runtime_identity",
        [
            python,
            "-I",
            "-m",
            "cellio.release_runtime",
            "--inventory",
            inventory_path,
            "--output",
            identity,
        ],
    )
    proof["runtime_identity_sha256"] = hash_file(identity)
    invoke(
        "doctor",
        [
            python,
            "-I",
            "-m",
            "cellio",
            "doctor",
            "--profile",
            "isaac62",
            "--level",
            "smoke",
            "--output",
            out / "doctor.json",
        ],
    )
    invoke("assets", [python, "-I", "-m", "cellio", "assets", "verify", "--offline"])
    invoke(
        "plan",
        [
            python,
            "-I",
            "-m",
            "cellio",
            "campaign",
            "plan",
            "--suite",
            "clean-install",
            "--output",
            campaign,
        ],
    )
    invoke(
        "run",
        [
            python,
            "-I",
            "-m",
            "cellio",
            "campaign",
            "run",
            "--plan",
            campaign / "campaign.plan.json",
        ],
    )
    print(f"Completed reproduction log at {out / 'reproduction.json'}")


if __name__ == "__main__":
    main()
