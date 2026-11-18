# CellIO

CellIO tests how a robotic workcell responds to sensor, command-delivery, and actuator faults. The development contract defines two Franka workcells, independent verdicts, evidence recording, and a campaign with 820 first attempts.

The simulator runs on Windows 11 in the dedicated Conda environment `cellio-isaac62`. WSL2 uses `cellio-audit` for offline verification. Both use CPython 3.12. The [implementation and evaluation specification](./CellIO_Development_Specification_v1.0_20261118.md) includes the workstation profile in revision 1.1.

The [installation guide](docs/installation.md) describes both runtimes. Follow the [Cell A](docs/quickstart_cell_a.md) and [Cell B](docs/quickstart_cell_b.md) quickstarts to run the simulator.

The Windows [physics probe](results/workstation/doctor-windows-drive.json) measures free fall and a driven prismatic joint. In separate full-horizon commissioning runs, Cell A transferred its part and Cell B clamped, processed and unloaded its part. Windows and WSL2 reproduced the first witness step of each requirement. The [commissioning record](docs/adr/002-gripper-commissioning.md) identifies the tuning conditions and the overlap of one initial state with the registered evaluation.

The [campaign guide](docs/run_campaigns.md) explains how to collect the repeated nominal trials, check the boundary states, freeze the candidate, and resume the registered physical campaigns. The [experiment protocols](protocols) specify their cells, seeds and repeat counts.

A small Windows-generated [mock attempt](examples/traces/mock-36) is available for offline replay. It contains 36 physics decisions. The final placement goal expires at this short horizon. The [Windows verdicts](results/r0/replay-windows.json) and [WSL verdicts](results/r0/replay-linux.json) record the same source digest, rule digest and witness steps.

```sh
python -m cellio verify-artifact --artifact examples/traces/mock-36
python -m cellio evaluate --artifact examples/traces/mock-36
python -m cellio acceptance --level r0 --report work/r0.json
```
