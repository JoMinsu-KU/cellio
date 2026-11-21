# Related workcell and I/O simulators

The comparison uses the three implementations named in specification §2.4. Each entry links to the source revision examined on 18 November 2026.

| Project | What the published implementation models | Evidence relevant to CellIO |
| --- | --- | --- |
| isaac-plc-digital-twin | A robot in Isaac Sim exchanges state with a PLC through a bridge. | The project describes heartbeat and watchdog handling, configuration tests and latency probes. These provide a comparison for signal mapping and timing diagnostics. [Project README](https://github.com/Nebras4u/isaac-plc-digital-twin/blob/f250615c084be85ff66334b61b0bdddb3e80b06a/README.md) |
| industrial-automation-cell-simulator | A simulated C++ plant supplies sensor feedback to an OpenPLC sequence with interlocks and latched alarms. | Tag maps, snapshots, event logs and alarm history expose controller and plant behavior. The project provides CTest instructions for its regression tests. [Project README](https://github.com/rgcb01/industrial-automation-cell-simulator/blob/a1098dcaf5222049366da11cbd326720882ce5da/README.md) |
| otlab-sim | A YAML process specification defines tags, units, simulated changes over time and scheduled signal faults. | Its two protocol interfaces expose the same simulated process. Its documented stuck-value case supplies a concrete reference for a frozen observation. [Project README](https://github.com/Furiadelimon/otlab-sim/blob/ec74003d2f0269b03dcc9d7f46d31c5839dfc533/README.md) |

CellIO evaluates observations, commands and joint targets against the same simulated Franka episode and records separate physical-state evidence. The three projects use different scenes, controllers and clocks. Their reported timing and success rates therefore cannot establish a paired performance difference on the CellIO workstation. Within-project comparisons use paired input states in the overhead protocol and the researcher-authored manual scripts. [Overhead protocol](../protocols/overhead-v1.yaml) · [Manual comparison implementation](../scripts/compare_manual.py)

For the native Isaac comparison, we ran the manual and modular implementations in separate processes at pilot seed 0. Cell A used F1 stuck high and Cell B used F3. In both pairs, all 7,201 recorded time points matched for raw and observed signals, issued and applied commands, and the number of records on each channel. The independent requirement verdicts and first witness steps also agreed. The comparison files include both attempt IDs, input identities and script hashes. [Cell A native results](../results/workstation/manual-baseline-a-f1-high.json) · [Cell B native results](../results/workstation/manual-baseline-b-f3.json)

## Recorded evidence and runtime monitoring

| Tool and scope | Input and check | What the user can inspect |
| --- | --- | --- |
| Metriplane v0.2.0 | Turns recorded workcell data into events and evidence bundles and checks event evidence across replays. | A reader can trace an event in a bundle and compare replay outputs. [SoftwareX article](https://doi.org/10.1016/j.softx.2026.103104) · [Published-version regression code](https://github.com/Miko997/metriplane/blob/v0.2.0/metriplane/atlas/regression.py) |
| RTAMT | Evaluates Signal Temporal Logic specifications against input signals, supporting online and offline monitoring. | A user can examine how robustly a signal satisfies a temporal formula. [RTAMT article](https://doi.org/10.1007/s10009-023-00720-3) |
| CellIO v1.1.1 | Runs a workcell controller under scheduled faults and seals the observations, issued and applied commands, and rule specification for replay. | On the recorded A/B cases, a reader can recover the first missed stop deadline or check whether the controller issued a command that the configured delay could affect. On C, a reader can compare the inspected part, result owner and observed tray. [Eight-case replay procedure](native_replay.md) |

The comparisons describe the documented operations of each tool. The CellIO experiments measure the three workcells under the registered protocols. No cross-tool timing measurements were collected.
