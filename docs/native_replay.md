# Replay eight native Isaac Sim recordings on a CPU

Each directory in `native-replay-v1.1.1.zip` holds one complete 7,200-step recording. Six runs use the v1.1.1 common framework. Two original first attempts from the canonical A/B campaign exercise questions that the six-case qualification does not cover. The archive includes the traces, commands, events, requirement results, manifests and other sealed files that `cellio verify-artifact` checks. Cell C also includes the inspection and motion observations used to recover result ownership and settled tray. Replay requires Python 3.12 and CellIO. Isaac Sim and a GPU are required only for collecting new native runs.

## Run the replay

Download the wheel and `native-replay-v1.1.1.zip` from the [v1.1.1 release](https://github.com/JoMinsu-KU/cellio/releases/tag/v1.1.1). Compare the archive SHA-256 to the release checksum before extracting. In a Python 3.12 environment, run the following commands from the download directory.

```sh
python -m pip install ./cellio-1.1.1-py3-none-any.whl
python -m zipfile -e native-replay-v1.1.1.zip native-replay
python native-replay/verify_native_replays.py --output replay-results.json
```

A successful run reports `verified_cases: 8` and `matched_requirements: 88`. Each match covers the requirement verdict and first witness step, including a null step when a rule had no witness. The script checks the sealed inventory and trace chain before recomputing each verdict. It also checks the native Cell C owners and final trays, the Cell A dwell, and the absence of a Cell B clamp-close opportunity. Expect several minutes of CPU work. To inspect individual results, run:

```sh
cellio verify-artifact --artifact native-replay/cell_c_both_good_stale
cellio evaluate --artifact native-replay/cell_c_both_good_stale --recorded-rules
cellio evaluate --artifact native-replay/cell_a_stop_dwell
```

| Directory | Task | Compliance | Evidence |
|---|---|---|---|
| `cell_a_healthy` | SUCCESS | PASS | Healthy pick-and-place, 16 requirements |
| `cell_b_healthy` | SUCCESS | PASS | Healthy machine tending, 18 requirements |
| `cell_c_upper_reference` | SUCCESS | PASS | P1 and P2 use their own results and settle in OK and NG |
| `cell_c_upper_stale` | SUCCESS | FAIL | P2 uses P1's result and settles in OK instead of NG |
| `cell_c_both_good_reference` | SUCCESS | PASS | Both use their own results and settle in OK |
| `cell_c_both_good_stale` | SUCCESS | FAIL | Both settle in OK, but P2 uses P1's result |
| `cell_a_stop_dwell` | FAILED | FAIL | R06 stop dwell completes after its deadline |
| `cell_b_unexercised_clamp` | CONTROLLED_ABORT | FAIL | F4 trigger matches, with zero clamp-close commands and zero delayed packets |

The six new recordings seal the rules and the evaluator identity. Their 54 verdict-and-witness pairs have a verified match between recording and replay. The two earlier recordings predate the rule lock. The installed v1.1.1 evaluator reproduces their 34 recorded pairs and the witness digests in the [published first-attempt index](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/candidate-8ad96bd/primary/first-attempt-index.json). Recording-time rule identity and identity-qualified agreement are reported as `null` for these two recordings.

Cell A's canonical reference-controller F2 attempt aborted at boundary 1,840. R06 failed at its deadline, 1,912. The first 13 consecutive quiet boundaries completed the required 12-interval dwell at 1,938, 26 steps (about 217 ms at 120 Hz) late. In Cell B's canonical F4 attempt, the trigger matched at 3,273. The controller issued no clamp-close command during the recording, leaving coverage at `NOT_EXERCISED`. Task and compliance are separate from the fault coverage classification.

## Selection and provenance

The root `index.json` supplies the run and attempt IDs, final manifest SHA-256, final trace-chain hash and canonical hash of the verdict-and-first-witness mapping for each case. It also records the input index and qualification report digests. The [six-case design](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/protocols/three-cell-framework-v1.json) sets the expected healthy and stale-result conditions. The historical examples retain their original manifest and file checksums.

To rebuild the selection from the original local campaign directories and a checked six-case report, use the packaging script in the v1.1.1 checkout. The canonical argument points to the directory containing the canonical run-ID directories.

```sh
python scripts/package_native_replays.py --runs work/release-1.1.1/native-runs --summary work/release-1.1.1/three-cell-results.json --canonical PATH_TO_CANONICAL_RUNS --output native-replay-v1.1.1.zip
```
