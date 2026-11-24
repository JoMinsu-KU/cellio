# Running the workstation protocol

Use the Windows Conda environment with the commissioned Isaac Sim installation. The data root holds local assets, doctor output, dependency locks and the measured robot calibration. Place the campaign root on a volume with enough space for the raw attempts and the storage allowance estimated from the ten pilots. See [installation](installation.md) and the [Cell A commissioning quickstart](quickstart_cell_a.md).

```powershell
conda activate cellio-isaac62
$campaignRoot = Join-Path $env:LOCALAPPDATA 'CellIO\campaigns\candidate-01'
python scripts/run_workstation_protocol.py --campaign-root $campaignRoot --phase commission-v1
python scripts/run_workstation_protocol.py --campaign-root $campaignRoot --phase all
```

The `all` phase uses this order.

| Phase | Work and evidence |
| --- | --- |
| `commission-v1` | Run the 20 nominal trials per cell. Check each first attempt for full horizon, successful task, passing compliance and agreement with offline replay. |
| `boundaries-v1` | Check all eight prespecified boundary states per cell on the same candidate. |
| `pilot-v1` | Execute five states per cell and arm. Check replay, realization and restoration for each family and the physical hold evidence for F6. |
| `freeze` | Run the existing freeze tool. Its protocol records candidate identity, calibrated robot hash, suite designs, commissioning trace hashes and measured storage. |
| `canonical-v1` | Execute the registered 820 first attempts, including mutant and injected-fault arms, and generate the Windows report in `canonical-v1/reports`. Their observed task and compliance failures are research outcomes. |
| `reset-v1` | Execute each control and each predecessor/successor pair in plan order. Verify the planned cancellations and 48 three-attempt comparisons. |
| `overhead-v1` | Collect the 180 registered timing trials and build the paired report in the suite's `reports` directory. |
| `soak-v1` | Run the registered generator in one worker. Validate its ledger, PID, generation sequence, timing and closure against the soak evidence. |

Select a single phase to advance only that phase. Earlier phases must already have valid evidence under the same campaign root. A new invocation writes its command journal and separate stdout/stderr logs in `protocol-runs/<invocation-id>/`. The console prints the journal entries, suite plan hash, ledger location and the verified first-attempt IDs. Live worker heartbeats and logs are in each suite's `.workers` directory.

For example, these commands check the prerequisite evidence and advance the pilot or frozen campaign.

```powershell
python scripts/run_workstation_protocol.py --campaign-root $campaignRoot --phase pilot-v1
python scripts/run_workstation_protocol.py --campaign-root $campaignRoot --phase canonical-v1
```

Repeat the same invocation after interruption. The runner reads the existing plan and ledger, verifies the first attempt for each recorded run and skips verified complete work. A failed commissioning first attempt stops progression. The journal identifies the attempt for diagnosis. Start a changed candidate under a distinct campaign root with its own matching candidate locks. Existing traces, failed attempts and retry lineage are retained. An already frozen protocol must match the selected pre-freeze evidence. A complete soak is audited on re-entry. An interrupted soak retains its files and requires a distinct experiment directory for a new uninterrupted measurement.

The clean installation is an explicit operation. Choose a new work directory outside the checkout. The reproduction tool creates a fresh Conda prefix, uses its package sources and records each command, wheel identity, environment and both five-run quickstarts. Completed clean-install evidence is audited on repeated invocation.

If a dependency download fails before the suite has a plan, repeat the same command. It archives the failed command record, retains its original logs and creates a fresh prefix in a new directory. The runtime probe checks the active Conda prefix and its Windows DLL directory. Once the clean-install plan exists, re-entry audits that campaign and preserves every first attempt.

```powershell
python scripts/run_workstation_protocol.py --phase clean-install --campaign-root $campaignRoot --clean-work-dir C:\CellIO-reproduction\candidate-01
```

## Contract, reuse and cross-platform evidence

