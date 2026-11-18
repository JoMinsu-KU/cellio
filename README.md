# CellIO

CellIO tests how a robotic workcell responds to sensor, command-delivery, and actuator faults. The development contract defines two Franka workcells, independent verdicts, evidence recording, and a campaign with 820 first attempts.

The simulator runs on Windows 11 in the dedicated Conda environment `cellio-isaac62`. WSL2 uses `cellio-audit` for offline verification. Both use CPython 3.12. The [implementation and evaluation specification](./CellIO_Development_Specification_v1.0_20261118.md) includes the workstation profile in revision 1.1.

Development and physical commissioning are in progress. The [installation guide](docs/installation.md) describes the two runtimes.
