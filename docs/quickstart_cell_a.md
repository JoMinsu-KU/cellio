# Run Cell A

Activate the Windows Conda environment from the [installation guide](installation.md). Run these commands from the source checkout.

```powershell
python -m cellio doctor --profile isaac62 --output artifacts/doctor.json
python -m cellio assets resolve --profile franka_panda
python -m cellio assets verify --offline
python -m cellio validate --config examples/cell_a/healthy.yaml
if ($env:CELLIO_DATA_ROOT) { $cellioData = $env:CELLIO_DATA_ROOT } else { $cellioData = Join-Path $env:LOCALAPPDATA "CellIO" }
python -m cellio.adapters.isaac62.commissioning --assets "$cellioData/assets" --output "$cellioData/commissioning/geometry.json" --write-lock "$cellioData/locks/robot_profile.lock.json"
python -m cellio run --config examples/cell_a/healthy.yaml --headless
```

The CLI prints the new attempt directory. Use that directory for both commands below. Windows stores the working artifacts under the user's LocalAppData CellIO directory by default. The environment variable `CELLIO_DATA_ROOT` selects another working volume.

The calibration command measures forward kinematics and fingertip geometry at five stage setups. It writes the robot profile used by both quickstarts. Repeat the calibration when the robot asset, tool frame or simulator build changes.

The distributed simulation examples use pilot seed 0. Development runs use seeds 0–4, the fixed nominal pose or the specified boundary cases. The main campaign uses its own registered seed block.

```powershell
python -m cellio verify-artifact --artifact <attempt-directory>
python -m cellio evaluate --artifact <attempt-directory>
```

The scenario feeds a cube on a conveyor, stops the belt and transfers the cube to an output station. The independent placement rule uses the raw pose, velocity, fingers and hand clearance. The final evidence is a 7,200-step trace, a result, manifest checksums and a COMPLETE marker.

Use `examples/cell_a/f6.yaml` to run the gripper-block condition. The fault report distinguishes a committed target override from a measured hold meeting the 31-sample tolerance.

For a viewport recording, set `artifacts.video` to `true`, put FFmpeg on the Windows PATH and select `--gui`. The completed attempt includes `videos/viewport.mp4`. At 30 frames per simulated second the frames correspond to S4, S8, S12 and subsequent fourth steps. Numeric evaluation reads the raw trace.
