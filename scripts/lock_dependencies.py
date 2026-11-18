"""Hash actual platform wheels for the CellIO packages added to the simulator."""

import argparse
from importlib import metadata
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tempfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

from cellio.environment import data_root
from cellio.io import atomic_write, file_hash, write_json


def dependency_versions():
    pending = [
        "PyYAML",
        "jsonschema",
        "pytest",
        "hypothesis",
        "coverage",
        "ruff",
        "mypy",
        "matplotlib",
    ]
    versions = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in versions:
            continue
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for text in distribution.requires or ():
            dep = Requirement(text)
            if dep.marker is None or dep.marker.evaluate({"extra": ""}):
                pending.append(dep.name)
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    env_dir = args.output
    versions = dependency_versions()
    specs = [f"{n}=={v}" for n, v in sorted(versions.items())]
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--progress-bar",
                "off",
                "--no-deps",
                "--only-binary=:all:",
                "-d",
                temp,
                *specs,
            ],
            check=True,
        )
        lines = []
        for wheel in sorted(Path(temp).glob("*.whl")):
            name, version, _, _ = parse_wheel_filename(wheel.name)
            if str(version) != versions[name]:
                raise RuntimeError(f"Wheel and installed version differ for {name}")
            lines.append(f"{name}=={version} --hash=sha256:{file_hash(wheel)}")
        if len(lines) != len(versions):
            raise RuntimeError("Incomplete platform wheel closure")
    payload = ("\n".join(sorted(lines)) + "\n").encode()
    atomic_write(env_dir / "requirements.lock", payload)
    atomic_write(data_root() / "locks" / "requirements.lock", payload)
    # An editable checkout may also contain old build-time .egg-info. Inventory
    # the active prefix's installed distributions, as pip checks them.
    locations = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
    all_packages = {
        canonicalize_name(d.metadata["Name"]): d.version
        for d in metadata.distributions(path=locations)
        if d.metadata["Name"]
    }
    records = {
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "os": platform.system(),
        "os_version": platform.version(),
        "packages": dict(sorted(all_packages.items())),
        "requirements_sha256": file_hash(env_dir / "requirements.lock"),
    }
    write_json(env_dir / "python-environment.json", records)


if __name__ == "__main__":
    main()
