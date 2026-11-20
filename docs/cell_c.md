# Cell C inspection and sorting

Cell C runs two machined brackets through an indexed infeed, a locating fixture with a contact comparator, and single-place accept and reject nests. The software records both the workpiece ID inspected and the ID of the result consumed for routing. A stale-OK controller can carry the first part's accepted result into the next cycle.

## Replay on a CPU

The standard CellIO installation includes two compact witnesses extracted from native commissioning runs. The following commands work without Isaac Sim, USD, a robot asset or a GPU.

```console
cellio inspection replay --example normal
cellio inspection replay --example stale_ok
cellio inspection replay --example stale_ok --assert-pass
```

In this pair, P1 measures 33.000 mm and P2 measures 33.600 mm against a 33.00 ± 0.20 mm drawing tolerance. The normal controller places the second part in NG. The stale controller measures the same part, consumes P1's result and places P2 in OK. The latter produces `STALE_RESULT` and `WRONG_DESTINATION`. In a pair of accepted parts a stale result can violate identity even when the destination is correct. Successful replay returns zero by default. `--assert-pass` returns 5 when the replay detects a violation.

The portable replay classifies the recorded part-local height and checks the four transformed sole corners, tray elevation, linear speed and angular speed independently of the commanded bin. It also checks event order and result provenance. Extracting a witness from a native run checks the motion-trace digest and matches the recorded settled pose against its trace sample. An exported witness contains these measurements and source hashes. Recomputing geometric measurements from the rendered meshes requires the full native verification below.

```console
cellio inspection replay path/to/completed-native-run --export witness.json
cellio inspection replay witness.json
```

## Run on a workstation

Use the Windows Python 3.12 environment with Isaac Sim 6.2 and its scientific Python dependencies. Install this CellIO checkout in that environment. Resolve the Franka asset through the documented asset workflow; its license and local cached location belong to the user. The original workcell geometry, construction scripts, floor material and camera definitions ship with CellIO. The `--assets` argument points to the directory containing the verified `assets.lock.json`. The default is the `assets` directory under the CellIO data root.

```console
cellio inspection prepare --assets path/to/assets --output local-cell
cellio inspection run normal --assets path/to/assets --output run-normal
cellio inspection run stale_ok --assets path/to/assets --output run-stale
cellio inspection verify --normal run-normal --stale run-stale --output comparison.json
```

Each run accepts an optional `--recipe path/to/recipe.json` to specify the two part dimensions and indexed input offsets. Use a new output directory for each run. The preparation command produces a local `cell_c_inspection.usda` for inspection. Each native run also prepares its own scene. Open its `replay.usda` to play the recorded robot, gauge and part motion. The paired verification reads the native stage and checks the motion trace and geometry audits.

The native study uses joint-limited kinematics and virtual attachment during transport. After release, PhysX computes gravity and rigid-body settlement at 120 Hz. The gripper does not model holding force or slip under load. Geometry checks use the saved motion samples. The first settled part is removed by a simulated external takeaway before the next part is indexed. The supplemental native event/pose evidence and its verifier are accessed through `cellio inspection`. The registered A/B tick traces use `cellio run` and `cellio evaluate`.
