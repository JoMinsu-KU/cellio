# Canonical evaluation, candidate 8ad96bd

The registered primary population contains 820 native Windows simulator runs. Each planned row has one completed first attempt. Windows and WSL independently replayed all 820 and agreed on every requirement verdict and first witness. The 68/752 split describes prior development exposure to scene seed 1000. Both groups reuse physical initial states across arms and repeats. [Primary results](primary-review.md) · [Platform comparison](report-comparison.json)

The secondary physical analysis examines the 80 Cell A reference attempts that failed R06 under observation freezing or delay. Command receipts, joint velocities and belt speeds establish when the stop command was applied and when the simulated plant first met the registered stopping criterion. [Stop timing and physical measurements](r06-physical-diagnosis.md)

| Evidence | Contents |
|---|---|
| [Primary measurements](primary-review.json) | Counts and denominators for the full population and both disclosure groups, with outcomes and compliance by cell, controller and fault arm |
| [First-attempt index](first-attempt-index.json) | All 820 planned identities and first-attempt IDs, with hashes of configurations, states, rules, witnesses, sealed manifests and traces |
| [Matched mutant pairs](matched-mutant-pairs.json) | The 120 targeted mutant attempts paired to the reference for the same cell, physical state, arm and repeat, including rule witnesses and unexercised cases |
| [Coordinator events](coordinator-first-attempts.jsonl) | The 820 first-attempt completion events in their recorded order, with native timestamps and identities |
| [Physical evidence](r06-physical-diagnosis.json) | Stop receipts, dwell intervals, velocity exceedances and trace record hashes for the 80 diagnosed attempts |
| [File checksums](checksums.sha256) | SHA-256 of the eight generated evidence files |

## Reproduction

Run the following from the repository root with the candidate's CellIO package installed. Supply the extracted canonical raw campaign and the final Windows and WSL reports. The two reports can also be produced by `cellio report` on their respective platforms. The publication scripts read completed evidence and use the existing candidate comparison and rule code. They require no simulator startup.

```sh
python scripts/publish_primary.py \
  --campaign /path/to/canonical-v1 \
  --qualification results/qualification/candidate-8ad96bd/summary.json \
  --journal results/publication/candidate-8ad96bd/primary/coordinator-first-attempts.jsonl \
  --windows /path/to/windows-report/metrics.json \
  --wsl /path/to/wsl-report/metrics.json \
  --output work/primary-reproduction

python scripts/diagnose_primary_settling.py \
  --campaign /path/to/canonical-v1 \
  --primary work/primary-reproduction

(cd work/primary-reproduction && sha256sum -c ../../results/publication/candidate-8ad96bd/primary/checksums.sha256)
```

The original coordinator's complete `journal.jsonl` is also accepted. The exporter selects its canonical first-attempt records and verifies them against the ledger, sealed attempt summaries and platform reports. The physical analysis hashes each selected compressed trace and inspects every boundary from abort through the first completed dwell or the horizon. Full trajectories are retained in the workstation archive. [Archive digest and entry counts](../local-archive.json)

All files use UTF-8 without a byte-order mark. The first-attempt and matched-pair indexes retain the original CRLF bytes, which preserves their earlier SHA-256 values. The remaining generated files and published analysis scripts use LF.
