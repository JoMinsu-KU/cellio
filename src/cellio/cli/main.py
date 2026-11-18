import argparse
import json
from pathlib import Path
import sys

from cellio.config import load
from cellio.environment import data_root, doctor, lock_context
from cellio.io import CellIOError, digest, json_value, write_json


class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        raise CellIOError("CFG_USAGE", f"{message}. See {self.prog} --help.")


def parser():
    p = CLIParser(
        prog="cellio",
        description="Robotic workcell I/O regression tests",
        epilog="Canonical H=7200, physics=120 Hz, scene seeds 1000–1019. Default runs directory: "
        "CELLIO_DATA_ROOT/artifacts/runs. Exit: 0 success, "
        "2 configuration, 3 environment, 4 infrastructure, 5 mismatch, 6 missing evidence, 130 cancelled.",
    )
    p.add_argument(
        "--json", action="store_true", help="Write structured error details to stdout"
    )
    cmds = p.add_subparsers(dest="command", required=True)
    d = cmds.add_parser("doctor")
    d.add_argument("--profile", choices=["isaac62", "mock", "audit"], default="isaac62")
    d.add_argument("--level", choices=["static", "import", "smoke"], default="smoke")
    d.add_argument("--output", type=Path)
    a = cmds.add_parser("assets").add_subparsers(dest="action", required=True)
    for action in ("resolve", "verify"):
        sub = a.add_parser(action)
        sub.add_argument("--root", type=Path, default=data_root() / "assets")
        if action == "resolve":
            sub.add_argument(
                "--profile", choices=["franka_panda"], default="franka_panda"
            )
        else:
            sub.add_argument("--offline", action="store_true", required=True)
    cmds.add_parser("validate").add_argument("--config", required=True, type=Path)
    run = cmds.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument(
        "--output", type=Path, help="Parent directory for this run and attempt"
    )
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--headless", action="store_true")
    mode.add_argument("--gui", action="store_true")
    run.add_argument("--assert-expectations", action="store_true")
    c = cmds.add_parser("campaign").add_subparsers(dest="action", required=True)
    plan = c.add_parser("plan")
    plan.add_argument("--suite", required=True)
    plan.add_argument("--output", type=Path, required=True)
    for action in ("run", "resume"):
        sub = c.add_parser(action)
        sub.add_argument("--plan", type=Path, required=True)
        if action == "resume":
            sub.add_argument("--retry-failed", action="store_true")
    cmds.add_parser("verify-artifact").add_argument(
        "--artifact", type=Path, required=True
    )
    ev = cmds.add_parser("evaluate")
    ev.add_argument("--artifact", type=Path, required=True)
    ev.add_argument("--requirements", type=Path)
    output = cmds.add_parser("report")
    output.add_argument("--campaign", type=Path, required=True)
    output.add_argument("--output", type=Path, required=True)
    pair = cmds.add_parser("compare")
    pair.add_argument("--reference", type=Path, required=True)
    pair.add_argument("--candidate", type=Path, required=True)
    bench = cmds.add_parser("benchmark")
    bench.add_argument("--suite", choices=["overhead-v1"], required=True)
    bench.add_argument("--output", type=Path, required=True)
    schema = (
        cmds.add_parser("schema")
        .add_subparsers(dest="action", required=True)
        .add_parser("export")
    )
    schema.add_argument("--output", type=Path, required=True)
    acc = cmds.add_parser("acceptance")
    acc.add_argument("--level", choices=["r0", "r1", "r2", "r3"], required=True)
    acc.add_argument("--report", type=Path, required=True)
    acc.add_argument("--campaign-root", type=Path, default=data_root() / "campaigns")
    acc.add_argument("--strict", action="store_true")
    return p


def single(a):
    from cellio.plugins import scene_plugin
    from cellio.runtime.campaign import exclusive
    from cellio.runtime.supervisor import attempt, run_group, transition

    cfg = load(a.config)
    ctx, identity = lock_context(cfg["profile"])
    cell = cfg["scene"]["cell_id"]
    state = scene_plugin(cell).initial_state_sampler(cfg["seeds"]["scene"])
    row = {
        "run_id": digest([cfg, state, identity])[:24],
        "config": cfg,
        "initial_state": state,
        "gui": a.gui,
    }
    root = (a.output or (data_root() / cfg["artifacts"]["output_root"])).resolve()
    root.mkdir(parents=True, exist_ok=True)
    machine = data_root() / (
        "gpu-worker" if cfg["profile"] == "isaac62_lockstep" else "mock-worker"
    )
    machine.mkdir(parents=True, exist_ok=True)
    with exclusive(machine), exclusive(root):
        from cellio.runtime.supervisor import history

        prior = history(root).get(row["run_id"], [])
        entry = attempt(
            root,
            row,
            ctx,
            len(prior),
            prior[-1]["attempt_id"] if prior else None,
            "explicit rerun" if prior else None,
        )
        transition(root, entry, "PLANNED")
        result = run_group(root, [row], [entry], ctx)[0]
    directory = root / row["run_id"] / entry["attempt_id"]
    if result["completion_status"] == "INFRA_ERROR":
        raise CellIOError("RUN_ATTEMPT_FAILED", f"Inspect {directory}", exit_code=4)
    outcome = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    code = 130 if result["completion_status"] == "CANCELLED" else 0
    if (
        code == 0
        and a.assert_expectations
        and cfg.get("expected")
        and outcome["case_verdict"] in ("FAIL", "UNKNOWN")
    ):
        code = 5
    return {
        **result,
        "directory": str(directory),
        "task_outcome": outcome["task_outcome"],
        "case_verdict": outcome["case_verdict"],
    }, code