Run R0 from the release checkout in each environment. Record each report under a distinct name. R0 runs the contract tests and frozen oracle fixtures and can run before any physical campaigns.

```powershell
python -m cellio acceptance --level r0 --strict --report "$campaignRoot/release-evidence/r0-windows.json"
```

G12 tests the committed Cell B change against the tagged Cell A baseline and saves the isolated regression log below the campaign root. The optional `reuse-v1` experiment measures the prespecified Cell A conveyor variant in 40 physical trials. It runs on explicit selection after the freeze.

```powershell
python -m cellio.release reuse --campaign-root $campaignRoot
python scripts/run_workstation_protocol.py --phase reuse-v1 --campaign-root $campaignRoot
```

After the registered canonical campaign completes, open the same checkout in WSL, activate `cellio-audit`, and set the path to the Windows campaign root under `/mnt/c`. The WSL report verifies the closed raw files and replays each obligation. The comparison checks the full plan, first-attempt ledger, source and rule identities, and the verdict/first-witness digest from both operating systems. It exits with a failure when any row differs. Each operating system keeps its own report.

```sh
windows_local_appdata=$(powershell.exe -NoProfile -Command '$env:LOCALAPPDATA' | tr -d '\r')
campaign_root="$(wslpath -u "$windows_local_appdata")/CellIO/campaigns/candidate-01"
python -m cellio acceptance --level r0 --strict --report "$campaign_root/release-evidence/r0-wsl.json"
python -m cellio report --campaign "$campaign_root/canonical-v1" --output "$campaign_root/release-evidence/canonical-wsl"
python scripts/compare_platform_replays.py --campaign "$campaign_root/canonical-v1" --windows "$campaign_root/canonical-v1/reports/metrics.json" --wsl "$campaign_root/release-evidence/canonical-wsl/metrics.json" --output "$campaign_root/release-evidence/canonical-platform-comparison.json"
```

To reproduce the publication tables and plots from the registered rows on Ubuntu, use its `cellio-audit` environment with the `plots` extra. The drawing code uses a noninteractive renderer. It exports the measured figures as PDF and PNG.

```sh
python scripts/reproduce_tables.py --campaign "$campaign_root/canonical-v1" --output "$campaign_root/release-evidence/canonical-wsl"
python scripts/reproduce_figures.py --campaign "$campaign_root/canonical-v1" --output "$campaign_root/release-evidence/canonical-wsl"
python scripts/reproduce_figures.py --campaign "$campaign_root/overhead-v1" --output "$campaign_root/release-evidence/overhead-wsl"
```

An oracle-only campaign also regenerates the interface diagram, the checked-in native camera views, the gold confusion matrix, and Tables 2 and 4. Supply `--timeline-attempt <sealed-attempt-directory>` to include a separately identified commissioning trace in Figure 3. The workstation snapshot used Cell A attempt `5efc632a-4535-42b0-82da-666a6162b9ef`. The output `figures/index.json` identifies the available inputs, measured panels and file hashes. [Workstation figures and tables](../results/publication/README.md)

### Reproduce the archived 820-run A/B campaign

