"""Install an exact local wheel in a new venv and replay both packaged Cell C examples."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--environment",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    directory=args.environment.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    venv.EnvBuilder(with_pip=True,system_site_packages=False).create(directory)
    py=directory / ("Scripts/python.exe" if os.name=="nt" else "bin/python")
    wheel=args.wheel.resolve()
    subprocess.run([str(py),"-I","-m","pip","install","--disable-pip-version-check",str(wheel)],cwd=directory,check=True)
    subprocess.run([str(py),"-I","-m","pip","check"],cwd=directory,check=True)
    probe=("import importlib.metadata as m, importlib.util as u, json,sys; from pathlib import Path; import cellio; "
           "print(json.dumps(dict(version=m.version('cellio'), installed_in_venv=Path(cellio.__file__).is_relative_to(sys.prefix), "
           "absent={n:u.find_spec(n) is None for n in ('isaacsim','pxr','omni','numpy','scipy','torch')})))")
    package=json.loads(subprocess.check_output([str(py),"-I","-c",probe],cwd=directory))
    if not package["installed_in_venv"] or not all(package["absent"].values()):
        raise RuntimeError("Unexpected simulator, numerical stack or import path")
    examples={}
    for name,expected in (("normal",0),("stale_ok",5)):
        run=subprocess.run([str(py),"-I","-m","cellio","inspection","replay","--example",name,"--assert-pass"],
                           cwd=directory,capture_output=True)
        if run.returncode != expected:
            raise RuntimeError(f"{name} exit {run.returncode}: {run.stderr!r}")
        output=json.loads(run.stdout)
        examples[name]={"exit":run.returncode,"verdict":output["verdict"],"bins":[r["observed_bin"] for r in output["parts"]],
                        "owners":[r["result_part_id"] for r in output["parts"]]}
    packages=json.loads(subprocess.check_output([str(py),"-I","-m","pip","list","--format=json",
                                                 "--disable-pip-version-check"],cwd=directory))
    report={"wheel":wheel.name,"wheel_bytes":wheel.stat().st_size,"wheel_sha256":sha256(wheel.read_bytes()).hexdigest(),
            "system":platform.system(),"kernel":platform.release(),"python":platform.python_version(),
            "package":package,"dependencies":packages,"examples":examples,"pip_check":"passed"}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(report,indent=2,sort_keys=True)+"\n").encode("utf-8"))
    print(json.dumps(examples))


if __name__=="__main__":
    main()