def perform(a):
    if a.command == "doctor":
        output = (
            a.output or data_root() / "locks" / f"doctor-{a.profile}-{a.level}.json"
        )
        report = doctor(a.profile, output, a.level)
        if a.profile == "isaac62" and a.level == "smoke" and report["passed"]:
            write_json(data_root() / "locks" / "doctor.json", report)
        return report, 0 if report["passed"] else 3
    if a.command == "validate":
        cfg = load(a.config, project_root=Path.cwd())
        return {
            "scenario_id": cfg["scenario_id"],
            "config_hash": digest(cfg),
            "config": cfg,
        }, 0
    if a.command == "assets":
        from cellio.adapters.isaac62.assets import resolve_assets, verify_assets

        lock = (resolve_assets if a.action == "resolve" else verify_assets)(a.root)
        return {
            "entry": lock["entry"],
            "lock_hash": digest(lock),
            "asset_root": str(a.root),
        }, 0
    if a.command == "run":
        return single(a)
    if a.command in ("campaign", "benchmark"):
        from cellio.runtime.campaign import create_plan, suite_design
        from cellio.runtime.supervisor import execute_plan

        if a.command == "benchmark" or a.action == "plan":
            suite_design(a.suite)
            backend = "mock_lockstep" if a.suite == "oracle-v1" else "isaac62_lockstep"
            _, identity = lock_context(
                backend,
                measured=a.suite
                not in {"pilot-v1", "boundaries-v1", "commission-v1", "oracle-v1"},
            )
            plan = create_plan(a.suite, identity, a.output)
            if a.command == "campaign":
                return {
                    "path": str(a.output / "campaign.plan.json"),
                    "plan_hash": plan["plan_hash"],
                    "rows": len(plan["runs"]),
                    "plan_mode": plan["plan_mode"],
                }, 0
            return execute_plan(a.output / "campaign.plan.json"), 0
        return execute_plan(
            a.plan,
            resume=a.action == "resume",
            retry_failed=getattr(a, "retry_failed", False),
        ), 0
    if a.command == "verify-artifact":
        from cellio.artifacts import verify_artifact

        return verify_artifact(a.artifact), 0
    if a.command == "evaluate":
        from cellio.analysis import evaluate

        return evaluate(a.artifact, a.requirements), 0
    if a.command == "report":
        from cellio.reporting import report

        return report(a.campaign, a.output), 0
    if a.command == "compare":
        from cellio.analysis import compare

        return compare(a.reference, a.candidate), 0
    if a.command == "schema":
        from cellio.config.export import export_schemas

        return export_schemas(a.output), 0
    from cellio.acceptance import assess

    result = assess(a.level, a.campaign_root, a.report)
    return result, (
        0 if result["passed"] else 6 if a.strict and result.get("missing") else 5
    )


def _diagnostic(code, message, path, json_mode):
    prefix = code.partition("_")[0]
    module = {
        "CFG": "config",
        "ENV": "environment",
        "ASSET": "assets",
        "RUN": "runtime",
        "SIM": "simulation",
        "TRACE": "artifacts",
        "ORACLE": "oracle",
    }.get(prefix, "cli")
    suggestion = {
        "config": "Check the named option or field against cellio --help and the configuration schema.",
        "environment": "Run cellio doctor for the current Conda environment.",
        "assets": "Check the asset lock with cellio assets verify --offline.",
        "runtime": "Inspect the attempt ledger and corresponding worker log.",
        "artifacts": "Inspect the artifact manifest, trace and errors.jsonl.",
    }.get(module, "Inspect the indicated input and diagnostic files.")
    detail = {
        "code": code,
        "severity": "ERROR",
        "module": module,
        "path": path,
        "message": message,
        "suggestion": suggestion,
        "run_id": None,
    }
    if json_mode:
        print(json.dumps(detail, ensure_ascii=True))
        location = f" [{path}]" if path else ""
        print(f"{code}{location}: {message}", file=sys.stderr)
    else:
        print(json.dumps(detail, ensure_ascii=True), file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    try:
        # A global output flag is accepted before or after nested subcommands.
        args = parser().parse_args([a for a in argv if a != "--json"])
        result, status = perform(args)
        print(
            json.dumps(
                json_value(result),
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
                default=str,
            )
        )
        return status
    except CellIOError as exc:
        _diagnostic(exc.code, str(exc), exc.path, json_mode)
        return exc.exit_code
    except OSError as exc:
        _diagnostic("IO_ERROR", str(exc), exc.filename, json_mode)
        return 4
    except (KeyError, ValueError, TypeError) as exc:
        _diagnostic("RUN_INVALID_DATA", str(exc), None, json_mode)
        return 4
    except KeyboardInterrupt:
        return 130
