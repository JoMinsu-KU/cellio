"""Package measured geometry and asset inventory metadata for the source repository."""

import argparse
import json
from pathlib import Path

from cellio.adapters.isaac62.assets import verify_assets
from cellio.environment import data_root
from cellio.io import digest, file_hash, write_json


def export(root, geometry, output):
    assets = verify_assets(root / "assets")
    measurement = json.loads(geometry.read_bytes())
    asset = Path(measurement["provenance"]["robot_asset"].removeprefix("cache://"))
    if not asset.is_absolute():
        asset = root / "assets" / asset
    if asset.resolve() != (root / "assets" / assets["entry"]).resolve():
        raise ValueError("The measured geometry refers to a different asset generation")
    measurement["provenance"]["robot_asset"] = assets["entry"]
    measurement["source_file_sha256"] = file_hash(geometry)
    write_json(output / "franka-geometry.json", measurement)
    profile = root / "locks" / "robot_profile.lock.json"
    write_json(output / "robot_profile.lock.json", json.loads(profile.read_bytes()))
    write_json(
        output / "franka-asset-inventory.json",
        {
            "asset_lock_digest": digest(assets),
            "entry": assets["entry"],
            "source": assets["source"],
            "terms": assets["terms"],
            "usd_layers": len(assets["source_layers"]),
            "external_assets": len(assets["source_assets"]),
            "motion_config_sha256": {
                name: obj["sha256"] for name, obj in assets["motion_config"].items()
            },
            "file_count": len(assets["files"]),
            "total_bytes": sum(f["bytes"] for f in assets["files"].values()),
            "files": sorted(
                assets["files"].values(), key=lambda f: (f["sha256"], f["bytes"])
            ),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=data_root())
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.data_root, args.geometry, args.output)
