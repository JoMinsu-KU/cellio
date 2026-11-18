"""Run the registered workstation campaign sequence and verify its first attempts."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from time import time_ns
from uuid import uuid4

from cellio.analysis.campaign import attempt_histories, lines, load_campaign, read_trial
from cellio.analysis.comparisons import overhead
from cellio.analysis.suites import fault_coverage, reset_chains, soak_evidence
from cellio.environment import data_root, lease_directory, lock_context
from cellio.io import CellIOError, digest
from cellio.reporting import attempt_replay
from cellio.runtime.campaign import (
    append_line,
    exclusive,
    finite_rows,
    read_plan,
    suite_design,
)
from cellio.runtime.supervisor import TERMINAL, reconcile

SOURCE = Path(__file__).resolve().parents[1]
PREFREEZE = ("commission-v1", "boundaries-v1", "pilot-v1")
MEASURED = ("canonical-v1", "reset-v1", "overhead-v1", "soak-v1")
ORDER = (*PREFREEZE, "freeze", *MEASURED)


def require(condition, message):
    if not condition:
        raise CellIOError("RUN_PROTOCOL_BLOCKED", message, exit_code=5)


def read(path):
    return json.loads(path.read_bytes())


class Protocol:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.logs = self.root / "protocol-runs" / str(uuid4())
        self.logs.mkdir(parents=True)
        self.commands = 0
        self.frozen = data_root() / "locks" / "protocol.lock.json"
        self.preflight = {}

    def log(self, kind, **fields):
        value = {"event": kind, "unix_ns": time_ns(), **fields}
        append_line(self.logs / "journal.jsonl", value)
        print(json.dumps(value, ensure_ascii=True), flush=True)

    def invoke(self, label, arguments):
        self.commands += 1
        stem = f"{self.commands:05d}-{label}"
        stdout = self.logs / (stem + ".stdout.log")
        stderr = self.logs / (stem + ".stderr.log")
        args = [sys.executable, *map(str, arguments)]
        self.log("launch", command=args, stdout=str(stdout), stderr=str(stderr))
        with (
            stdout.open("xb") as output,
            stderr.open("xb") as errors,
            subprocess.Popen(args, stdout=output, stderr=errors, cwd=SOURCE) as child,
        ):
            self.log("process", operation=label, pid=child.pid)
            try:
                code = child.wait()
            except KeyboardInterrupt:
                # Parent, supervisor and worker share the console's interrupt.
                # Let the supervisor finish its cancellation and ledger writes.
                self.log("awaiting_child_shutdown", operation=label, pid=child.pid)
                while child.poll() is None:
                    try:
                        child.wait(timeout=1)
                    except (subprocess.TimeoutExpired, KeyboardInterrupt):
                        continue
                self.log("child_exit", operation=label, code=child.returncode)
                raise
        self.log("exit", operation=label, code=code)
        if code:
            raise CellIOError(
                "RUN_PROTOCOL_COMMAND",
                f"{label} exited {code}. Inspect {stdout} and {stderr}",
                exit_code=130 if code in (-2, 130) else code if code > 0 else 4,
            )
        return stdout

    def cli(self, label, *args):
        return read(self.invoke(label, ["-m", "cellio", "--json", *args]))

    def finite(self, suite, allow_run):
        directory = self.root / suite
        plan_path = directory / "campaign.plan.json"
        if not plan_path.is_file():
            require(allow_run, f"Complete the prerequisite suite {suite} first")
            require(
                suite not in PREFREEZE or not self.frozen.exists(),
                f"The existing protocol lock cannot start a new {suite} plan",
            )
            self.cli(
                "plan", "campaign", "plan", "--suite", suite, "--output", directory
            )
        plan = read_plan(plan_path)
        design = suite_design(suite)
        require(
            plan["suite_id"] == suite
            and plan.get("suite_design") == design
            and plan["plan_mode"] == design["mode"] == "finite"
            and plan["runs"] == finite_rows(suite, plan["provenance"], design),
            f"{suite} differs from its registered plan",
        )
        # A pre-freeze plan retains its original protocol hash after the candidate is frozen.
        measured = suite not in PREFREEZE
        _, identity = lock_context("isaac62_lockstep", measured=measured)
        require(
            all(
                plan["provenance"].get(k) == v
                for k, v in identity.items()
                if measured or k != "protocol_hash"
            ),
            f"{suite} belongs to a different candidate",
        )
        if suite in PREFREEZE and self.frozen.exists():
            pinned = read(self.frozen).get("evidence", {}).get(suite, {})
            require(
                pinned.get("plan_hash") == plan["plan_hash"],
                f"{suite} plan differs from frozen evidence",
            )
        rows = {r["run_id"]: r for r in plan["runs"]}
        verified = {}
        ledger = directory / "ledger.jsonl"
        journal = self.logs / "journal.jsonl"
        self.log("suite", suite=suite, plan_hash=plan["plan_hash"], ledger=str(ledger))
        checked_chains = set()
        while True:
            histories = attempt_histories(lines(ledger), set(rows))
            if any(
                records and records[-1][-1]["state"] not in TERMINAL
                for records in histories.values()
            ):
                machine = lease_directory("gpu-worker")
                with exclusive(machine), exclusive(directory):
                    reconcile(directory)
                histories = attempt_histories(lines(ledger), set(rows))
            for run_id, records in histories.items():
                if not records or run_id in verified:
                    continue
                row = rows[run_id]
                trial = read_trial(directory, row, records[0], plan["provenance"])
                self.log(
                    "first_attempt",
                    suite=suite,
                    run_id=run_id,
                    attempt_id=trial.attempt_id,
                    status=trial.status,
                    outcome=trial.outcome,
                    error=trial.error,
                )
                require(
                    trial.error is None and trial.verified_decision,
                    f"{suite}/{run_id} first attempt {trial.attempt_id}: {trial.error or trial.status}",
                )
                if row.get("test_control", {}).get("kind", "full_h") != "full_h":
                    require(
                        trial.status == "CANCELLED"
                        and read(trial.path / "result.json").get("reason")
                        == "TEST_RESET_CANCEL",
                        f"{run_id} missed the prescribed reset cancellation",
                    )
                else:
                    require(
                        trial.status == "COMPLETED"
                        and trial.eval["end_step"]
                        == row["config"]["episode"]["horizon_steps"],
                        f"{run_id} did not complete its registered horizon",
                    )
                if suite in ("commission-v1", "boundaries-v1", "clean-install"):
                    require(
                        trial.outcome == "SUCCESS"
                        and trial.eval["compliance_verdict"] == "PASS",
                        f"{suite}/{run_id} first attempt failed its healthy commissioning gate",
                    )
                if suite == "overhead-v1":
                    require(
                        (trial.performance.get("running_wall_ns") or 0) > 0,
                        f"Missing runtime for {run_id}",
                    )
                verified[run_id] = trial
                if suite == "reset-v1":
                    name = row["chain_id"]
                    chain = [t for t in verified.values() if t.row["chain_id"] == name]
                    if len(chain) == 3 and name not in checked_chains:
                        status = reset_chains(chain)["chains"][0]
                        require(
                            status["status"] == "PASS", f"Reset chain {name}: {status}"
                        )
                        checked_chains.add(name)
            if len(verified) == len(rows):
                break
            require(
                allow_run,
                f"{suite} has {len(rows) - len(verified)} unfulfilled planned rows",
            )
            require(
                suite not in PREFREEZE or not self.frozen.exists(),
                f"Frozen {suite} evidence is incomplete",
            )
            action = "resume" if ledger.exists() else "run"
            # Commissioning and reset have per-attempt acceptance gates. The canonical
            # campaign scores legitimate task failures and amortizes asset verification.
            group_size = (
                1 if suite in ("commission-v1", "boundaries-v1", "reset-v1") else 16
            )
            result = self.cli(
                action,
                "campaign",
                action,
                "--plan",
                plan_path,
                "--max-groups",
                str(group_size),
            )
            require(
                result.get("groups_executed", 0) > 0,
                f"{suite} made no new progress; inspect {journal}",
            )
        trials = [verified[r] for r in rows]
        if suite in PREFREEZE:
            witness = {
                "plan_hash": plan["plan_hash"],
                "attempts": [
                    {"attempt_id": t.attempt_id, "trace_hash": t.eval["source_digest"]}
                    for t in trials
                ],
            }
            self.preflight[suite] = witness
            if self.frozen.exists():
                require(
                    read(self.frozen)["evidence"][suite] == witness,
                    f"{suite} changed after protocol freeze",
                )
        if suite in ("pilot-v1", "canonical-v1"):
            for (cell, family), counts in fault_coverage(trials).items():
                require(
                    counts["realized"] and counts["restored"],
                    f"No verified realization and restoration for {suite}/{cell}/{family}",
                )
                if family == "F6":
                    require(
                        counts["held"],
                        f"No verified physical hold for {suite}/{cell}/F6",
                    )
        if suite in ("canonical-v1", "overhead-v1", "reuse-v1"):
            metrics = directory / "reports" / "metrics.json"
            if not all(
                (metrics.parent / name).is_file()
                for name in ("metrics.json", "report.html", "report.md")
            ):
                self.cli(
                    "report",
                    "report",
                    "--campaign",
                    directory,
                    "--output",
                    metrics.parent,
                )
            report = read(metrics)
            summaries = [attempt_replay(t) for t in trials]
            entries = report.get("attempts", [])
            require(
                report.get("plan_hash") == plan["plan_hash"]
                and report.get("replay_environment", {}).get("source_hash")
                == plan["provenance"]["code_hash"]
                and len(entries) == len(summaries)
                and all(
                    all(entry.get(k) == v for k, v in summary.items())
                    for entry, summary in zip(entries, summaries, strict=True)
                ),
                f"Report differs from the verified first attempts: {metrics}",
            )
            if suite == "overhead-v1":
                require(
                    digest(report.get("overhead")) == digest(overhead(trials)),
                    f"Overhead report differs from verified measurements: {metrics}",
                )
        self.log("verified_suite", suite=suite, first_attempt_count=len(trials))

    def freeze(self, allow_run):
        if not self.frozen.is_file():
            require(allow_run, "Freeze the validated commissioning candidate first")
            self.invoke(
                "freeze",
                [SOURCE / "scripts/freeze_protocol.py", "--campaign-root", self.root],
            )
        ctx, identity = lock_context("isaac62_lockstep", measured=True)
        require(
            ctx["protocol"].get("evidence") == self.preflight,
            "Protocol commissioning fingerprints differ from the selected campaign root",
        )
        self.log(
            "frozen", path=str(self.frozen), protocol_hash=identity["protocol_hash"]
        )

    def soak(self):
        directory = self.root / "soak-v1"
        plan = directory / "campaign.plan.json"
        if not plan.exists():
            self.cli(
                "plan", "campaign", "plan", "--suite", "soak-v1", "--output", directory
            )
        header = read_plan(plan)
        design = suite_design("soak-v1")
        _, identity = lock_context("isaac62_lockstep", measured=True)
        require(
            header["suite_id"] == "soak-v1"
            and header.get("suite_design") == design
            and header["plan_mode"] == design["mode"] == "soak_generator"
            and header.get("generator") == design["generator"]
            and header["runs"] == []
            and header["provenance"] == identity,
            "Soak plan differs from the frozen candidate",
        )
        if not (directory / "soak.result.json").is_file():
            require(
                not (directory / "ledger.jsonl").exists()
                and not (directory / "generator.rows.jsonl").exists(),
                f"Preserved interrupted soak at {directory}. Independent workers cannot be pooled",
            )
            self.cli("soak", "campaign", "run", "--plan", plan)
        _, trials, retries = load_campaign(directory)
        require(
            not retries
            and all(t.error is None and t.verified_decision for t in trials),
            "Incomplete or invalid soak attempt",
        )
        evidence = soak_evidence(directory, trials)
        require(evidence["status"] == "PASS", f"Soak evidence: {evidence}")
        self.log(
            "verified_suite",
            suite="soak-v1",
            first_attempt_count=len(trials),
            worker_pid=evidence["worker_pid"],
        )

    def clean(self, work, conda):
        from cellio.release import audit_clean

        lock_context("isaac62_lockstep", measured=True)
        proof = self.root / "release-evidence" / "clean-install"
        campaign = self.root / "clean-install"
        checkpoint = proof / "reproduction.json"
        commands = read(checkpoint).get("commands", []) if checkpoint.is_file() else []
        download_failed = (
            bool(commands)
            and commands[-1]["key"] in {"create_env", "torch", "isaac", "dependencies"}
            and commands[-1]["exit_code"] != 0
        )
        if not campaign.exists() and (not checkpoint.exists() or download_failed):
            if work is None and download_failed:
                previous = read(checkpoint)
                work = Path(
                    previous.get(
                        "requested_work_directory", previous["working_directory"]
                    )
                )
            require(
                work is not None,
                "The clean installation needs --clean-work-dir outside the checkout",
            )
            self.invoke(
                "clean-install",
                [
                    SOURCE / "scripts/reproduce_clean_install.py",
                    "--work-dir",
                    work,
                    "--campaign-root",
                    self.root,
                    "--data-root",
                    data_root(),
                    "--conda",
                    conda,
                ],
            )
        evidence = audit_clean(SOURCE, self.root)
        require(
            evidence["status"] == "PASS",
            f"Clean-install evidence at {proof}: {evidence}",
        )
        self.log("verified_suite", suite="clean-install", evidence=str(proof))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--phase", choices=("all", *ORDER, "reuse-v1", "clean-install"), default="all"
    )
    p.add_argument("--campaign-root", type=Path, default=data_root() / "campaigns")
    p.add_argument("--clean-work-dir", type=Path)
    p.add_argument("--conda", default="conda")
    a = p.parse_args(argv)
    if a.clean_work_dir is not None and a.phase != "clean-install":
        p.error("--clean-work-dir applies to the explicit clean-install phase")
    runner = Protocol(a.campaign_root)
    try:
        with exclusive(runner.logs.parent):
            if a.phase == "clean-install":
                runner.clean(a.clean_work_dir, a.conda)
            else:
                phases = (
                    (*PREFREEZE, "freeze", "reuse-v1")
                    if a.phase == "reuse-v1"
                    else ORDER
                )
                for phase in phases:
                    execute = a.phase in ("all", phase)
                    if phase == "freeze":
                        runner.freeze(execute)
                    elif phase == "soak-v1":
                        runner.soak()
                    else:
                        runner.finite(phase, execute)
                    if phase == a.phase:
                        break
    except KeyboardInterrupt:
        runner.log("interrupted")
        return 130
    except (CellIOError, OSError, ValueError, KeyError) as exc:
        runner.log("stopped", reason=str(exc))
        return getattr(exc, "exit_code", 4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
