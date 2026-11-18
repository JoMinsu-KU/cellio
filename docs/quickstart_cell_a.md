# Run Cell A

Activate the Windows Conda environment from the [installation guide](installation.md). Run these commands from the source checkout.

```powershell
python -m cellio doctor --profile isaac62 --output artifacts/doctor.json
python -m cellio assets resolve --profile franka_panda
python -m cellio assets verify --offline
python -m cellio validate --config examples/cell_a/healthy.yaml
python -m cellio run --config examples/cell_a/healthy.yaml --headless
```

The CLI prints the new attempt directory. Use that directory for both commands below. Windows stores the working artifacts under the user's LocalAppData CellIO directory by default. The environment variable `CELLIO_DATA_ROOT` selects another working volume.

```powershell
python -m cellio verify-artifact --artifact <attempt-directory>
python -m cellio evaluate --artifact <attempt-directory>
```

The scenario feeds a cube on a conveyor, stops the belt and transfers the cube to an output station. The independent placement rule uses the raw pose, velocity, fingers and hand clearance. The final evidence is a 7,200-step trace, a result, manifest checksums and a COMPLETE marker.

Use `examples/cell_a/f6.yaml` to run the gripper-block condition. The fault report distinguishes a committed target override from a measured hold meeting the 31-sample tolerance.
