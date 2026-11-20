# Changelog

## 1.1.0.dev1 (development snapshot)

Cell C adds dimensional inspection, per-part result ownership, physical tray witnesses and a CPU-only example replay. Its simulator and independent USD checks use the `cellio inspection` command. The distribution includes original inspection equipment geometry and its authoring code. The simulator resolves the robot separately from the user's verified asset cache. [Cell C commands and model scope](docs/cell_c.md)

The extension has a separate prospective two-part experimental protocol. A retrospective audit of the Cell B F4 controller records also identifies whether a clamp-close command was issued at any point during each episode. [Cell C design](protocols/cell-c-softwarex-v1.json) · [Cell B command opportunities](results/publication/softwarex-extension/cell-b-f4-opportunities.json)

## 1.0.0

The workstation profile uses Windows 11 and a dedicated Conda Python 3.12 environment for simulation. WSL2 supplies a second Python runtime for offline verdict and witness comparison.

The registered evaluation sealed all 820 first attempts and reproduced each verdict and first witness on Windows and WSL2. Both nominal reference arms passed 40/40 trials. Cell A missed the stop dwell in 80 faulted trials. The paired evaluation distinguished all six injected controller defects. The archive receipt and analysis scripts accompany the results. [Evaluation and reproduction](results/publication/candidate-8ad96bd/primary/README.md)

The 48 reset chains met their software and physical criteria. The 180-run performance benchmark measured median matched runtime overhead of 7.94% in Cell A and 8.00% in Cell B. The measured four-step foreground time exceeded the recommended 3.33 ms at the 99th percentile in both cells. [Workstation measurements](results/publication/candidate-8ad96bd/README.md)

One worker completed ten warmups and 200 measured episodes over 2 h 17 min 15 s of measured time. Its working-set median increased by 379.5 MiB, exceeding the registered memory alert threshold. Its GPU memory median increased by 212 MiB. [Scope and limitations](docs/limitations.md)

The Cell A source baseline is tagged `cell-a-core-v1`. The commit history preserves the subsequent fixture workcell extension. Physical certification status is determined by the campaign artifacts and acceptance report for each candidate.
