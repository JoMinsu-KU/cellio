"""CLI entry points for the supplemental Cell C study."""

import json
import subprocess
import sys
from importlib import resources
from importlib.util import find_spec
from pathlib import Path

from cellio.io import CellIOError, write_json

from .replay import CONDITIONS, extract_witness, replay_witness


def add_commands(commands):
    inspection = commands.add_parser(
        "inspection", help="Run and replay the Cell C inspection study"
    )
    actions = inspection.add_subparsers(dest="action", required=True)
    prep = actions.add_parser(
        "prepare", help="Assemble the study scene using a resolved local Franka asset"
    )
    run = actions.add_parser(
        "run", help="Simulate a new two-part sequence in Isaac Sim 6.2"
    )
    for action in (prep, run):
        action.add_argument(
            "--output", required=True, type=Path, help="New output directory"
        )
        action.add_argument(
            "--assets",
            type=Path,
            help="Resolved asset directory containing assets.lock.json",
        )
    run.add_argument("condition", choices=CONDITIONS)
    run.add_argument(
        "--recipe",
        type=Path,
        help="Study recipe JSON with two dimensional errors and infeed offsets",
    )
    check = actions.add_parser(
        "verify",
        help="Audit paired native runs against the composed USD and motion trace",
    )
    check.add_argument("--normal", required=True, type=Path)
    check.add_argument("--stale", required=True, type=Path)
    check.add_argument(
        "--source-root", type=Path, help="Exact native sources for an earlier run"
    )
    check.add_argument(
        "--output", required=True, type=Path, help="Destination comparison JSON"
    )
    replay = actions.add_parser(
        "replay", help="Evaluate a two-part witness using CPU-only measurements"
    )
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Native run directory or exported witness JSON",
    )
    source.add_argument(
        "--example", choices=CONDITIONS, help="Packaged commissioning witness"
    )
    replay.add_argument("--export", type=Path, help="Save the portable witness JSON")
    replay.add_argument(
        "--assert-pass",
        action="store_true",
        help="Return code 5 for a detected violation",
    )


def _execute(script, args):
    study = resources.files("cellio.inspection.study")
    with resources.as_file(study.joinpath(script)) as path:
        proc = subprocess.run(
            [sys.executable, str(path), *map(str, args)], stdout=sys.stderr, check=False
        )
    if proc.returncode:
        raise CellIOError(
            "RUN_INSPECTION_FAILED",
            f"{script} exited with {proc.returncode}",
            exit_code=4,
        )


def _replay(args):
    if args.example:
        sample = resources.files("cellio.inspection").joinpath(
            "examples", f"{args.example}.json"
        )
        witness = json.loads(sample.read_bytes())
    elif args.input.is_dir():
        witness = extract_witness(args.input)
    else:
        witness = json.loads(args.input.read_bytes())
    result = replay_witness(witness)
    if args.export is not None:
        if (
            args.input
            and not args.input.is_dir()
            and args.input.resolve() == args.export.resolve()
        ):
            raise CellIOError(
                "CFG_INSPECTION_OUTPUT",
                "Select a different path for the exported witness.",
            )
        if args.export.exists():
            raise CellIOError(
                "CFG_INSPECTION_OUTPUT", f"Export destination exists: {args.export}"
            )
        write_json(args.export, witness)
    return result, 5 if args.assert_pass and result["verdict"] == "FAIL" else 0


def perform(args):
    if args.action == "replay":
        return _replay(args)
    output = args.output.resolve()
    if output.exists():
        raise CellIOError("CFG_INSPECTION_OUTPUT", f"Output already exists: {output}")
    if args.action == "run" and find_spec("isaacsim") is None:
        raise CellIOError(
            "ENV_ISAAC_MISSING",
            "Run this command in the Isaac Sim 6.2 environment.",
            exit_code=3,
        )
    if args.action == "verify":
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [args.normal.resolve(), args.stale.resolve(), "--out", output]
        if args.source_root:
            command.extend(["--source-root", args.source_root.resolve()])
        _execute("verify_inspection.py", command)
        return {
            "comparison": str(output),
            "verified": json.loads(output.read_bytes())["verified"],
        }, 0
    common = ["--out", output]
    if args.assets:
        common.extend(["--assets", args.assets.resolve()])
    if args.action == "prepare":
        _execute("prepare.py", common)
        return {"scene": str(output / "cell_c_inspection.usda")}, 0
    if args.recipe:
        common.extend(["--recipe", args.recipe.resolve()])
    _execute("run_inspection.py", [args.condition, *common])
    result = replay_witness(extract_witness(output))
    return {"directory": str(output), "replay": result}, 0
