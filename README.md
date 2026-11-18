# CellIO

CellIO tests how a robotic workcell responds to sensor, command-delivery, and actuator faults. The development contract defines two Franka workcells, independent verdicts, evidence recording, and a campaign with 820 first attempts.

The simulator runs on Windows 11 in the dedicated Conda environment `cellio-isaac62`. WSL2 uses `cellio-audit` for offline verification. Both use CPython 3.12. The [implementation and evaluation specification](./CellIO_Development_Specification_v1.0_20261118.md) includes the workstation profile in revision 1.1.

The [installation guide](docs/installation.md) describes both runtimes. Follow the [Cell A](docs/quickstart_cell_a.md) and [Cell B](docs/quickstart_cell_b.md) quickstarts to run the simulator.

The [workstation smoke result](results/workstation/doctor-windows-freefall.json) records 120 PhysX steps on Windows. A cube fell from 10.000 m to 5.135 m over one simulated second. The [experiment protocols](protocols) specify which cells, seeds and repeats enter each campaign.

A small Windows-generated [mock attempt](examples/traces/mock-36) is available for offline replay. It contains 36 physics decisions. The final placement goal expires at this short horizon. The [Windows verdicts](results/r0/replay-windows.json) and [WSL verdicts](results/r0/replay-linux.json) record the same source digest, rule digest and witness steps.

```sh
python -m cellio verify-artifact --artifact examples/traces/mock-36
python -m cellio evaluate --artifact examples/traces/mock-36
python -m cellio acceptance --level r0 --report work/r0.json
```
