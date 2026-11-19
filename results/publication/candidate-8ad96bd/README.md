# Candidate 8ad96bd results

Windows ran 820 registered first attempts in Isaac Sim. Windows and WSL then replayed the sealed traces independently and agreed on every requirement verdict and first witness. All 80 healthy attempts with the reference controller (40 per cell) succeeded and passed the requirements. The full experiment also includes deliberate faults and controller mutants. A controlled abort can satisfy a safety requirement while leaving the task unfinished. [Platform verification](verification.json) · [Primary measurements and physical diagnosis](primary/README.md)

| Registered first attempts | Evaluable | Task succeeded | Controlled abort | Task failed | Compliance passed | Compliance failed |
|---|---:|---:|---:|---:|---:|---:|
| Cell A | 410 | 175 | 145 | 90 | 260 | 150 |
| Cell B | 410 | 160 | 215 | 35 | 340 | 70 |
| Both cells | 820 | 335 | 360 | 125 | 600 | 220 |

All 680 planned faults activated. The injected fault produced its specified effect in 670 attempts, and all 670 eligible paths subsequently met the restoration criterion. Across the 120 targeted mutant–reference pairs, 110 detected a mutant and 10 did not exercise their target. All six mutant types were detected at least once. The independent 144-case oracle corpus reproduced all 48 PASS, 48 FAIL, 24 UNKNOWN and 24 NOT_APPLICABLE labels. [Condition counts](tables/table3_conditions.csv) · [Fault coverage](tables/coverage_rates.csv) · [Matched pairs](tables/paired_mutants.csv) · [Oracle confusion matrix](tables/oracle_confusion.csv)

Seed 1000 had appeared in development. It accounts for 68 attempts and two distinct cell states. Seeds 1001–1019 account for 752 attempts and 38 cell states. The arms and controller mix differ between these two groups. [Seed exposure and denominators](tables/seed_exposure.csv) · [All first-attempt IDs, outcomes and witness hashes](tables/canonical_first_attempts.csv) · [Evidence checksums](tables/canonical_evidence_index.json)

## Publication figures

| Figure | Evidence |
|---|---|
| [1. Architecture and information boundaries](figures/figure1_architecture.pdf) | Runtime records and module ports. [Port inventory](tables/figure1_ports.json) |
| [2. Native workcells](figures/figure2_native_workcells.pdf) | Direct simulator frames from the fixed operator camera. Cell A is transferring its part at step 2440. Cell B is processing the clamped part at step 3736. [Frame and trace checksums](verification.json) |
| [3. Example failure timeline](figures/figure3_failure_timeline.pdf) | A Cell B F5 trace follows the clamp-closed signal through the injected interval. [Timeline source](figures/index.json) |
| [4. Fault realization, task success and compliance](figures/figure4_realization_task_compliance.pdf) | First-attempt counts by cell and condition. [Task and compliance data](tables/task_compliance.csv) |
| [5. Oracle and mutant validation](figures/figure5_oracle_mutant_validation.pdf) | Gold-label agreement and targeted pairs, including unexercised cases. [Figure inventory and digests](figures/index.json) |
| [6. Overhead, reset recovery and sustained memory use](figures/figure6_overhead_reset_soak.pdf) | 60 timing pairs, 48 reset chains and 200 measured episodes in one simulator process. [Source plans and figure hashes](figures/index.json) |

The candidate’s [module support](tables/table2_module_support.csv) and [environment and asset fingerprints](tables/table4_environment_assets.csv) document the implementation used for the measurements.

## Reset and runtime measurements

The reset suite ran 48 three-attempt chains across the two cells. All 48 met the physical initial-state tolerances, cleared software state and reproduced the control run’s goal and requirement verdicts after reset. Of the 144 attempts, 36 ended in the planned cancellation of the predecessor. The recovery denominator is 48 chains. [Per-chain initial-state errors, limits and attempt IDs](tables/reset-v1.json)

All 180 overhead trials completed. Windows and WSL agreed on every paired timing and performance distribution. Across 30 matched B1/B0 running-time pairs per cell, median overhead was 7.94% in Cell A and 8.00% in Cell B. The respective 95th percentiles were 10.51% and 11.26%. Both cells met the recommended 10% median and 20% 95th-percentile overhead limits. [Matched attempt IDs and paired distributions](tables/overhead-v1.json)

The recommended 3.33 ms critical-path budget per four physics steps was exceeded in both cells. In B1 and B2, the 99th percentile over all intervals ranged from 9.36 to 11.28 ms. Over controller-active intervals it ranged from 9.09 to 11.28 ms. Median real-time factors in these four groups ranged from 1.20 to 1.51 simulated seconds per wall second. [Per-mode measurements, sample sizes and report hashes](tables/overhead-runtime.json)

The soak used one simulator process from warmup through shutdown. All 200 measured episodes completed after 10 warmup episodes, with zero infrastructure errors. The measured window was 8,235.015 s (2 h 17 min 15 s). WSL verified all 210 sealed first attempts and the worker’s generation sequence. [Soak summary](tables/soak-v1.json) · [Per-episode resources, trace and seal hashes](tables/soak-episodes.csv) · [Evidence and replay hashes](tables/soak-evidence-index.json)

| End-of-episode memory, 200 measurements | Median of first 20 | Median of last 20 | Change | Theil–Sen slope per episode | 256 MiB growth alert |
|---|---:|---:|---:|---:|---|
| Process RSS | 4.870 GiB | 5.241 GiB | +379.53 MiB | +2.006 MiB | Triggered |
| Process-local VRAM (DXGI) | 2.179 GiB | 2.386 GiB | +212.00 MiB | +1.195 MiB | Clear |

## Reproducing the figures and tables

Set `CAMPAIGN` to the directory containing the four original suite directories. Run the following from the candidate checkout using the locked audit environment. The commands read the raw traces, replay the requirements and render the results on CPU. The fixed-camera directory contains the two native captures and their source index. The [canonical archive inventory](local-archive.json) identifies the raw data for the 820 primary attempts.

```sh
python scripts/reproduce_tables.py --campaign "$CAMPAIGN/canonical-v1" --output work/reproduced-tables
python scripts/reproduce_figures.py --campaign "$CAMPAIGN/canonical-v1" \
  --companion-campaign "$CAMPAIGN/reset-v1" --companion-campaign "$CAMPAIGN/overhead-v1" \
  --companion-campaign "$CAMPAIGN/soak-v1" --renders "$CAMPAIGN/release-evidence/fixed-camera" \
  --output work/reproduced-figures
```
