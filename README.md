# CellIO

CellIO is a Python framework for testing robotic workcells in Isaac Sim and replaying their evidence on a CPU. It ships three scene plugins. Cell A transfers a part from a conveyor, Cell B clamps and processes a part, and Cell C measures and sorts two successive brackets. Each scene supplies its plant, controller, signals and acceptance rules. The shared engine runs the scenario, records commands and observations, and seals the evidence for independent evaluation.

## Replay on a CPU with the release package

In a Python 3.12 environment on Windows or Linux, install the [v1.1.1 release wheel](https://github.com/JoMinsu-KU/cellio/releases/tag/v1.1.1) and replay the two inspection examples bundled with it. These commands work from any directory.

```console
python -m pip install https://github.com/JoMinsu-KU/cellio/releases/download/v1.1.1/cellio-1.1.1-py3-none-any.whl
python -m cellio inspection replay --example normal
python -m cellio inspection replay --example stale_ok
```

If you downloaded the source distribution (`cellio-1.1.1.tar.gz`), run `python -m pip install ./cellio-1.1.1.tar.gz` in the download directory, then run the same two replay commands. The normal example reports `PASS`. The stale example reports `FAIL` because the second bracket consumed the first bracket's inspection result and reached the accept tray.

To re-evaluate eight full native records on a CPU, download `native-replay-v1.1.1.zip` from the same release and follow the [replay instructions](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/native_replay.md). The verifier checks 88 requirement verdicts and first witness steps. A separate ZIP contains 24 compact two-part cases. [Evidence and independent replay across three workcells](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/qualification/cell-c-framework/README.md)

## Run the R0 acceptance tests from a full checkout

R0 uses the repository's test suite and fixtures. Both the wheel and the source distribution omit the test suite. From a Python 3.12 environment on Windows or Linux, clone the release tag and install the development dependencies.

```console
git clone --branch v1.1.1 https://github.com/JoMinsu-KU/cellio.git cellio-v1.1.1
cd cellio-v1.1.1
python -m pip install -e ".[dev]"
python -m cellio acceptance --level r0 --strict --report work/r0.json
```

A successful run writes `PASS` for G01, G02 and G03 in `work/r0.json`. From the same checkout you can verify and evaluate the [36-step mock attempt](https://github.com/JoMinsu-KU/cellio/tree/v1.1.1/examples/traces/mock-36). The final placement goal expires at this short horizon. The [Windows verdicts](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/r0/replay-windows.json) and [WSL verdicts](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/r0/replay-linux.json) record the same source digest, rule digest and witness steps.

```console
python -m cellio verify-artifact --artifact examples/traces/mock-36
python -m cellio evaluate --artifact examples/traces/mock-36
```

## Run new simulations from the checkout

The simulator runs on Windows 11 in the dedicated Conda environment `cellio-isaac62`. WSL2 uses `cellio-audit` for offline verification. Both use CPython 3.12. The [implementation and evaluation specification](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/CellIO_Development_Specification_v1.0_20261118.md) includes the workstation profile in revision 1.1.

The [installation guide](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/installation.md) describes both runtimes. Follow the [Cell A](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/quickstart_cell_a.md) and [Cell B](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/quickstart_cell_b.md) quickstarts to run the simulator.

With the simulator environment and assets ready, run these commands from the checkout root. Choose a scenario in `examples/cell_a`, `examples/cell_b` or `examples/cell_c`, and use the attempt directory printed by `run` for the last two commands.

```console
cellio validate --config examples/cell_c/healthy.yaml
cellio run --config examples/cell_c/healthy.yaml --headless
cellio verify-artifact --artifact <attempt-directory>
cellio evaluate --artifact <attempt-directory>
```

The [inspection and sorting guide](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/cell_c.md) covers result ownership, tray placement and offline evidence. The [plugin contract](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/add_a_cell.md) describes how to register another workcell. The original 820-run A/B campaign and the two-part Cell C study retain their respective protocols and denominators.

## Registered workstation evaluation

On 19 November 2026, candidate `8ad96bd` finished all 820 registered first attempts in 14 h 12 min 32 s. Each attempt completed 7,200 physics steps. There were no retries, cancellations or infrastructure errors. Windows and WSL2 agreed on all 13,940 requirement verdicts and first witness steps. The reference results below count compliance with the requirements, including the required response to a fault. [Local archive checksum and size](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/candidate-8ad96bd/local-archive.json)

| Reference-controller condition | Trials | Task success | Controlled abort | Task failure | Compliance pass |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cell A, nominal | 40 | 40 | 0 | 0 | 40 |
| Cell B, nominal | 40 | 40 | 0 | 0 | 40 |
| Cell A, injected fault | 280 | 80 | 120 | 80 | 200 |
| Cell B, injected fault | 280 | 80 | 200 | 0 | 280 |

To regenerate these historical tables, use the original full campaign records and the analysis revision specified in the [archived campaign instructions](https://github.com/JoMinsu-KU/cellio/blob/main/docs/run_campaigns.md#reproduce-the-archived-820-run-ab-campaign). The release ZIP supports individual replay of two original A/B attempts alongside six v1.1.1 runs.

All 560 reference fault injections took effect and their normal paths recovered. Cell A missed the stop deadline in all 40 trials with a frozen gripper-closed signal and all 40 with a delayed signal. Cell B met the registered requirements in all 280 faulted trials. The 40 nominal trials per cell use two repeats of each of 20 initial states. One state per cell had been observed during development. [Scope and limitations](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/limitations.md)

The evaluation distinguished all six intentionally defective controller variants, exceeding the target of five. Across the 120 registered faulted mutant–reference pairs, 110 exposed a targeted requirement violation. In the remaining 10 pairs, the configured fault had no measured effect on the mutant. An additional 60 nominal mutant trials measured diagnostic behavior. [Primary analysis and paired results](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/candidate-8ad96bd/primary/primary-review.md) · [Windows and WSL2 comparison](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/candidate-8ad96bd/primary/report-comparison.json)

For the versioned acceptance decision, open the release summary attached to the matching GitHub release. It records the fifteen required gates, the separate clean-environment quickstarts and each recommended performance target. [Releases](https://github.com/JoMinsu-KU/cellio/releases)

## Qualification and development history

The Windows [physics probe](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/workstation/doctor-windows-drive.json) measures free fall and a driven prismatic joint. In separate full-horizon commissioning runs, Cell A transferred its part and Cell B clamped, processed and unloaded its part. Windows and WSL2 reproduced the first witness step of each requirement. The [commissioning record](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/docs/adr/002-gripper-commissioning.md) identifies the tuning conditions and the overlap of one initial state with the registered evaluation.

The workstation completed 19 headless development attempts of 7,200 steps each. The register retains the failed, aborted and successful trials and their hashes. On the evaluation candidate, a separate 120-step comparison measured one second of physical time in both GUI and headless modes, with zero observed difference in part position. [Workstation commissioning register](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/workstation/commissioning-register.json) · [GUI clock and trajectory comparison](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/workstation/gui-clock-8ad96bd.json)

The publication materials include native images of the Cell A transfer and Cell B clamping, a fault timeline, coverage by condition and the oracle confusion matrix. The earlier commissioning snapshot also retains the stuck-low belt example. [Registered figures and tables](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/candidate-8ad96bd/README.md) · [Commissioning figures](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/publication/README.md)

The [campaign guide](https://github.com/JoMinsu-KU/cellio/blob/main/docs/run_campaigns.md) explains how to collect the repeated nominal trials, check the boundary states, freeze the candidate, and resume the registered physical campaigns. The [experiment protocols](https://github.com/JoMinsu-KU/cellio/tree/v1.1.1/protocols) specify their cells, seeds and repeat counts.

The formal fixed-state commissioning suite completed 20 first attempts per cell on the workstation. All 40 reached 7,200 steps with task SUCCESS and compliance PASS. WSL2 independently reproduced every verdict and first witness across the 680 requirement records. The 20 repeats in each cell share one nominal physical state. [Frozen plan](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/qualification/commission-v1/campaign.plan.json) · [Replay summary and per-attempt checksums](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/qualification/commission-v1/summary.json)

Candidate `a6f3f9d` then completed 8 of 16 planned boundary attempts. All eight reached the placement goal. Seven passed compliance. On Cell A boundary 3, the measured part-to-tool displacement reached 18.942 mm at step 2,043, exceeding the 18 mm carry limit. The coordinator stopped before the pilot. WSL2 reproduced all 136 boundary requirement verdicts and first witnesses. [Candidate history, denominators and source hashes](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/qualification/candidate-a6f3f9d/README.md)

Candidate `8ad96bd` completed all 40 nominal trials and all 16 boundary trials with successful placement and passing compliance. In the 80-run pilot, 30 trials succeeded, 40 ended in a controlled abort, and 10 failed. All ten failures occurred in Cell A when the gripper's closed signal froze or arrived late. All 70 injected faults took effect and their normal paths recovered. Windows and WSL2 agreed on all 2,312 requirement verdicts and first witness steps across the 136 runs. [Qualification plans and replay evidence](https://github.com/JoMinsu-KU/cellio/blob/v1.1.1/results/qualification/candidate-8ad96bd/summary.json)
