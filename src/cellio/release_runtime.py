"""Pin and validate the installed dependency graph of the Windows simulator."""

import argparse
import json
import platform
import re
import sys
from importlib import metadata
from pathlib import Path

# Prefix/build tooling is managed by Conda. CellIO itself is installed from
# the independently verified candidate wheel after the hashed project deps.
PREFIX_TOOLS = {
    "cellio",
    "pip",
    "setuptools",
    "wheel",
    "menuinst",
    "anaconda-anon-usage",
}
ROOTS = ("isaacsim[all,extscache]", "torch")


def name_key(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def frozen_constraints(inventory):
    packages = {}
    for original, version in inventory["packages"].items():
        name = name_key(original)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9.!+_-]*", version
        ):
            raise ValueError("Unsafe frozen package/version")
        if name in packages and packages[name] != version:
            raise ValueError(f"Duplicate versions for {name}")
        if (
            name not in PREFIX_TOOLS
            and not name.startswith(("conda-", "anaconda-"))
            and name != "conda"
        ):
            packages[name] = version
    if not {"isaacsim", "torch"} <= packages.keys() or not re.fullmatch(
        r"3\.12\.\d+", inventory["python"]
    ):
        raise ValueError("Incomplete frozen Python/Torch/Isaac inventory")
    return "".join(f"{name}=={version}\n" for name, version in sorted(packages.items()))


def verify_closure(inventory, *, prefix=None, marker_environment=None):
    # Read the metadata in the isolated new prefix. This does not start Kit.
    from packaging.requirements import Requirement
    from packaging.version import Version

    expected = {name_key(k): v for k, v in inventory["packages"].items()}
    prefix = Path(sys.prefix if prefix is None else prefix).resolve()
    installed = {}
    for dist in metadata.distributions(path=[str(prefix / "Lib" / "site-packages")]):
        key = name_key(dist.metadata["Name"])
        if key in installed and installed[key].version != dist.version:
            raise ValueError(f"Conflicting installed distributions for {key}")
        installed[key] = dist
    todo = [Requirement(text) for text in ROOTS]
    seen = set()
    closure = {}
    while todo:
        requirement = todo.pop()
        key = name_key(requirement.name)
        dist = installed.get(key)
        if dist is None:
            raise ValueError(f"Missing installed runtime dependency {key}")
        if (
            expected.get(key) != dist.version
            or not requirement.specifier.contains(
                Version(dist.version), prereleases=True
            )
            or not Path(dist.locate_file("")).resolve().is_relative_to(prefix)
        ):
            raise ValueError(
                f"Runtime dependency {requirement} differs from frozen inventory or prefix: {dist.version}"
            )
        closure[key] = dist.version
        for extra in {"", *requirement.extras}:
            if (key, extra) in seen:
                continue
            seen.add((key, extra))
            for text in dist.requires or ():
                dependency = Requirement(text)
                if dependency.marker is None or dependency.marker.evaluate(
                    {**(marker_environment or {}), "extra": extra}
                ):
                    todo.append(dependency)
    return dict(sorted(closure.items()))


def loaded_conda_dlls(prefix):
    import ctypes
    import sqlite3
    from ctypes import wintypes

    # Importing the standard extension loads the prefix's SQLite runtime.
    if not sqlite3.sqlite_version:
        raise ValueError("SQLite runtime unavailable")
    system = ctypes.WinDLL("kernel32", use_last_error=True)
    system.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    system.GetModuleHandleW.restype = wintypes.HMODULE
    system.GetModuleFileNameW.argtypes = (
        wintypes.HMODULE,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    paths = {}
    for filename, expected in (
        ("python312.dll", prefix / "python312.dll"),
        ("sqlite3.dll", prefix / "Library" / "bin" / "sqlite3.dll"),
    ):
        handle = system.GetModuleHandleW(filename)
        location = ctypes.create_unicode_buffer(32768)
        if not handle or not system.GetModuleFileNameW(handle, location, len(location)):
            raise ValueError(f"Cannot identify loaded {filename}")
        path = Path(location.value).resolve()
        if path != expected:
            raise ValueError(
                f"Loaded {filename} outside the clean Conda prefix: {path}"
            )
        paths[filename] = str(path)
    return paths


def probe(inventory_path, output):
    import os
    from packaging.markers import default_environment

    import cellio
    from cellio.environment import source_hash

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not sys.flags.isolated or inventory["python"] != platform.python_version():
        raise ValueError("Exact Python patch level and isolated mode required")
    closure = verify_closure(inventory)
    prefix = Path(sys.prefix).resolve()
    dll_directory = prefix / "Library" / "bin"
    search_paths = [
        Path(p).resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p
    ]
    if (
        Path(os.environ.get("CONDA_PREFIX", "")).resolve() != prefix
        or dll_directory not in search_paths
        or not dll_directory.is_dir()
        or search_paths[0] != prefix
    ):
        raise ValueError(
            "Clean Conda prefix and its native DLL search path must be active"
        )
    value = {
        "prefix": sys.prefix,
        "executable": sys.executable,
        "python": platform.python_version(),
        "os": platform.system(),
        "isolated": True,
        "conda_prefix": str(prefix),
        "conda_dll_directory": str(dll_directory),
        "loaded_conda_dlls": loaded_conda_dlls(prefix),
        "package": cellio.__file__,
        "source_hash": source_hash(),
        "runtime_roots": list(ROOTS),
        "runtime_closure": closure,
        "marker_environment": default_environment(),
        "versions": {
            name: metadata.version(name)
            for name in ("cellio", "isaacsim", "torch", "PyYAML", "jsonschema")
        },
    }
    output.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe(args.inventory, args.output)
