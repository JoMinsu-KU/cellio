"""Resolve a private local USD dependency closure and verify it without a network."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

from cellio.io import CellIOError, digest, file_hash as file_sha256, write_json

CATALOG_PATH = "Isaac/Robots_Multiphysics/FrankaRobotics/FrankaPanda/franka/franka.usda"
LICENSE_URL = "https://docs.isaacsim.omniverse.nvidia.com/6.2.0/common/licenses.html"
LOCK = "assets.lock.json"
LAYOUT = "catalog-and-sdk-relative-v1"


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


def _sdk_root():
    import isaacsim
    roots = {Path(p).resolve() for p in isaacsim.__path__
             if (Path(p) / "kit").is_dir() and (Path(p) / "exts").is_dir()}
    if len(roots) != 1:
        raise _failure(f"Expected one installed Isaac SDK root, found {len(roots)}")
    return roots.pop()


def _portable_uri(uri, sdk, local_origin):
    parsed = urlsplit(uri)
    if parsed.scheme in {"https", "http", "omniverse"}:
        if parsed.username or parsed.password or parsed.query:
            raise _failure("The offline asset profile requires stable asset URLs without credentials or queries")
        return uri
    native = Path(uri).resolve()
    if native.is_relative_to(sdk):
        return "sdk://isaacsim/" + native.relative_to(sdk).as_posix()
    if local_origin is not None and native.is_relative_to(local_origin):
        return "input://root/" + native.relative_to(local_origin).as_posix()
    raise _failure(f"Local dependency has no reproducible SDK or input root: {native.name}")


def _mirror_path(root, uri):
    parsed = urlsplit(uri)
    category = parsed.scheme if parsed.scheme in {"sdk", "input"} else "remote"
    name = Path(category, parsed.hostname, unquote(parsed.path).lstrip("/"))
    return _relative(root, str(name))


def _mirror_dependencies(destination, source, layers, assets, sdk, local_origin):
    """Keep the catalog layout so external gripper variants retain relative references."""
    import omni.client
    from pxr import Ar, Sdf, UsdUtils
    identifiers = {str(layer.realPath or layer.identifier) for layer in layers} | set(map(str, assets)) | {source}
    public = {uri: _portable_uri(uri, sdk, local_origin) for uri in identifiers}
    paths = {uri: _mirror_path(destination, public[uri]) for uri in identifiers}
    local_sources = {os.path.normcase(os.path.normpath(uri)): target for uri, target in paths.items()
                     if "://" not in uri}
    digests = {}
    for uri in sorted(paths):
        target = paths[uri]
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(uri).is_file():
            shutil.copyfile(uri, target)
        else:
            status, _, contents = omni.client.read_file(uri)
            if status != omni.client.Result.OK:
                raise _failure(f"Could not read {uri}: {status}")
            target.write_bytes(bytes(contents))
        digests[public[uri]] = file_sha256(target)
    # Absolute vendor references need a relative equivalent in a portable mirror.
    # Leave already-relative paths and vendor source layers intact.
    for layer in layers:
        local = paths[str(layer.realPath or layer.identifier)]
        local_layer = Sdf.Layer.FindOrOpen(str(local))
        if not local_layer:
            raise _failure(f"Cannot open mirrored layer {local}")
        def relative(asset_path):
            if not asset_path:
                return asset_path
            if "://" not in asset_path and not Path(asset_path).is_absolute() and (local.parent / asset_path).is_file():
                return asset_path
            absolute = str(Sdf.ComputeAssetPathRelativeToLayer(layer, asset_path))
            resolved = str(Ar.GetResolver().Resolve(absolute))
            target = paths.get(absolute) or paths.get(resolved) or local_sources.get(os.path.normcase(os.path.normpath(resolved)))
            # Search-path MDL names resolve through Kit's shader registry, which
            # need not expose that same search path to the generic Ar resolver.
            if target is None and Path(asset_path).name == asset_path:
                matches = [target for uri, target in paths.items() if target.name == asset_path]
                if len(matches) == 1:
                    target = matches[0]
            if target is not None:
                return "./" + Path(os.path.relpath(target, local.parent)).as_posix()
            return asset_path
        UsdUtils.ModifyAssetPaths(local_layer, relative)
        if local_layer.dirty:
            local_layer.Save()
    return paths[source], digests


def _collect(root, source):
    import omni.kit.app
    from pxr import Sdf, UsdUtils

    destination = root / "localized"
    destination.mkdir()
    asset = Sdf.AssetPath(source)
    layers, assets, missing = UsdUtils.ComputeAllDependencies(asset)
    if not layers or missing:
        raise _failure(f"Unresolved source dependencies: {list(map(str, missing))!r}")
    sdk = _sdk_root()
    local_origin = Path(source).resolve().parent if "://" not in source else None
    entry, original_hashes = _mirror_dependencies(destination, source, layers, assets, sdk, local_origin)
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
    return {"schema": "cellio.assets/1.0", "layout": LAYOUT, "source": _portable_uri(source, sdk, local_origin),
            "entry": entry.relative_to(root).as_posix(),
            "terms": LICENSE_URL, "files": files, "motion_config": {
                path.name: {"source_path": _portable_uri(str(path), sdk, local_origin), "sha256": file_sha256(path)}
                for path in motion_paths},
            "source_layers": sorted(_portable_uri(str(layer.realPath or layer.identifier), sdk, local_origin) for layer in layers),
            "source_assets": sorted(_portable_uri(str(asset), sdk, local_origin) for asset in assets),
            "original_sha256": original_hashes}


def motion_files(directory):
    """Discover the official Franka kinematic descriptions and its tuned RMPflow file."""
    urdf = sorted(directory.glob("*.urdf"))
    xrdf = sorted(directory.glob("*.xrdf"))
    rmp = directory / "rmp_flow.yaml"
    if not urdf or not xrdf or not rmp.is_file():
        raise _failure(f"Incomplete cuMotion configuration directory: {directory}")
    return (*urdf, *xrdf, rmp)


def resolve_assets(root, *, source=None):
    """A separate native process prevents Kit from terminating the calling CLI."""
    root = Path(root).resolve()
    if (root / LOCK).is_file():
        current = verify_assets(root)
        if current.get("layout") == LAYOUT:
            return current
    command = [sys.executable, "-m", "cellio.adapters.isaac62.asset_worker", str(root)]
    if source is not None:
        command.extend(["--source", str(source)])
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise _failure(f"Asset resolver failed (exit {result.returncode}); inspect its log")
    return verify_assets(root)


def collect_assets(root, *, source=None):
    """Populate an empty cache using the installed 6.2 resolver. Never replaces a prior lock."""
    root = Path(root).resolve()
    if (root / LOCK).is_file():
        current = verify_assets(root)
        if current.get("layout") == LAYOUT:
            return current
    root.mkdir(parents=True, exist_ok=True)
    from .application import release_application, start_application
    app = start_application({"headless": True, "multi_gpu": False, "disable_viewport_updates": True})
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
            write_json(pending / LOCK, lock)
            verify_assets(pending)
            # Publish immutable data before the lock, so a retry after a crash can
            # validate/reuse the same generation without deleting a prior cache.
            generation = "franka-" + digest(lock["files"])[:24]
            destination = root / generation
            try:
                (pending / "localized").rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
            lock["entry"] = generation + "/" + lock["entry"].removeprefix("localized/")
            lock["files"] = {generation + "/" + key.removeprefix("localized/"): v for key, v in lock["files"].items()}
            for name, expected in lock["files"].items():
                if file_sha256(_relative(root, name)) != expected["sha256"]:
                    raise _failure(f"Existing generation contains a modified file: {name}")
            write_json(root / LOCK, lock)
    finally:
        release_application(app)
    return verify_assets(root)
