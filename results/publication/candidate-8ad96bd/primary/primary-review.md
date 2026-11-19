# Canonical first-attempt analysis

The primary analysis uses all registered first attempts. The secondary development-exposure disclosure compares two disjoint subsets of those same attempts. It was documented during development. Scene conditions repeat within a seed, and mutant coverage ends at seed 1004.

| Cohort | First attempts | Distinct cell–states | Reference / targeted mutant / healthy mutant | Task success / controlled abort / failed | Compliance pass / fail |
|---|---:|---:|---:|---:|---:|
| All 820 | 820 | 40 | 640 / 120 / 60 | 335 / 360 / 125 | 600 / 220 |
| Seed 1000 | 68 | 2 | 32 / 24 / 12 | 31 / 24 / 13 | 36 / 32 |
| Seeds 1001–1019 | 752 | 38 | 608 / 96 / 48 | 304 / 336 / 112 | 564 / 188 |

## Reference-controller outcomes

Each cell and arm contributes 40 attempts from the same 20 scene seeds with two repeats per seed.

| Cell | Arm | Success | Controlled abort | Failed | Compliance pass | Compliance fail |
|---|---|---:|---:|---:|---:|---:|
| cell_a | f1_high | 40 | 0 | 0 | 40 | 0 |
| cell_a | f1_low | 0 | 40 | 0 | 40 | 0 |
| cell_a | f2 | 0 | 0 | 40 | 0 | 40 |
| cell_a | f3 | 0 | 0 | 40 | 0 | 40 |
| cell_a | f4 | 40 | 0 | 0 | 40 | 0 |
| cell_a | f5 | 0 | 40 | 0 | 40 | 0 |
| cell_a | f6 | 0 | 40 | 0 | 40 | 0 |
| cell_a | healthy | 40 | 0 | 0 | 40 | 0 |
| cell_b | f1_high | 40 | 0 | 0 | 40 | 0 |
| cell_b | f1_low | 0 | 40 | 0 | 40 | 0 |
| cell_b | f2 | 0 | 40 | 0 | 40 | 0 |
| cell_b | f3 | 0 | 40 | 0 | 40 | 0 |
| cell_b | f4 | 40 | 0 | 0 | 40 | 0 |
| cell_b | f5 | 0 | 40 | 0 | 40 | 0 |
| cell_b | f6 | 0 | 40 | 0 | 40 | 0 |
| cell_b | healthy | 40 | 0 | 0 | 40 | 0 |

## Matched mutant detection

6 of 6 mutant types produced a targeted failure against a passing or inapplicable rule in its paired reference. Each pair used the same cell, physical state, fault arm and repeat. The 60 healthy mutant runs are diagnostic. The 120 faulted mutant episodes reuse matching reference episodes from the 640 reference rows.

| Model | Target pairs | Killed | Exercised without targeted failure | Missing exercise | Infra / ineligible |
|---|---:|---:|---:|---:|---:|
| mutant.c_m1 | 20 | 20 | 0 | 0 | 0 |
| mutant.c_m2 | 20 | 20 | 0 | 0 | 0 |
| mutant.c_m3 | 20 | 20 | 0 | 0 | 0 |
| mutant.c_m4 | 20 | 10 | 0 | 10 | 0 |
| mutant.c_m5 | 20 | 20 | 0 | 0 | 0 |
| mutant.c_m6 | 20 | 20 | 0 | 0 | 0 |

## Reference R06 abort-stop failures

| Cell | Fault arm | R06 failures / reference attempts | First violation step, min–max | Abort-to-first-violation steps, min–max |
|---|---|---:|---:|---:|
| cell_a | f2 | 40 / 40 | 1904–1920 | 72–72 |
| cell_a | f3 | 40 / 40 | 1904–1920 | 72–72 |

R06 failed in 80 of 640 reference attempts. The interval measures the time from the recorded abort declaration to the requirement's first failed deadline. The separate physical-settling analysis reconstructs the first qualifying dwell from the logged joint and belt velocities.

The accompanying JSON records each cell, controller and arm, including full task and compliance counts, the denominator for each group and R06 timing histograms. Source rows and matched pairs are indexed by attempt ID. The source index binds each included run to its sealed manifest and trace-chain digest.

[Measured physical settling following R06 failures](r06-physical-diagnosis.md) · [Windows and WSL report checks](report-comparison.json)
