"""Collect inspectable environment and source identities without importing Kit."""

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from cellio.io import CellIOError, canonical, digest, file_hash, write_json


def data_root():
    override = os.environ.get("CELLIO_DATA_ROOT")
    if override:
        return Path(override).resolve()
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "CellIO"
    return Path.home() / ".local" / "share" / "cellio"


def version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def probe_static(profile="isaac62"):
    root = data_root()
    base = next(p for p in (root, *root.parents) if p.exists())
    packages = {
        n: version(n)
        for n in (
            "cellio",
            "PyYAML",
            "jsonschema",
            "isaacsim",
            "isaacsim-kernel",
            "isaacsim-robot-motion",
            "torch",
            "warp-lang",
        )
    }
    result = {
        "profile": profile,
        "platform": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "processor": platform.processor(),
        "logical_processors": os.cpu_count(),
        "packages": packages,
        "disk_free": shutil.disk_usage(base).free,
        "problems": [],
    }
    if sys.version_info[:2] != (3, 12) or not all(
        packages[n] for n in ("PyYAML", "jsonschema")
    ):
        result["problems"].append(
            "Python 3.12 and installed core dependencies are required"
        )
    if profile == "isaac62":
        if os.name != "nt" or packages["isaacsim"] != "6.2.0.0":
            result["problems"].append(
                "The simulation profile requires Windows and isaacsim 6.2.0.0"
            )
        try:
            info = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                timeout=15,
                capture_output=True,
                text=True,
            )
            result["gpus"] = [
                dict(
                    zip(
                        ("name", "driver_version", "vram_mib"),
                        (value.strip() for value in line.split(",")),
                        strict=True,
                    )
                )
                for line in info.stdout.splitlines()
                if line.strip()
            ]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["problems"].append(str(exc))
    if sys.platform == "win32":
        import ctypes

        value = ctypes.c_ulonglong()
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(
            ctypes.byref(value)
        ):
            result["installed_ram_bytes"] = value.value * 1024
    return result


def doctor(profile, output, level="smoke"):
    report = probe_static(profile)
    report["level"] = level
    if profile == "isaac62" and level in ("import", "smoke") and not report["problems"]:
        with TemporaryDirectory() as directory:
            reply = Path(directory) / "kit.json"
            log = Path(output).with_suffix(".kit.log")
            log.parent.mkdir(parents=True, exist_ok=True)
            try:
                with log.open("wb") as stream:
                    p = subprocess.run(
                        [sys.executable, "-m", "cellio.kit_probe", str(reply), level],
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        timeout=900,
                    )
                if reply.is_file():
                    report["kit"] = json.loads(reply.read_text(encoding="utf-8"))
                if p.returncode or not report.get("kit", {}).get("passed"):
                    report["problems"].append(
                        f"Kit probe exited {p.returncode}; see {log.name}"
                    )
            except subprocess.TimeoutExpired:
                report["problems"].append("ENV_BOOT_TIMEOUT")
    report["passed"] = not report["problems"]
    write_json(Path(output), report)
    return report


def source_hash():
    package = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(
        package.rglob("*.py"), key=lambda p: p.relative_to(package).as_posix()
    ):
        h.update(canonical([p.relative_to(package).as_posix(), file_hash(p)]))
    return h.hexdigest()


def lock_context(profile, *, measured=False):
    folder = data_root() / "locks"
    env = probe_static("mock" if profile == "mock_lockstep" else "isaac62")
    env.pop("disk_free", None)
    if env.pop("problems"):
        raise CellIOError(
            "ENV_PROFILE",
            "The active interpreter does not meet the profile",
            exit_code=3,
        )
    if profile == "mock_lockstep":
        assets = {"profile": "mock_lockstep", "dependencies": []}
        protocol = {
            "profile": "mock_lockstep",
            "clock_hz": 120,
            "controller_divisor": 4,
        }
    else:
        check = folder / "doctor.json"
        if not check.exists():
            raise CellIOError(
                "ENV_DOCTOR_REQUIRED",
                "Run doctor using the installed 6.2 runtime",
                exit_code=3,
            )
        status = json.loads(check.read_text(encoding="utf-8"))
        if (
            not status.get("passed")
            or status.get("level") != "smoke"
            or any(status.get(k) != v for k, v in env.items())
        ):
            raise CellIOError(
                "ENV_PROFILE_CHANGED",
                "Repeat doctor for the current device and environment",
                exit_code=3,
            )
        env["kit"] = status["kit"]
        from cellio.adapters.isaac62.assets import verify_assets

        assets = verify_assets(data_root() / "assets")
        frozen = folder / "protocol.lock.json"
        if measured and not frozen.exists():
            raise CellIOError(
                "CFG_LOCK_MISSING",
                "Freeze the protocol after commissioning",
                exit_code=3,
            )
        protocol = (
            json.loads(frozen.read_text(encoding="utf-8"))
            if frozen.exists()
            else {"phase": "commissioning", "clock_hz": 120, "controller_divisor": 4}
        )
    lock = folder / "requirements.lock"
    if measured and not lock.is_file():
        raise CellIOError(
            "CFG_LOCK_MISSING", "Verified distribution lock required", exit_code=3
        )
    req = (
        lock.read_text(encoding="utf-8")
        if lock.is_file()
        else "".join(
            f"{name}=={v}\n" for name, v in sorted(env["packages"].items()) if v
        )
    )
    provenance = {
        "code_hash": source_hash(),
        "environment_hash": digest(env),
        "assets_hash": digest(assets),
        "protocol_hash": digest(protocol),
        "requirements_hash": hashlib.sha256(req.encode()).hexdigest(),
    }
    return dict(
        environment=env, assets=assets, protocol=protocol, requirements_lock=req
    ), provenance
