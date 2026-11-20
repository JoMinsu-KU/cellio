"""Allocate and execute the frozen two-part inspection extension on a native Isaac host."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols" / "cell-c-softwarex-v1.json"
STUDY = ROOT / "src" / "cellio" / "inspection" / "study"
RUNTIME_FILES = ("run_inspection.py", "cell_c_motion.py", "cell_c_inspection_logic.py", "cell_c_handling.py")
MODES = ("normal", "stale_ok")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    pending = path.with_suffix(".partial")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    pending.replace(path)


def schedule(protocol):
    rows = []
    for sequence, seq in protocol["sequences"].items():
        for placement, profile in protocol["placement_profiles"].items():
            modes = MODES if (len(rows)//2)%2 == 0 else tuple(reversed(MODES))
            for condition in modes:
                rows.append({"id": f"{sequence}-{placement}-{condition}", "condition": condition,
                             "sequence": sequence, "placement": placement, "errors_mm": seq["errors_mm"],
                             "infeed_offsets_mm": profile["infeed_offsets_mm"]})
    if len(rows) != protocol["expected_native_runs"]:
        raise ValueError("Declared allocation differs from protocol")
    return rows


def source_files():
    # Package the runtime sources as well as the recipe and the outcome-independent design.
    files = [PROTOCOL, Path(__file__), *(STUDY / name for name in RUNTIME_FILES)]
    files.extend(p for p in sorted(STUDY.iterdir()) if p.suffix in (".usdc", ".usda", ".png") or
                 p.name in ("scene.py", "render_study.py", "stage.py", "stage_assets.py", "prepare.py"))
    return {str(p.relative_to(ROOT).as_posix()): digest(p) for p in files}


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    rows = schedule(read(PROTOCOL))
    lock = {"protocol": read(PROTOCOL)["id"], "created_utc": stamp(), "rows": rows,
            "source_sha256": source_files()}
    write(root/"allocation.json", lock)
    for row in rows:
        folder = root/row["id"]
        folder.mkdir()
        write(folder/"recipe.json", {**row, "protocol_sha256":digest(PROTOCOL)})
    print(f"Frozen {len(rows)} native allocations in {root}; {digest(root/'allocation.json')}", flush=True)


def execute(root, python, timeout, ids, retry):
    lock = read(root/"allocation.json")
    if lock["source_sha256"] != source_files() or lock["rows"] != schedule(read(PROTOCOL)):
        raise RuntimeError("Code or design changed after allocation; preserve this series and start a versioned series")
    if ids and set(ids) - {row["id"] for row in lock["rows"]}:
        raise ValueError("Requested ID outside the allocation")
    env = dict(os.environ, MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="2")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT/"src"), env.get("PYTHONPATH","")])
    for row in lock["rows"]:
        if ids and row["id"] not in ids:
            continue
        folder = root / row["id"]
        existing = sorted(folder.glob("attempt-[0-9][0-9]"))
        if existing and (not retry or any((p/"status.json").exists() and
                                          read(p/"status.json")["status"] == "completed" for p in existing)):
            continue
        attempt = folder / f"attempt-{len(existing)+1:02d}"
        attempt.mkdir()
        start = time.monotonic()
        status = {"id":row["id"], "attempt":attempt.name, "started_utc":stamp(),
                  "allocation_sha256":digest(root/"allocation.json"), "recipe_sha256":digest(folder/"recipe.json")}
        write(attempt/"start.json",status)
        command = [python, "-B","-u","-m","cellio.inspection.study.run_inspection", row["condition"],
                   "--recipe",str(folder/"recipe.json"),"--out",str(attempt/"episode")]
        print(f"{stamp()} START {row['id']} {attempt.name}",flush=True)
        with (attempt/"stdout.log").open("wb") as out, (attempt/"stderr.log").open("wb") as err:
            try:
                rc = subprocess.run(command, stdout=out, stderr=err, env=env, cwd=ROOT, timeout=timeout).returncode
                success = (rc == 0 and (attempt/"episode/result.json").is_file() and
                           read(attempt/"episode/result.json").get("status")=="completed")
                status.update(exit_code=rc, status="completed" if success else "native_failure")
            except (OSError, subprocess.TimeoutExpired) as exc:
                status.update(status="infrastructure_failure",error=str(exc))
        status.update(ended_utc=stamp(), wall_seconds=time.monotonic()-start)
        write(attempt/"status.json",status)
        print(f"{stamp()} END {row['id']} {status['status']} {status['wall_seconds']:.1f}s",flush=True)
        if status["status"] == "infrastructure_failure":
            return 3
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare","execute"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--timeout", type=int, default=360)
    p.add_argument("--id", action="append", default=[])
    p.add_argument("--retry", action="store_true", help="Append new attempts for unfinished allocations")
    p.add_argument("--affinity-mask",type=lambda value:int(value,0),help="Optional native Windows processor mask")
    args = p.parse_args()
    if args.affinity_mask:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
        kernel.SetProcessAffinityMask.restype = wintypes.BOOL
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), args.affinity_mask):
            raise ctypes.WinError(ctypes.get_last_error())
    if args.action == "prepare":
        prepare(args.root.resolve())
        return 0
    return execute(args.root.resolve(),args.python,args.timeout,args.id,args.retry)


if __name__ == "__main__":
    raise SystemExit(main())
