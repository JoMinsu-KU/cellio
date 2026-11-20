# Cell C dimensional inspection and sorting

This example models an inlet belt, a three-point datum fixture, a retractable comparator and two single-place outlet nests. An external service removes the first bracket after its output position has been recorded. The controller keeps its result latch across the two parts. The reference pair measures a 33.00 mm boss and a 33.60 mm boss against a tolerance of 33.00 ± 0.20 mm.

## Run from an installed CellIO wheel

Use CellIO's Windows Isaac Sim 6.2 Python environment. The native run needs the original Franka asset, SciPy and NumPy. The USD audits use the same environment and can run without starting Kit. A local installation of FFmpeg with an H.264 encoder is used only for optional video export.

```shell
python -m cellio assets resolve --profile franka_panda
python -m cellio assets verify --offline
python -m cellio.inspection.study.run_inspection normal --out normal
python -m cellio.inspection.study.run_inspection stale_ok --out stale
python -m cellio.inspection.study.verify_inspection normal stale --out comparison.json
python -m cellio.inspection.study.capture_inspection normal
```

Each run requires a fresh output directory. The scene, local evidence and replay go into that directory. To use an existing asset cache outside the default CellIO data root, pass its directory containing `assets.lock.json` to each native run with `--assets`. The simulator loads the Franka from that cache. When moving an entire run to another workstation, preserve the referenced asset location or re-run the example with the target machine's cache.

Open `normal/replay.usda` in Isaac Sim to inspect the time samples. The verifier checks both conditions, the event and pose records, the geometry at the measurement frame, the tray actually occupied after release, and the robot, gripper and payload geometry. Missing audit sidecars are calculated from the replay. Video export writes `cycle.mp4` and inspection and output stills to the run directory. The separate `--stills-only` capture mode works without FFmpeg.

## Authoring and provenance

CellIO contributors created the equipment geometry, materials and procedural floor texture. These original sources and the included `inspection_equipment.usdc` and `floor_epoxy.png` are distributed under the repository's MIT license. The equipment USD has no external resource references. Blender 5.1.1 was used for the distributed geometry. Use the installed `build_equipment.py` path with Blender in background mode to regenerate the editable Blender model and the USD in a fresh directory.

```shell
blender --background --factory-startup --python-exit-code 1 --python /path/to/build_equipment.py -- --out new-equipment
python -m cellio.inspection.study.prepare --out assembled-cell
python -m cellio.inspection.study.render_study --out rendered-cell
```

`prepare` hashes the licensed asset closure using CellIO's offline resolver, generates a local assembly and records the asset lock and included file digests. `render_study` additionally renders three camera views. The generated assembly references the separately acquired NVIDIA Franka USD. The NVIDIA model, its materials, the cuMotion collision masks and the SDK remain subject to their upstream terms. [NVIDIA Isaac Sim licenses](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/common/licenses.html)

## Model scope

Arm motion follows constrained joint kinematics. The workpiece follows a virtual grasp during transport, then falls and settles under native PhysX gravity and contact after release. The probe measures the actual mesh geometry. Inputs arrive by an indexing event. Clearances are sampled and report their sampling interval and the included obstacle pairs. These runs support conclusions about measurement provenance and physical misrouting in this simulated cell.
