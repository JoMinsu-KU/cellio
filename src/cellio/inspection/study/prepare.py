"""Compose a local Cell C stage from the original equipment and the verified Franka cache."""
import argparse
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from cellio.adapters.isaac62.assets import verify_assets
from cellio.environment import data_root
from cellio.io import file_hash, write_json
from cellio.inspection.study.render_study import create_stage

HERE = Path(__file__).resolve().parent
SCENE = "cell_c_inspection.usda"
EQUIPMENT = ("inspection_equipment.usdc", "floor_epoxy.png")


def prepare_stage(output, assets=None):
    """Materialize the generated stage atomically. The external robot stays in its own cache."""
    output = Path(output).resolve()
    cache = Path(assets).resolve() if assets is not None else data_root() / "assets"
    lock = verify_assets(cache)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Choose a new stage directory: {output}")
    with TemporaryDirectory(dir=output.parent) as scratch:
        stage_dir = Path(scratch) / "stage"
        stage_dir.mkdir()
        for name in EQUIPMENT:
            copyfile(HERE / name, stage_dir / name)
        count = create_stage(stage_dir / SCENE, cache / lock["entry"])
        write_json(stage_dir / "stage-manifest.json", {
            "schema": "cellio.inspection-stage/1.0", "prims": count,
            "robot_lock_sha256": file_hash(cache / "assets.lock.json"),
            "robot_entry": lock["entry"],
            "robot_entry_sha256": lock["files"][lock["entry"]]["sha256"],
            "files": {name: file_hash(stage_dir / name) for name in (*EQUIPMENT, SCENE)},
        })
        stage_dir.rename(output)
    return output / SCENE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--assets", type=Path, help="Directory containing the verified assets.lock.json")
    args = parser.parse_args()
    print(prepare_stage(args.out, args.assets))
