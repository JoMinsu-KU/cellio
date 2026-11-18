# ADR 001 — Windows workstation execution

Date: 2026-11-18. This decision supersedes the Ubuntu workstation installation in specification revision 1.0.

The reference machine boots Windows 11 Pro, has an i9-14900K, 64 GiB RAM and a 24 GiB RTX 4090. The project checkout is on a SATA HDD. The C: volume is NVMe. Windows Conda owns the Isaac Sim 6.2.0.0 installation, simulation subprocesses and active traces. WSL2 Ubuntu 22.04.5 uses a separate Python 3.12 environment to evaluate finalized evidence. Only paths to completed artifacts cross the operating system boundary.

The original Linux bundle required a separate OS installation. Running Kit inside WSL2 would introduce a different graphics stack. Native Windows and the documented Python 3.12 packages give the existing machine an official Isaac Sim installation route. The exact Windows build, driver, package versions, PhysX configuration, calibration and assets belong to the measured run manifest.

The experimental clock, six fault definitions, independent oracle, sampling distributions, campaign sizes and acceptance thresholds follow the original protocol. Results from a previous simulator version cannot satisfy the new environment gate. The original specification is preserved in commit `e76f586`.
