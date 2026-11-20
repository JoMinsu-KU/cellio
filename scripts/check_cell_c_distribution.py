"""Build a clean source-only wheel and replay the shipped witnesses in a CPU interpreter."""

import argparse
import json
import os
import subprocess
import sys
import tomllib
from hashlib import sha256
from pathlib import Path
from shutil import copy2, copytree, ignore_patterns
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
from importlib import metadata, resources, util
import json
from pathlib import Path
import platform
import subprocess
import sys

site = Path.cwd() / 'installed'
sys.path.insert(0, str(site))
import cellio
assert Path(cellio.__file__).is_relative_to(site)
assert cellio.__version__ == metadata.version('cellio')
missing = {n:util.find_spec(n) is None for n in ('isaacsim', 'pxr', 'omni')}
assert all(missing.values()), 'Use a CPU interpreter without Isaac, Omniverse or USD'
samples = {}
for label, status in [('normal',0), ('stale_ok',5)]:
    cmd = [sys.executable, '-m','cellio','inspection','replay','--example',label,'--assert-pass']
    import os
    env = dict(os.environ, PYTHONPATH=str(site))
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert p.returncode == status, p.stderr
    result = json.loads(p.stdout)
    samples[label] = {'exit_code':p.returncode, 'verdict':result['verdict'],
                      'destinations':[r['observed_bin'] for r in result['parts']],
                      'result_ids':[r['result_part_id'] for r in result['parts']]}
assert samples['normal']['destinations'] == ['OK','NG']
assert samples['stale_ok']['destinations'] == ['OK','OK']
assert samples['stale_ok']['result_ids'] == ['P1','P1']
print(json.dumps({'version':cellio.__version__, 'python':platform.python_version(),
    'platform':platform.system(), 'unavailable_runtime_modules':sorted(missing), 'examples':samples}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cpu-python",
        type=Path,
        required=True,
        help="Core dependencies installed, no Isaac or USD",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Fresh output directory"
    )
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    temp = out / "temp"
    temp.mkdir()
    env = dict(
        os.environ,
        TMP=str(temp),
        TEMP=str(temp),
        TMPDIR=str(temp),
        PYTHONPATH="",
        SOURCE_DATE_EPOCH="1767225600",
        PYTHONDONTWRITEBYTECODE="1",
    )
    tree = out / "sources"
    copytree(
        ROOT / "src" / "cellio",
        tree / "src" / "cellio",
        ignore=ignore_patterns("__pycache__", "*.pyc"),
    )
    spec = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = ["pyproject.toml", "README.md", "LICENSE.txt"]
    for group in spec["tool"]["setuptools"]["data-files"].values():
        patterns.extend(group)
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT)
            (tree / rel.parent).mkdir(parents=True, exist_ok=True)
            copy2(path, tree / rel)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--no-index",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(out / "wheel"),
            str(tree),
        ],
        env=env,
        check=True,
    )
    (wheel,) = (out / "wheel").glob("*.whl")
    with ZipFile(wheel) as archive:
        sources = {
            p.relative_to(tree / "src").as_posix()
            for p in (tree / "src" / "cellio").rglob("*.py")
        }
        packed = {
            n
            for n in archive.namelist()
            if n.startswith("cellio/") and n.endswith(".py")
        }
        if packed != sources:
            raise RuntimeError(
                "Installed Python files differ from the clean source inventory"
            )
        for name in packed | {
            "cellio/inspection/study/inspection_equipment.usdc",
            "cellio/inspection/study/floor_epoxy.png",
            "cellio/inspection/examples/normal.json",
            "cellio/inspection/examples/stale_ok.json",
        }:
            if archive.read(name) != (tree / "src" / name).read_bytes():
                raise RuntimeError(f"Wheel source content mismatch: {name}")
    cpu = args.cpu_python.resolve()
    subprocess.run(
        [
            str(cpu),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--disable-pip-version-check",
            "--target",
            str(out / "installed"),
            str(wheel),
        ],
        env=env,
        check=True,
    )
    cpu_run = subprocess.run(
        [str(cpu), "-I", "-c", PROBE],
        cwd=out,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if cpu_run.returncode:
        raise RuntimeError(cpu_run.stderr or cpu_run.stdout)
    report = json.loads(cpu_run.stdout)
    report["wheel"] = {
        "name": wheel.name,
        "bytes": wheel.stat().st_size,
        "sha256": sha256(wheel.read_bytes()).hexdigest(),
        "python_sources": len(sources),
        "source_date_epoch": int(env["SOURCE_DATE_EPOCH"]),
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
