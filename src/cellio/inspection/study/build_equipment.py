"""Build the metre-scale equipment model with Blender in background mode."""
import argparse
import sys
from pathlib import Path
import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gauge_model import build_gauge, build_part
from handling_model import build_handling
from skid_model import build_skid
from shop_context import build_context

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", required=True, type=Path, help="Fresh directory for the editable model and USD")
parser.add_argument("--blend-only", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.scale_length = 1.0
build_skid()
build_handling()
build_gauge()
build_context()
build_part("Infeed_bracket", (-0.44, -0.248, 0.8990))
build_part("Accepted_bracket", (0.50, -0.19, 0.854))
bpy.context.view_layer.update()
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=str(out / "inspection_equipment.blend"), compress=True)
if not args.blend_only:
    equipment = out / "inspection_equipment.usdc"
    bpy.ops.wm.usd_export(
        filepath=str(equipment), root_prim_path="/Equipment", export_animation=False,
        export_materials=True, generate_preview_surface=True, export_lights=False,
        export_cameras=False, relative_paths=True, convert_scene_units="METERS",
    )
    print(f"Saved {equipment} ({equipment.stat().st_size:,} bytes), {len(bpy.context.scene.objects)} Blender objects")
