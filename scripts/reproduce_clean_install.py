"""Reproduce both five-run quickstarts in a new Conda prefix from a separate working directory."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from time import perf_counter_ns


def hash_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


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
    root = Path(__file__).resolve().parents[1]
    work = args.work_dir.resolve()
    campaign_root = args.campaign_root.resolve()
    data = args.data_root.resolve()
    if platform.system() != "Windows":
        parser.error("Isaac 6.2 workstation profile requires Windows")
    if work.exists() or work.is_relative_to(root) or root.is_relative_to(work):
        parser.error("--work-dir must be a new directory outside the checkout")
    campaign = campaign_root / "clean-install"
    if campaign.exists():
        parser.error("select a campaign root without an existing clean-install suite")
    lock = data / "locks" / "requirements.lock"
    if not lock.is_file():
        parser.error("missing hashed CellIO package lock")
    exe = shutil.which(args.conda)
    if not exe:
        parser.error("Conda executable not found")
    out = campaign_root / "release-evidence" / "clean-install"
    out.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True)
    prefix = work / "env"
    wheel_dir = work / "wheelhouse"
    wheel_dir.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["CELLIO_DATA_ROOT"] = str(data)
    # Conda env activation is unnecessary for pip itself, but the native Windows GPU
    # modules rely on its Library/bin DLL search path.
    env["PATH"] = (
        os.pathsep.join(
            map(str, (prefix, prefix / "Scripts", prefix / "Library" / "bin"))
        )
        + os.pathsep
        + env["PATH"]
    )
    python = str(prefix / "python.exe")
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    proof = {
        "schema_version": "cellio.clean_reproduction/1.0",
        "checkout": str(root),
        "source_commit": commit,
        "working_directory": str(work),
        "prefix": str(prefix),
        "requirements_lock": str(lock),
        "requirements_sha256": hash_file(lock),
        "campaign": str(campaign),
        "commands": [],
    }

    def invoke(key, command):
        stamp = perf_counter_ns()
        started = out / (key + ".log")
        print("Running " + " ".join(map(str, command)), flush=True)
        with started.open("w", encoding="utf-8") as stream:
            proc = subprocess.run(
                list(map(str, command)),
                cwd=work,
                env=env,
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
            "python=3.12",
            "pip",
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
            "torch==2.11.0",
            "--index-url",
            "https://download.pytorch.org/whl/cu130",
        ],
    )
    invoke(
        "isaac",
        [
            python,
            "-m",
            "pip",
            "install",
            "isaacsim[all,extscache]==6.2.0.0",
            "--extra-index-url",
            "https://pypi.nvidia.com",
        ],
    )
    invoke(
        "dependencies", [python, "-m", "pip", "install", "--require-hashes", "-r", lock]
    )
    invoke(
        "build",
        [python, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", wheel_dir, root],
    )
    built = list(wheel_dir.glob("cellio-*.whl"))
    if len(built) != 1:
        raise RuntimeError("expected one freshly built CellIO wheel")
    proof.update(wheel=str(built[0]), wheel_sha256=hash_file(built[0]))
    invoke("install", [python, "-m", "pip", "install", "--no-deps", built[0]])
    identity = out / "runtime.json"
    probe = (
        "import json,platform,sys,cellio; from importlib import metadata as m; from pathlib import Path; "
        "from cellio.environment import source_hash; "
        "Path(sys.argv[1]).write_text(json.dumps(dict(prefix=sys.prefix,executable=sys.executable,"
        "python=platform.python_version(),os=platform.system(),package=cellio.__file__,source_hash=source_hash(),"
        "versions={n:m.version(n) for n in ('cellio','isaacsim','torch','PyYAML','jsonschema')})),encoding='utf-8')"
    )
    invoke("runtime_identity", [python, "-I", "-c", probe, identity])
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
