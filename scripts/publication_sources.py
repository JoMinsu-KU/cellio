"""Read the declared ports, implementation symbols and portable workstation locks."""

import argparse
import ast
import csv
import gzip
import json
import re
from pathlib import Path

from cellio.artifacts import frames, verify_artifact
from cellio.io import atomic_write, file_hash, write_json

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    ("M01", "Configuration and registry", "config/__init__.py", "resolve"),
    ("M02", "Clock and event phases", "core/clock.py", "StepClock"),
    ("M03", "Native Isaac plant", "adapters/isaac62/plant.py", "IsaacPlant"),
    ("M04", "Scene geometry and independent truth", "scenes/definition.py", "Scene"),
    ("M05", "Nominal typed signals", "core/signals.py", "NominalSampler"),
    (
        "M06",
        "Observation, delivery and actuator faults",
        "core/faults/engine.py",
        "FaultEngine",
    ),
    ("M07", "Command validation and delivery", "core/commands.py", "CommandRouter"),
    (
        "M08",
        "Reference and mutant controllers",
        "controllers/reference.py",
        "ReferenceController",
    ),
    ("M09", "Independent finite-trace oracle", "core/oracle/engine.py", "Oracle"),
    ("M10", "Fixed-step episode runner", "runtime/episode.py", "run_episode"),
    ("M11", "Campaign supervisor", "runtime/supervisor.py", "execute_plan"),
    ("M12", "Sealed trace verification", "artifacts/trace.py", "verify_artifact"),
    ("M13", "Replay and reporting", "reporting/__init__.py", "report"),
    ("M14", "CLI and workstation diagnostics", "cli/main.py", "main"),
    ("M15", "Registered scene extensions", "plugins/__init__.py", "scene_plugin"),
)


def read(path):
    return json.loads(path.read_bytes())


