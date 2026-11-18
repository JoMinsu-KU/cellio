# CellIO

CellIO tests how a robotic workcell responds to sensor, command-delivery, and actuator faults. The development contract defines two Franka workcells, independent verdicts, evidence recording, and a campaign with 820 first attempts.

The simulator runs on Windows 11 in the dedicated Conda environment `cellio-isaac62`. WSL2 uses `cellio-audit` for offline verification. Both use CPython 3.12. The [implementation and evaluation specification](./CellIO_Development_Specification_v1.0_20261118.md) includes the workstation profile in revision 1.1.

The [installation guide](docs/installation.md) describes both runtimes. Follow the [Cell A](docs/quickstart_cell_a.md) and [Cell B](docs/quickstart_cell_b.md) quickstarts to run the simulator.

The Windows [physics probe](results/workstation/doctor-windows-drive.json) measures free fall and a driven prismatic joint. In separate full-horizon commissioning runs, Cell A transferred its part and Cell B clamped, processed and unloaded its part. Windows and WSL2 reproduced the first witness step of each requirement. The [commissioning record](docs/adr/002-gripper-commissioning.md) identifies the tuning conditions and the overlap of one initial state with the registered evaluation.

The workstation completed 19 headless development attempts of 7,200 steps each. Using the observed mean for each cell, 410 attempts per cell would take about 13.82 hours from supervisor launch through closure and produce 9.64 GiB of sealed artifacts. The register retains the failed, aborted and successful trials, their hashes, and the GUI clock and gripper measurements. [Workstation commissioning register](results/workstation/commissioning-register.json)

The publication materials include the actual Cell A transfer and Cell B clamping images, a trace of a stuck-low belt observation, and the independent oracle confusion matrix. [Figures and tables](results/publication/README.md)

The [campaign guide](docs/run_campaigns.md) explains how to collect the repeated nominal trials, check the boundary states, freeze the candidate, and resume the registered physical campaigns. The [experiment protocols](protocols) specify their cells, seeds and repeat counts.

The formal fixed-state commissioning suite completed 20 first attempts per cell on the workstation. All 40 reached 7,200 steps with task SUCCESS and compliance PASS. WSL2 independently reproduced every verdict and first witness across the 680 requirement records. The 20 repeats in each cell share one nominal physical state. [Frozen plan](results/qualification/commission-v1/campaign.plan.json) · [Replay summary and per-attempt checksums](results/qualification/commission-v1/summary.json)

Candidate `a6f3f9d` then completed 8 of 16 planned boundary attempts. All eight reached the placement goal. Seven passed compliance. On Cell A boundary 3, the measured part-to-tool displacement reached 18.942 mm at step 2,043, exceeding the 18 mm carry limit. The coordinator stopped before the pilot. WSL2 reproduced all 136 boundary requirement verdicts and first witnesses. [Candidate history, denominators and source hashes](results/qualification/candidate-a6f3f9d/README.md)

Candidate `8ad96bd` completed all 40 nominal trials and all 16 boundary trials with successful placement and passing compliance. In the 80-run pilot, 30 trials succeeded, 40 ended in a controlled abort, and 10 failed. All ten failures occurred in Cell A when the gripper's closed signal froze or arrived late. All 70 injected faults took effect and their normal paths recovered. Windows and WSL2 agreed on all 2,312 requirement verdicts and first witness steps across the 136 runs. [Qualification plans and replay evidence](results/qualification/candidate-8ad96bd/summary.json)

A small Windows-generated [mock attempt](examples/traces/mock-36) is available for offline replay. It contains 36 physics decisions. The final placement goal expires at this short horizon. The [Windows verdicts](results/r0/replay-windows.json) and [WSL verdicts](results/r0/replay-linux.json) record the same source digest, rule digest and witness steps.

```sh
python -m cellio verify-artifact --artifact examples/traces/mock-36
python -m cellio evaluate --artifact examples/traces/mock-36
python -m cellio acceptance --level r0 --report work/r0.json
```
