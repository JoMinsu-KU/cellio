# Cell C dimensional inspection extension

This experiment tests whether CellIO can distinguish a finished robot cycle from a correct quality decision. A Franka arm transfers a machined bracket from an indexed conveyor to a comparator fixture, then to an accept or reject tray. The shared result latch stays active across both parts. The first seated part is removed from the single-place output tray before the second is routed.

## Registered design

The drawing height is 33.00 ± 0.20 mm. The four registered machined heights, 32.75, 32.85, 33.15 and 33.25 mm, bracket both limits by 0.05 mm. Six ordered two-part recipes cover crossings in both directions at both limits, two accepted parts, and two rejected parts. Two independently specified indexed infeed schedules vary the two bracket origins on the belt. The reference and stale-OK controllers receive matched geometry and infeed inputs at each of the 12 design points. Each run starts a fresh Isaac Sim process.

The reference controller tags each new quality result with the inspected part ID. The mutant suppresses result updates after its first OK. Its three distinct outcomes are a false accept when an out-of-tolerance part follows a good part, a stale tag masked by a correct physical bin when two good parts are consecutive, and an unaffected second decision when the first part is rejected.

The primary count is the number of first native attempts after the runtime revision was frozen. Report the earlier startup-only revision separately. Placement schedules, dimension offsets, controllers and ordered parts are fixed design levels. The two ordered parts share a latch and do not represent independent replications.

## Observed outcomes

The corrected runtime finished all 24 first attempts. Independent replay and all three supplementary geometry checks passed for each. The reference controller produced the correct result ID and physical bin for all 24 parts. On its 24 parts the mutant consumed six stale IDs, physically false-accepted four out-of-tolerance brackets, and placed two brackets with stale IDs in the correct tray. The affected second parts came from the four OK→NG runs and the two OK→OK runs respectively.

| Registered second-part context | Matched physical recipes | Reference P2 tags and bins | Stale-OK P2 tags and bins |
| --- | ---: | --- | --- |
| OK → NG, upper/lower crossing | 4 | 4 fresh, 4 rejected | 4 stale, 4 false accepts |
| NG → OK, reverse crossing | 4 | 4 fresh, 4 accepted | 4 fresh, 4 accepted |
| OK → OK | 2 | 2 fresh, 2 accepted | 2 stale, 2 accepted |
| NG → NG | 2 | 2 fresh, 2 rejected | 2 fresh, 2 rejected |

Every paired input pose and probe reading matched between controllers at recorded precision. The largest absolute probe-to-part-local geometry difference was 0.000172 mm in this synthetic model. Native release-to-tray descent was 3.020–3.026 mm. The 24 jobs took a median of 69.5 seconds each on this workstation, with a range of 67.4–75.3 seconds including startup.

The offline geometry audit covered 96 grasps, 39,368 recorded robot poses and 92,210 interpolated carried-workpiece poses. Minimum sampled separation bounds were 3.955 mm for robot to equipment, 7.197 mm for nonadjacent robot links, and 45.920 mm for the carried part to gauge hardware. The in-bore radial bound was 0.228 mm. The most conservative projection including the near-contact foot/locator interface was 0.000034 mm. Geometry audits found zero unresolved intersections at the checked samples.

An earlier runtime allocation had two native starts, both ending during Kit initialization with Windows error `0xc0000139` and zero recorded part inspections. That configuration loaded standalone USD before the Kit process initialized its own USD runtime. The corrected revision initializes Kit first and preserves all 24 original registered recipes. The compact summary includes the prior starts, logs' hashes, 22 unstarted rows in that earlier allocation, and both runtime source manifests.

## Reproduction

Use the repository checkout in the Windows Isaac Sim 6.2 environment with the verified Franka asset cache. Use a new output directory. The `prepare` command allocates all cells of the design before measurements are collected. The native worker checks the source hashes and writes a new attempt directory per launch. Each archived episode includes a regenerated local USD, native motion/event trace, two seating witnesses, controller decisions, and readback.

```powershell
python scripts/run_cell_c_extension.py prepare --root work/reproduce-cell-c
python scripts/run_cell_c_extension.py execute --root work/reproduce-cell-c
python scripts/summarize_cell_c_extension.py collect --root work/reproduce-cell-c
python scripts/summarize_cell_c_extension.py export --root work/reproduce-cell-c --out work/reproduce-cell-c-summary
```

The study runner uses session-accepted NVIDIA terms and the installed CellIO asset cache. CPU affinity and number of BLAS threads are host settings. This workstation ran native workers on E-cores only. The `--id` option launches selected preallocated rows, for instance `--id upper_cross-A-normal`. The `--retry` switch preserves the first attempt before appending another.

Independent verification reopens each USD replay, recomputes every TCP and workpiece pose, queries the actual probe-contact surface and workpiece dimensions, and classifies support from all four rotated foot corners plus speed. It also checks native jaw fits, the Franka link-pair exclusion mask, the swept bracket against mounted equipment, and the commanded release-to-tray fall. The protocol records checking limits before the new native outcomes.

The `summary.json` file links native evidence, raw input hashes and per-condition geometry audits. Each line of `observations.jsonl` describes one core-verified part and includes its settled input, fixture and output poses, tagged decision, probe contact, and independent drawing measurement. Use the packaging script to regenerate the local raw archive.

```powershell
python scripts/package_cell_c_extension.py --root work/reproduce-cell-c --summary-dir work/reproduce-cell-c-summary --out work/reproduce-cell-c-evidence.zip
```

The ZIP stores a manifest of individual member digests. Its receipt records verified member count, bytes and archive digest. Under the local workstation configuration the raw USD stages reference the installed vendor assets by path. Raw archive redistribution and robot asset licensing require separate author decisions. The compact observation file stores measurements and IDs as JSON.

The archived workstation run used `cell-c-local-native-replays.zip`, 78,917,482 bytes and 500 entries. The archive SHA-256 is `6e0b8ee2628ea8f6e30af647221bafa3c89ce6250349455fbccfbd82a14e13ac`. The 48-line compact observation file SHA-256 is `58226a833d3f7c8627a8ab9cf8436e5c3923ebbe6d4dfa81f22700ca7be92f51`.

## Measurement scope

The robot follows joint-limited kinematics. A virtual attachment carries the grasped part, then PhysX controls free release and settlement at 120 Hz. The comparator uses the workpiece mesh directly at the probe contact. The dimensional reference computes the part-local vertical span independently of the probe ray. Both share the synthetic part geometry. Physical gauge calibration, grasp forces and industrial safety margins need separate hardware evidence. Interference bounds apply to the sampled or interpolated poses reported by each checker.