The published condition counts were generated with commit `8ad96bdfb04e29e3ad78c51c3776ecebee61eecf`. Rebuilding them requires the original `canonical-v1` directory with its frozen plan, ledger and all 820 sealed first attempts. The [public data catalog](https://github.com/JoMinsu-KU/cellio/releases/download/v1.1.1/cellio-original-records.json) lists the `ab` group containing the entire campaign. The [Python downloader](https://github.com/JoMinsu-KU/cellio/releases/download/v1.1.1/cellio-original-records.py) verifies each part and the reconstructed ZIP64 archive. The archive inventory also records the plan and ledger digests. [Archive inventory](../results/publication/candidate-8ad96bd/local-archive.json) · [Published counts and tables](../results/publication/candidate-8ad96bd/README.md)

For a shorter CPU replay, use the v1.1.1 wheel and the eight recordings in `native-replay-v1.1.1.zip`. Six recordings were made with v1.1.1. The two historical A/B recordings reproduce their recorded verdicts and first witnesses under v1.1.1, but predate recorded rule identities. The v1.1.1 campaign aggregator therefore treats those historical outcomes as `UNKNOWN`. [Replay the eight recordings](native_replay.md)

With the original data, start in a separate directory on x86-64 Ubuntu or WSL. Use the historical checkout and its own Python 3.12 environment so that both the publication scripts and the imported evaluator come from the same commit.

```sh
git clone --no-checkout https://github.com/JoMinsu-KU/cellio.git cellio-8ad96bd
cd cellio-8ad96bd
git checkout --detach 8ad96bdfb04e29e3ad78c51c3776ecebee61eecf
conda create -n cellio-audit-8ad96bd -c conda-forge python=3.12.16 pip
conda activate cellio-audit-8ad96bd
python -m pip install --require-hashes -r locks/wsl-audit/requirements.lock
python -m pip install --no-deps -e .

downloads="$PWD/../cellio-records"
campaign_root="$PWD/../candidate-8ad96bd"
mkdir -p "$downloads" "$campaign_root/canonical-v1"
curl -fL https://github.com/JoMinsu-KU/cellio/releases/download/v1.1.1/cellio-original-records.py -o "$downloads/cellio-original-records.py"
python "$downloads/cellio-original-records.py" ab --directory "$downloads"
python -m zipfile -e "$downloads/canonical-v1.zip" "$campaign_root/canonical-v1"
python scripts/reproduce_tables.py --campaign "$campaign_root/canonical-v1" --output work/reproduced-tables
```

Compare `work/reproduced-tables/tables/table3_conditions.csv` with the [published condition counts](../results/publication/candidate-8ad96bd/tables/table3_conditions.csv). Across all conditions, 820 first attempts were evaluable, 410 per cell. Figure 6 uses three additional public groups: `overhead` (180 attempts), `reset` (48 three-attempt chains) and `soak` (ten warmups and 200 measured episodes). Each performance ZIP contains whole attempts in the original suite layout. Extract all ZIPs for each group into the same campaign root. Figure 2 also requires the two fixed-camera PNGs and their capture index. Figure 4 uses the canonical controller and fault arms.

```sh
for group in overhead reset soak; do
  python "$downloads/cellio-original-records.py" "$group" --directory "$downloads"
  for archive in "$downloads"/cellio-perf-"$group"-*.zip; do
    python -m zipfile -e "$archive" "$campaign_root"
  done
done

python scripts/reproduce_figures.py --campaign "$campaign_root/canonical-v1" \
  --companion-campaign "$campaign_root/overhead-v1" --companion-campaign "$campaign_root/reset-v1" \
  --companion-campaign "$campaign_root/soak-v1" --renders "$campaign_root/release-evidence/fixed-camera" \
  --output work/reproduced-figures
```

## Package a completed campaign

To package a completed canonical campaign for transfer, run the following from the release checkout. The ZIP64 archive carries the actual plan, ledger, attempt manifests and data streams. The packager verifies every first-attempt file against its archived bytes. The release manifest binds the archive digest to the source tag and dependency locks.

```powershell
python scripts/package_raw_campaign.py --campaign "$campaignRoot/canonical-v1" --output "$campaignRoot/canonical-v1.zip"
python -m cellio.release manifest --campaign-root $campaignRoot --tag v1.0.0 --raw-archive "$campaignRoot/canonical-v1.zip"
python -m cellio acceptance --level r3 --strict --campaign-root $campaignRoot --report "$campaignRoot/release-evidence/r3-windows.json"
```

The final Windows command reads the first-attempt ledgers, the overhead report, the sealed soak, the versioned raw package, the tagged Cell A reuse audit and the separate clean installation. A release requires all fifteen gates to pass. Diagnostic runs in the data root's `commissioning` directory document tuning. Formal commissioning begins with `<campaign-root>/commission-v1/campaign.plan.json` and its first-attempt ledger.
