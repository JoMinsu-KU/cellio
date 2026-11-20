"""Check tooth/lug surface separation at grasp events against the recorded USD meshes."""
import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom


def triangles(mesh, cache):
    idx = mesh.GetFaceVertexIndicesAttr().Get()
    faces, offset = [], 0
    for count in mesh.GetFaceVertexCountsAttr().Get():
        face = idx[offset:offset+count]
        faces.extend((face[0],face[k],face[k+1]) for k in range(1,count-1))
        offset += count
    local = np.asarray(mesh.GetPointsAttr().Get(cache.GetTime()),dtype=float)
    tf = np.asarray(cache.GetLocalToWorldTransform(mesh.GetPrim()))
    return (local @ tf[:3,:3] + tf[3,:3])[faces]


def first_hit(tris, origin, direction):
    a,b,c = tris[:,0],tris[:,1],tris[:,2]
    e1,e2 = b-a,c-a
    h = np.cross(direction,e2)
    det = np.einsum('ij,ij->i',e1,h)
    valid = abs(det)>1e-12
    inv = np.divide(1.,det,out=np.zeros_like(det),where=valid)
    source = origin-a
    u = inv*np.einsum('ij,ij->i', source,h)
    q = np.cross(source,e1)
    v = inv*(q @ direction)
    t = inv*np.einsum('ij,ij->i',e2,q)
    hits = t[valid & (u>=-1e-9) & (v>=-1e-9) & (u+v<=1.+1e-9) & (t>=0.)]
    if not len(hits):
        raise ValueError('Missing lug/finger surface on witness ray')
    return float(hits.min())


def audit(folder):
    stage = Usd.Stage.Open(str(folder/'replay.usda'))
    events = json.loads((folder/'events.json').read_bytes())
    robot = stage.GetPrimAtPath('/World/Robot/Panda')
    finger_meshes = [next(UsdGeom.Mesh(p) for p in Usd.PrimRange(robot.GetChild(name))
                          if p.IsA(UsdGeom.Mesh)) for name in ('panda_leftfinger','panda_rightfinger')]
    rows = []
    for event in events:
        if event['event'] != 'grasp':
            continue
        clock = Usd.TimeCode(float(event['frame']))
        cache = UsdGeom.XformCache(clock)
        hand = np.asarray(cache.GetLocalToWorldTransform(robot.GetChild('panda_hand'))).T
        tcp = hand[:3,3]+.1034*hand[:3,2]
        part = stage.GetPrimAtPath('/World/ActiveParts/'+event['part'])
        lug = next(UsdGeom.Mesh(p) for p in Usd.PrimRange(part)
                   if p.IsA(UsdGeom.Mesh) and 'CrossDrilledEar' in str(p.GetPath()))
        lug_tris = triangles(lug,cache)
        jaw_tris = [triangles(mesh,cache) for mesh in finger_meshes]
        reads = []
        for dx in (-.005,-.003,.003,.005):
            point = tcp+hand[:3,0]*dx-hand[:3,2]*.003
            distances = []
            for jaw,dirn in zip(jaw_tris, (hand[:3,1],-hand[:3,1])):
                finger_t = first_hit(jaw,point,dirn)
                lug_t = first_hit(lug_tris,point,dirn)
                distances.append((finger_t,lug_t))
            clearances = [(jaw-lug)*1000 for jaw,lug in distances]
            reads.append(dict(dx_mm=dx*1000, height_above_tcp_mm=3,
                              lug_width_mm=1000*sum(lug for jaw,lug in distances),
                              jaw_width_mm=1000*sum(jaw for jaw,lug in distances),
                              left_gap_mm=clearances[0],right_gap_mm=clearances[1]))
        passed = all(abs(r[k]) < .20 for r in reads for k in ('left_gap_mm','right_gap_mm'))
        rows.append(dict(frame=event['frame'],part=event['part'],station=event['station'],
                         passes_geometric_fit=passed,reads=reads))
    return dict(method='Recorded native meshes; raycast at four serration teeth from within the lug on both jaw sides. Gap sign positive for free space, negative for interpenetration.', tolerance_mm=.20,
                expected_grasps=4,passes=(len(rows)==4 and all(r['passes_geometric_fit'] for r in rows)),grasps=rows)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    text = json.dumps(result,indent=2)
    if args.output:
        args.output.write_text(text+'\n',encoding='utf-8')
    else:
        print(text)
    raise SystemExit(0 if result['passes'] else 1)
