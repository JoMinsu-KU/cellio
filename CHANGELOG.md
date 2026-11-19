# Changelog

## 1.0.0

The workstation profile uses Windows 11 and a dedicated Conda Python 3.12 environment for simulation. WSL2 supplies a second Python runtime for offline verdict and witness comparison.

The registered evaluation sealed all 820 first attempts and reproduced each verdict and first witness on Windows and WSL2. Both nominal reference arms passed 40/40 trials. Cell A missed the stop dwell in 80 faulted trials. The paired evaluation distinguished all six injected controller defects. The archive receipt and analysis scripts accompany the results. [Evaluation and reproduction](results/publication/candidate-8ad96bd/primary/README.md)

The 48 reset chains met their software and physical criteria. The 180-run performance benchmark measured median matched runtime overhead of 7.94% in Cell A and 8.00% in Cell B. The measured four-step foreground time exceeded the recommended 3.33 ms at the 99th percentile in both cells. [Workstation measurements](results/publication/candidate-8ad96bd/README.md)

One worker completed ten warmups and 200 measured episodes over 2 h 17 min 15 s of measured time. Its working-set median increased by 379.5 MiB, exceeding the registered memory alert threshold. Its GPU memory median increased by 212 MiB. [Scope and limitations](docs/limitations.md)

The Cell A source baseline is tagged `cell-a-core-v1`. The commit history preserves the subsequent fixture workcell extension. Physical certification status is determined by the campaign artifacts and acceptance report for each candidate.
