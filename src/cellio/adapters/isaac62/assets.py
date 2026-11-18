"""Resolve a private local USD dependency closure and verify it without a network."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from cellio.io import CellIOError

CATALOG_PATH = "Isaac/Robots_Multiphysics/FrankaRobotics/FrankaPanda/franka/franka.usda"
LICENSE_URL = "https://docs.isaacsim.omniverse.nvidia.com/6.2.0/common/licenses.html"
LOCK = "assets.lock.json"


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _failure(reason):
    return CellIOError("ASSET_VERIFY_FAILED", reason, exit_code=3)


def _relative(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root) or Path(name).is_absolute():
        raise _failure(f"Path escapes the asset cache: {name}")
    return path


def verify_assets(root):
    """Pure-Python offline check of every localized file the resolver recorded."""
    root = Path(root).resolve()
    try:
        lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _failure(f"Cannot read {root / LOCK}: {exc}") from exc
    files = lock.get("files", {})
    if (lock.get("schema") != "cellio.assets/1.0" or not files
            or lock.get("entry") not in files or not lock.get("source")):
        raise _failure("Incomplete dependency lock")
    for name, expected in files.items():
        path = _relative(root, name)
        if not path.is_file() or path.stat().st_size != expected["bytes"] or file_sha256(path) != expected["sha256"]:
            raise _failure(f"Missing or modified dependency: {name}")
    return lock


def _collect(root, source):
    import omni.kit.app
    from pxr import Sdf, UsdUtils

    destination = root / "localized"
    destination.mkdir()
    asset = Sdf.AssetPath(source)
    layers, assets, missing = UsdUtils.ComputeAllDependencies(asset)
    if not layers or missing:
        raise _failure(f"Unresolved source dependencies: {list(map(str, missing))!r}")
    if not UsdUtils.LocalizeAsset(asset, str(destination)):
        raise _failure(f"USD could not localize {source}")
    entry = destination / source.rsplit("/", 1)[-1]
    if not entry.is_file():
        raise _failure(f"Localized root layer missing: {entry}")
    local_layers, local_assets, missing = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(entry)))
    if not local_layers or missing:
        raise _failure(f"Unresolved dependencies after localization: {list(map(str, missing))!r}")
    for identifier in [str(layer.realPath) for layer in local_layers] + list(map(str, local_assets)):
        if not Path(identifier).resolve().is_relative_to(destination):
            raise _failure(f"Dependency is not self-contained: {identifier}")
    extensions = omni.kit.app.get_app().get_extension_manager()
    extensions.set_extension_enabled_immediate("isaacsim.robot_motion.cumotion", True)
    from isaacsim.robot_motion.cumotion import load_cumotion_supported_robot
    motion_root = Path(load_cumotion_supported_robot("franka").directory)
    motion_paths = motion_files(motion_root)
    frozen_motion = destination / ".cellio-motion"
    frozen_motion.mkdir()
    for path in motion_paths:
        shutil.copyfile(path, frozen_motion / path.name)
    files = {
        path.relative_to(root).as_posix(): {"sha256": file_sha256(path), "bytes": path.stat().st_size}
        for path in sorted(destination.rglob("*")) if path.is_file()
    }
    return {"schema": "cellio.assets/1.0", "source": source, "entry": entry.relative_to(root).as_posix(),
            "terms": LICENSE_URL, "files": files, "motion_config": {
                path.name: {"source_path": str(path), "sha256": file_sha256(path)}
                for path in motion_paths},
            "source_layers": sorted(str(layer.identifier) for layer in layers),
            "source_assets": sorted(map(str, assets))}


def motion_files(directory):
    """Discover the official Franka kinematic descriptions and its tuned RMPflow file."""
    urdf = sorted(directory.glob("*.urdf"))
    xrdf = sorted(directory.glob("*.xrdf"))
    rmp = directory / "rmp_flow.yaml"
    if not urdf or not xrdf or not rmp.is_file():
        raise _failure(f"Incomplete cuMotion configuration directory: {directory}")
    return (*urdf, *xrdf, rmp)


def resolve_assets(root, *, source=None):
    """Populate an empty cache using the installed 6.2 resolver. Never replaces a prior lock."""
    root = Path(root).resolve()
    if (root / LOCK).is_file():
        return verify_assets(root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "localized").exists():
        raise _failure("Uncommitted localized directory exists. Select a fresh cache directory.")
    from isaacsim.simulation_app import SimulationApp

    app = SimulationApp({"headless": True, "multi_gpu": False, "disable_viewport_updates": True,
                         "fast_shutdown": False})
    try:
        if source is None:
            from isaacsim.storage.native import get_assets_root_path
            catalog = get_assets_root_path()
            if not catalog:
                raise _failure("NVIDIA 6.2 catalog root is unavailable")
            source = catalog.rstrip("/") + "/" + CATALOG_PATH
        with tempfile.TemporaryDirectory(dir=root, prefix=".resolve-") as temp:
            pending = Path(temp)
            lock = _collect(pending, str(source).replace(chr(92), "/"))
            (pending / LOCK).write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
            verify_assets(pending)
            (pending / "localized").rename(root / "localized")
            (pending / LOCK).rename(root / LOCK)
    finally:
        app.close()
    return verify_assets(root)
