# Cell C inspection and sorting

Cell C runs two machined brackets through an indexed infeed, a locating fixture with a contact comparator, and single-place accept and reject nests. The software records both the workpiece ID inspected and the ID of the result consumed for routing. A stale-OK controller can carry the first part's accepted result into the next cycle.

## Run as a CellIO scene

Use the same Windows environment, verified Franka cache and doctor check as for [Cell A](quickstart_cell_a.md). From the source checkout, run the following commands. Supply the attempt directory printed by `run` to the last two commands.

```console
cellio validate --config examples/cell_c/healthy.yaml
cellio run --config examples/cell_c/healthy.yaml --headless
cellio verify-artifact --artifact <attempt-directory>
cellio evaluate --artifact <attempt-directory>
```

| Scenario in `examples/cell_c` | Heights from the drawing | Expected result for P2 |
| --- | --- | --- |
| `healthy.yaml` | 33.15, 33.25 mm | P2's own result sends it to NG |
| `stale_ok.yaml` | 33.15, 33.25 mm | P1's result sends P2 to OK |
| `both_good.yaml` | 32.85, 33.15 mm | P2's own result sends it to OK |
| `both_good_stale.yaml` | 32.85, 33.15 mm | P1's result sends P2 to the correct OK tray |

Change `scene.parameters.errors_mm` to set the two signed deviations from 33.00 mm and `infeed_offsets_mm` to set the two initial XY offsets. The `reference` and `mutant.stale_ok` controllers see gauge observations through the common signal interface. They issue a result-owner and a route command for each part. The native workcell consumes those commands before physically placing the part. The attempt contains the common trace and command receipts, as well as a `native` directory with the motion and gauge records. Run `cellio inspection replay <attempt-directory>/native` to inspect the two parts individually.

Each native Cell C scenario uses a fresh headless process. The scene supports the two sorting controllers shown above. The A/B fault-family campaigns, grouped resets and GUI capture have their own coverage. The shared worker records its own process memory. Cell C's Isaac child runs separately, so the worker's RSS and per-process GPU figures cover only part of the total.

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

To evaluate an earlier study run with the scene plugin's current rules, import the completed native directory or an exported witness, then pass the new directory to the common commands.

```console
cellio inspection import path/to/completed-native-run --output archived-attempt
cellio verify-artifact --artifact archived-attempt
cellio evaluate --artifact archived-attempt
```

The import preserves the observed event times and the original source digests. Evaluation uses the recorded gauge, routing decision and settled pose. Each event frame maps to its original boundary on the 120 Hz clock. Imported evidence has no shared online verdict to compare with its newly computed verdict.

## Run the original inspection study

Use the Windows Python 3.12 environment with Isaac Sim 6.2 and its scientific Python dependencies. Install this CellIO checkout in that environment. Resolve the Franka asset through the documented asset workflow; its license and local cached location belong to the user. The original workcell geometry, construction scripts, floor material and camera definitions ship with CellIO. The `--assets` argument points to the directory containing the verified `assets.lock.json`. The default is the `assets` directory under the CellIO data root.

```console
cellio inspection prepare --assets path/to/assets --output local-cell
cellio inspection run normal --assets path/to/assets --output run-normal
cellio inspection run stale_ok --assets path/to/assets --output run-stale
cellio inspection verify --normal run-normal --stale run-stale --output comparison.json
```

Each run accepts an optional `--recipe path/to/recipe.json` to specify the two part dimensions and indexed input offsets. Use a new output directory for each run. The preparation command produces a local `cell_c_inspection.usda` for inspection. Each native run also prepares its own scene. Open its `replay.usda` to play the recorded robot, gauge and part motion. The paired verification reads the native stage and checks the motion trace and geometry audits.

The native study uses joint-limited kinematics and virtual attachment during transport. After release, PhysX computes gravity and rigid-body settlement at 120 Hz. The gripper does not model holding force or slip under load. Geometry checks use the saved motion samples. The first settled part is removed by a simulated external takeaway before the next part is indexed. The historical study recorded event and pose evidence at 30 Hz. Fresh scene-plugin runs also record shared controller decisions and applied commands on the framework clock.
