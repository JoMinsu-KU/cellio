"""Offline self-clearance screen of the 30 Hz recorded native Panda poses."""
import argparse
import hashlib
from itertools import combinations
import json
from pathlib import Path
from importlib.util import find_spec

import numpy as np
from pxr import Usd, UsdGeom
import yaml

from cellio.inspection.study.cell_c_motion import NAMES, _array, _bounds, _gjk_bound, _support_points, _transform


def default_xrdf():
    spec = find_spec("isaacsim")
    roots = spec.submodule_search_locations if spec else ()
    for root in roots or ():
        extension = Path(root) / "exts" / "isaacsim.robot_motion.cumotion"
        path = next(extension.rglob("franka/robot.xrdf"), None)
        if path:
            return path
    raise FileNotFoundError("Install the Isaac Sim 6.2 cuMotion extension or supply --xrdf")


def audit_stage(stage, end, xrdf):
    config = yaml.safe_load(xrdf.read_bytes())
    # The XRDF names fingertip virtual frames; its mask covers the corresponding
    # finger link, for which this audit uses the larger complete visible mesh.
    def name(s):
        return s.replace('fingertip','finger')
    excluded = {tuple(sorted((name(left),name(right)))) for left,others in config['self_collision']['ignore'].items() for right in others}
    indices = [(i,j) for i,j in combinations(range(len(NAMES)),2)
               if tuple(sorted((NAMES[i],NAMES[j]))) not in excluded]
    robot = stage.GetPrimAtPath('/World/Robot/Panda')
    cache = UsdGeom.XformCache(Usd.TimeCode(0))
    links = [robot.GetChild(n) for n in NAMES]
    hulls = []
    for link in links:
        pieces=[]
        inv = np.linalg.inv(_array(cache.GetLocalToWorldTransform(link)))
        for prim in Usd.PrimRange(link):
            if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(0))=='invisible':
                continue
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(),dtype=float)
            if len(points):
                pieces.append(_transform(inv @ _array(cache.GetLocalToWorldTransform(prim)),points))
        hulls.append(_support_points(np.vstack(pieces)))
    nearest,minimum = None,float('inf')
    unresolved = {}
    for t in range(end+1):
        cache.SetTime(Usd.TimeCode(float(t)))
        world = [_transform(_array(cache.GetLocalToWorldTransform(link)),points) for link,points in zip(links,hulls)]
        mins,maxs = map(np.array,zip(*(_bounds(points) for points in world)))
        for i,j in indices:
            sep = np.maximum(mins[i]-maxs[j],mins[j]-maxs[i]).max()
            if sep < .0005:
                sep = max(sep,_gjk_bound(world[i],world[j]))
            sep = max(0.,float(sep))
            if sep < minimum:
                nearest,minimum = dict(frame=t,links=[NAMES[i],NAMES[j]]),sep
            if sep < .00001:
                key=NAMES[i]+' / '+NAMES[j]
                unresolved[key] = unresolved.get(key,0)+1
    return dict(method='Integer-frame USD transforms, convex hull of visible vendor mesh per link, 13 separating directions and convex support fallback.',
                xrdf=str(xrdf),xrdf_sha256=hashlib.sha256(xrdf.read_bytes()).hexdigest(),
                exclusion_policy='Franka cuMotion XRDF ignore pairs, with fingertip entries mapped to the complete finger links.',
                frames_checked=end+1,pairs_checked_per_frame=len(indices),
                checked_pairs=[[NAMES[i],NAMES[j]] for i,j in indices],
                excluded_pairs=[list(k) for k in sorted(excluded)],minimum_lower_bound_m=minimum,
                nearest=nearest,unresolved=unresolved,passes=not unresolved)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('--xrdf',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    stage=Usd.Stage.Open(str(args.run/'replay.usda'))
    result=audit_stage(stage,int(stage.GetEndTimeCode()),args.xrdf or default_xrdf())
    data=json.dumps(result,indent=2)
    if args.output: args.output.write_text(data+'\n',encoding='utf-8')
    else: print(data)
    return 0 if result['passes'] else 1


if __name__=='__main__':
    raise SystemExit(main())