def csv_rows(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


def modules():
    rows = []
    for ident, label, source, symbol in MODULES:
        path = ROOT / "src" / "cellio" / source
        definitions = {
            node.name
            for node in ast.parse(path.read_bytes()).body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        if symbol not in definitions:
            raise ValueError(f"{ident} implementation symbol missing: {symbol}")
        rows.append(
            {
                "id": ident,
                "scope": label,
                "source_status": "definition present",
                "symbol": symbol,
                "source": path.relative_to(ROOT).as_posix(),
                "sha256": file_hash(path),
            }
        )
    rows.append(
        {
            "id": "M16",
            "scope": "External OpenPLC / wall-clock integration",
            "source_status": "outside the implemented release scope (optional)",
            "symbol": "",
            "source": "",
            "sha256": "",
        }
    )
    return rows


def ports():
    from dataclasses import fields
    from inspect import signature

    from cellio.core import models, ports

    methods = {
        "PlantPort": ("snapshot", "apply", "advance_one_step"),
        "SignalMapperPort": ("sample",),
        "ControllerPort": ("tick",),
        "OraclePort": ("observe", "finalize"),
        "TracePort": ("append_tick", "finish"),
    }
    actual = {
        name: {m: str(signature(getattr(getattr(ports, name), m))) for m in names}
        for name, names in methods.items()
    }
    return {
        "ports": actual,
        "controller_view_fields": [f.name for f in fields(models.ControllerView)],
        "evidence_fields": [f.name for f in fields(models.TickEvidenceFrame)],
        "source_sha256": {
            p: file_hash(ROOT / "src/cellio" / p)
            for p in (
                "core/ports.py",
                "core/models.py",
                "core/signals.py",
                "runtime/episode.py",
            )
        },
    }


def environment():
    rows = []

    def add(scope, key, value, path):
        rows.append(
            {
                "scope": scope,
                "item": key,
                "value": str(value),
                "source": path.relative_to(ROOT).as_posix(),
                "sha256": hashes[path],
            }
        )

    hashes = {}
    for lock in sorted((ROOT / "locks").glob("*/python-environment.json")):
        wheels = lock.with_name("requirements.lock")
        hashes[lock], hashes[wheels] = file_hash(lock), file_hash(wheels)
        data = read(lock)
        if hashes[wheels] != data["requirements_sha256"]:
            raise ValueError("Installed inventory and wheel lock hashes differ")
        scope = lock.parent.name
        for name in ("os", "os_version", "architecture", "python"):
            add(scope, name, data[name], lock)
        add(scope, "installed distributions", len(data["packages"]), lock)
        for key in (
            "cellio",
            "isaacsim",
            "torch",
            "numpy",
            "warp-lang",
            "matplotlib",
            "jsonschema",
            "pytest",
        ):
            if key in data["packages"]:
                add(scope, key, data["packages"][key], lock)
        add(scope, "hash-pinned wheel requirements", hashes[wheels], wheels)
    workstation = ROOT / "results/workstation"
    doctor = workstation / "doctor-windows-1.0.0.json"
    assets = workstation / "franka-assets-portable.lock.json"
    profile = workstation / "robot_profile.lock.json"
    geometry = workstation / "franka-geometry.json"
    for p in (doctor, assets, profile, geometry):
        hashes[p] = file_hash(p)
    probe = read(doctor)
    for key in ("platform", "processor", "installed_ram_bytes", "recorded_at_utc"):
        add("native doctor", key, probe[key], doctor)
    for gpu in probe["gpus"]:
        for key in ("name", "driver_version", "vram_mib"):
            add("native GPU", key, gpu[key], doctor)
    for key in ("kit_version", "physics_device", "solver", "warp_runtime_version"):
        add("Kit probe", key, probe["kit"][key], doctor)
    manifest = read(assets)
    if not manifest["files"] or any(
        not re.fullmatch("[0-9a-f]{64}", f["sha256"]) or f["bytes"] <= 0
        for f in manifest["files"].values()
    ):
        raise ValueError("Asset inventory contains an invalid file identity")
    for name, value in (
        ("inventory schema", manifest["schema"]),
        ("layout", manifest["layout"]),
        ("recorded file hashes", len(manifest["files"])),
        ("recorded bytes", sum(f["bytes"] for f in manifest["files"].values())),
        ("motion configuration records", len(manifest["motion_config"])),
        ("source terms", manifest["terms"]),
    ):
        add("Franka asset inventory", name, value, assets)
    model = read(profile)
    for key in ("schema", "tool_frame", "finger_drivers", "hand_T_tcp"):
        add("Robot profile", key, model[key], profile)
    measured = read(geometry)
    add(
        "Geometry calibration",
        "physical pose samples",
        len(measured["samples"]),
        geometry,
    )
    add("Geometry calibration", "recorded result", measured["passed"], geometry)
    return rows


def static_tables(output):
    tables = output / "tables"
    csv_rows(
        tables / "table2_module_support.csv",
        ("id", "scope", "source_status", "symbol", "source", "sha256"),
        modules(),
    )
    csv_rows(
        tables / "table4_environment_assets.csv",
        ("scope", "item", "value", "source", "sha256"),
        environment(),
    )
    write_json(tables / "figure1_ports.json", ports())


def fixed_camera_sources(captures, output):
    panels = []
    for (cell, step), capture in zip(
        (("cell_a", 2440), ("cell_b", 3736)), captures, strict=True
    ):
        scene = read(capture / "frames.json")
        episode = capture / "episode"
        proof = verify_artifact(episode)
        if (
            scene["cell"] != cell
            or read(episode / "result.json")["backend"] != "isaac62"
            or scene["camera_path"] != "/World/OperatorCamera"
            or proof["ticks"] != 7200
            or proof["completion_status"] != "COMPLETED"
            or proof["run_id"] != scene["run_id"]
            or proof["attempt_id"] != scene["attempt_id"]
            or proof["artifact_digest"] != scene["trace_chain_sha256"]
            or file_hash(episode / "manifest.final.json")
            != scene["manifest_final_sha256"]
        ):
            raise ValueError(
                "Sealed native capture does not match the selected fixed-camera episode"
            )
        frame = next(f for f in scene["frames"] if f["step"] == step)
        record = next(f for f in frames(episode) if f["step"] == step)
        name = f"s{step:04d}.png"
        png = capture / "frames" / name
        if (
            frame["frame"] != name
            or file_hash(png) != frame["sha256"]
            or record["record_hash"] != frame["trace_record_hash"]
            or record["truth"]["bodies"]["part_000"]["position_m"] != frame["part_m"]
            or record["truth"]["contacts"] != frame["active_contacts"]
        ):
            raise ValueError(
                "Selected frame differs from its declared trace step or original PNG"
            )
        with gzip.open(episode / "events.jsonl.gz", "rt", encoding="utf-8") as stream:
            stages = [
                e["data"]["payload"]["stage_id"]
                for e in map(json.loads, stream)
                if e["step"] <= step
                and e["data"]["event_type"] == "controller.stage_enter"
            ]
        if stages[-1] != frame["stage"]:
            raise ValueError("Stage label differs from trace events")
        contacts = {tuple(sorted(pair)) for pair in frame["active_contacts"]}
        expected = (
            ("finger_left", "finger_right")
            if cell == "cell_a"
            else ("fixed_jaw", "moving_jaw")
        )
        if not all(tuple(sorted((name, "part_000"))) in contacts for name in expected):
            raise ValueError("Selected scene lacks both required workpiece contacts")
        output.mkdir(parents=True, exist_ok=True)
        filename = cell + ".png"
        atomic_write(output / filename, png.read_bytes())
        panels.append(
            {
                "cell": cell,
                "step": step,
                "stage": stages[-1],
                "source": "isaac_gui_viewport",
                "camera": scene["camera_path"],
                "png": filename,
                "png_sha256": frame["sha256"],
                "trace_record_sha256": record["record_hash"],
                "trace_chain_sha256": proof["artifact_digest"],
                "manifest_final_sha256": scene["manifest_final_sha256"],
                "run_id": proof["run_id"],
                "attempt_id": proof["attempt_id"],
                "actual_advances": proof["ticks"],
                "part_position_m": frame["part_m"],
                "active_contacts": frame["active_contacts"],
                "frame_index_sha256": file_hash(capture / "frames.json"),
            }
        )
    write_json(
        output / "fixed-camera.json",
        {
            "schema": "cellio.publication.native_stills/1.0",
            "basis": "Development runs at fixed initial pose, selected from prespecified camera steps.",
            "panels": panels,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify and copy native workstation Figure 2 stills"
    )
    parser.add_argument("--cell-a-capture", type=Path, required=True)
    parser.add_argument("--cell-b-capture", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "results/workstation/figures"
    )
    opts = parser.parse_args()
    fixed_camera_sources((opts.cell_a_capture, opts.cell_b_capture), opts.output)
