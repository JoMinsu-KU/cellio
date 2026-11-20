"""Check carried CAD against mounted equipment using separately recorded PhysX poses.

Run with the Blender-bundled standalone Python (USD, NumPy and scipy); no Kit required.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom
from scipy.spatial.transform import Rotation

from cellio.inspection.study.cell_c_motion import DIRECTIONS, _bounds, _gjk_bound, _support_points


STEP = .001
PIN_BORE_RAD = .00398  # conservative inscribed radius of the CAD's polygonal 8 mm bore
TOL = 1.e-8


def points_at(mesh, cache):
    transform = np.asarray(cache.GetLocalToWorldTransform(mesh.GetPrim()))
    xyz = np.asarray(mesh.GetPointsAttr().Get(),float)
    return xyz @ transform[:3,:3] + transform[3,:3]


def edge_indices(mesh):
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(),int)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(),int)
    ends = np.cumsum(counts)-1
    nxt = np.roll(indices,-1)
    nxt[ends] = indices[ends-counts+1]
    return np.unique(np.sort(np.stack((indices,nxt),axis=1),axis=1),axis=0)


class Environment:
    def __init__(self,stage,frame,final):
        root = stage.GetPrimAtPath("/World/Equipment")
        start = UsdGeom.XformCache(Usd.TimeCode(frame))
        finish = UsdGeom.XformCache(Usd.TimeCode(final))
        self.meshes,self.hulls,self.tags,self.pins = [],[],[],{}
        self.moving=[]
        lo,hi = [],[]
        for p in Usd.PrimRange(root):
            if not p.IsA(UsdGeom.Mesh):
                continue
            path=str(p.GetPath())
            top=path.split("/")[3]
            if top in ("Infeed_bracket","WB01_InspectionBracket","Accepted_bracket"):
                continue
            mesh=UsdGeom.Mesh(p)
            pts=points_at(mesh,start)
            if not len(pts):
                continue
            # Gauge and clamps can begin parking while a bracket travels away.
            # All non-tray functional meshes enter the sample-time transform check.
            if top not in ("Part_handling","InspectionSkid") and not ("Architecture" in top):
                self.moving.append((len(self.meshes),p,_support_points(np.asarray(mesh.GetPointsAttr().Get(),float))))
            hull=_support_points(pts)
            if "ToolingLocator" in top:
                self.pins[len(self.meshes)]=(pts,edge_indices(mesh))
            self.meshes.append(path)
            self.tags.append(top)
            self.hulls.append(hull)
            a,b=_bounds(hull)
            lo.append(a)
            hi.append(b)
        self.lo,self.hi=np.asarray(lo),np.asarray(hi)
        self.last_matrices={}
        gauge_names=("Comparator","Column","Gauge","Stylus","Ruby","Sliding","Cantilever","Quill",
                     "Scale","Pointer","Carriage","LinearGuide","Knob","GuideFixing","SignalCable",
                     "CableGland","CrossLock","Engraved")
        self.gauge=np.asarray([n.startswith(gauge_names) for n in self.tags])
        self.interfaces=[
            (i, support_station(p) or (.077,.001,.882)) for i,p in enumerate(self.meshes)
            if i in self.pins or support_station(p) is not None]

    def set_time(self,time):
        cache=UsdGeom.XformCache(Usd.TimeCode(time))
        for i,p,local in self.moving:
            m=np.asarray(cache.GetLocalToWorldTransform(p))
            if i not in self.last_matrices or not np.array_equal(self.last_matrices[i],m):
                self.hulls[i]=local @ m[:3,:3]+m[3,:3]
                self.lo[i],self.hi[i]=_bounds(self.hulls[i])
                self.last_matrices[i]=m


def grasp_model(stage,part):
    root=stage.GetPrimAtPath("/World/ActiveParts/"+part)
    cache=UsdGeom.XformCache(Usd.TimeCode(0))
    inv=np.linalg.inv(np.asarray(cache.GetLocalToWorldTransform(root)))
    hulls={}
    for p in Usd.PrimRange(root.GetChild("Visual")):
        if p.IsA(UsdGeom.Mesh):
            mesh=UsdGeom.Mesh(p)
            m=np.asarray(cache.GetLocalToWorldTransform(p)) @ inv
            vs=np.asarray(mesh.GetPointsAttr().Get(),float)
            hulls[str(p.GetPath()).split("/")[-1]]=_support_points(vs @ m[:3,:3]+m[3,:3])
    if len(hulls)<5:
        raise ValueError(f"Missing payload meshes for {part}: {list(hulls)}")
    return hulls


def pin_bound(pin,rotation,xyz):
    pts,edges=pin
    local=(pts-xyz) @ rotation
    bottom,top=0.,.012
    z=local[:,2]
    if z.max()<bottom:
        return float(bottom-z.max()),None
    if z.min()>top:
        return float(z.min()-top),None
    points=[local[(z>=bottom)&(z<=top),:2]]
    a,b=local[edges[:,0]],local[edges[:,1]]
    for plane in (bottom,top):
        crosses=(np.minimum(a[:,2],b[:,2])<plane)&(np.maximum(a[:,2],b[:,2])>plane)
        left,right=a[crosses],b[crosses]
        fraction=(plane-left[:,2])/(right[:,2]-left[:,2])
        points.append(left[:,:2]+fraction[:,None]*(right[:,:2]-left[:,:2]))
    xy=np.vstack(points)
    if not len(xy):
        return 0.,None
    centres=((-0.045,0.026),(0.045,0.026))
    furthest=min(float(np.linalg.norm(xy-np.asarray(c),axis=1).max()) for c in centres)
    value=PIN_BORE_RAD-furthest
    return value,value


def support_station(path):
    if "Formed_endless_belt" in path or "Edge_tracking_mark" in path:
        return (-.440,-.248,.899)
    elif "DatumRest" in path:
        return (.077,.001,.882)
    elif "Removable_nest" in path:
        return (.500,-.190 if "/Routing_tray_OK/" in path else .160,.854)
    return None


def support_at(path, xyz):
    origin=support_station(path)
    return origin is not None and np.linalg.norm(xyz[:2]-origin[:2])<.003 and -.00020<=xyz[2]-origin[2]<=.00020


def evaluate(run):
    replay=run/"replay.usda"
    # Keep Kit-specific vendor assets out of this geometry-only offline audit.
    paths=Usd.StagePopulationMask()
    paths.Add("/World/Equipment")
    paths.Add("/World/ActiveParts")
    stage=Usd.Stage.OpenMasked(str(replay.resolve()),paths)
    rows={r["frame"]:r for r in map(json.loads,(run/"motion.jsonl").read_text().splitlines())}
    acts=[e for e in json.loads((run/"events.json").read_text()) if e["event"] in ("grasp","release")]
    if len(acts)!=8 or any(e["event"]!=("grasp","release")[i%2] for i,e in enumerate(acts)):
        raise ValueError("Expected four explicit grasp/release intervals")
    models={p:grasp_model(stage,p) for p in ("P1","P2")}
    stats={"passed":False,"intervals":[],"minimum_unintended_sampled_bound_m":None,
           "min_pin_hole_radial_bound_m":None,"unresolved":[],"intended_support_contacts":{},
           "interpolation_limit_m":STEP,"max_interpolated_vertex_step_m":0.,
           "method":"Recorded PhysX root poses; shortest-arc quaternions and linear translation; CAD convex hull per mesh, directional bounds and GJK. Pin vertices and edges clipped to the 12 mm foot slab in its moving local frame. 3.98 mm inscribed bore radius. Nonpositive unintended bounds are reported as unresolved."}
    min_general=radial_min=min_tool=min_nonmating=math.inf
    failures={}
    for grasp,release in zip(acts[::2],acts[1::2]):
        part=grasp["part"]
        begin,end=grasp["frame"],release["frame"]
        if release["part"]!=part or end<=begin:
            raise ValueError(f"Inconsistent pair: {grasp!r} {release!r}")
        env=Environment(stage,begin,end)
        shapes=models[part]
        radius=max(np.linalg.norm(h,axis=1).max() for h in shapes.values())
        interval={"part":part,"source":grasp["station"],"target":release["station"],
                  "first_frame":begin,"last_frame":end,"interpolated_poses":0,"equipment_meshes":len(env.meshes)}
        def audit(frame,xyz,q):
            nonlocal min_general,radial_min,min_tool,min_nonmating
            interval["interpolated_poses"]+=1
            env.set_time(frame)
            rot=Rotation.from_quat(q,scalar_first=True).as_matrix()
            for name,local in shapes.items():
                vertices=local@rot.T+xyz
                lo,hi=_bounds(vertices)
                gaps=np.maximum(env.lo-hi,lo-env.hi).max(axis=1)
                # A large projected separation already certifies this pair without GJK.
                for ix in np.flatnonzero(gaps<.0018):
                    path=env.meshes[ix]
                    if "_Foot" in name and ix in env.pins and gaps[ix]<=TOL:
                        certificate,rad=pin_bound(env.pins[ix],rot,xyz)
                        gaps[ix]=max(gaps[ix],certificate)
                        if rad is not None and rad>0:
                            radial_min=min(radial_min,rad)
                    if gaps[ix]<=TOL:
                        gaps[ix]=max(gaps[ix],_gjk_bound(vertices,env.hulls[ix]))
                    if gaps[ix]<=TOL and "_Foot" in name and support_at(path,xyz):
                        # Only allow actual underside contact at a fitted station.
                        support_z=env.hulls[ix][:,2].max()
                        penetration=float(support_z-vertices[:,2].min())
                        if penetration<=.00020:
                            record=stats["intended_support_contacts"].setdefault(path,
                                    {"samples":0,"max_apparent_penetration_m":0.})
                            record["samples"]+=1
                            record["max_apparent_penetration_m"]=max(record["max_apparent_penetration_m"],penetration)
                            gaps[ix]=1.
                            continue
                    if gaps[ix]<=TOL:
                        key=(part,name,path,grasp["station"],release["station"])
                        r=failures.setdefault(key,{"part":part,"mesh":name,"equipment":path,
                                    "leg":[grasp["station"],release["station"]],"first_frame":frame,"samples":0})
                        r["samples"]+=1
                ix=int(np.argmin(gaps))
                if gaps[ix]<min_general:
                    min_general=float(gaps[ix])
                    stats["closest"]={"part":part,"part_mesh":name,"equipment":env.meshes[ix],"frame":frame}
                tool_indices=np.flatnonzero(env.gauge)
                item=int(tool_indices[np.argmin(gaps[tool_indices])])
                if gaps[item]<min_tool:
                    min_tool=float(gaps[item])
                    stats["closest_gauge"]={"part":part,"part_mesh":name,"equipment":env.meshes[item],"frame":frame}
                nonmating=gaps.copy()
                if "_Foot" in name:
                    for i,site in env.interfaces:
                        if abs(xyz[0]-site[0])<.003 and abs(xyz[1]-site[1])<.003 and abs(xyz[2]-site[2])<.020:
                            nonmating[i]=np.inf
                item=int(np.argmin(nonmating))
                if nonmating[item]<min_nonmating:
                    min_nonmating=float(nonmating[item])
                    stats["closest_nonmating"]={"part":part,"part_mesh":name,"equipment":env.meshes[item],"frame":frame}
        for frame in range(begin,end):
            previous=rows[frame]["parts"][part]
            current=rows[frame+1]["parts"][part]
            a,b=np.array(previous["xyz"]),np.array(current["xyz"])
            q,r=np.array(previous["wxyz"]),np.array(current["wxyz"])
            q=q/np.linalg.norm(q)
            r=r/np.linalg.norm(r)
            dot=float(np.dot(q,r))
            if dot<0.:
                dot,r=-dot,-r
            arc=math.acos(min(1.,dot))
            distance=np.linalg.norm(b-a)+2*arc*radius
            n=max(1,math.ceil(distance/STEP))
            stats["max_interpolated_vertex_step_m"]=max(stats["max_interpolated_vertex_step_m"],distance/n)
            for i in range(n):
                f=i/n
                quat=q if arc<1.e-9 else (math.sin((1-f)*arc)*q+math.sin(f*arc)*r)/math.sin(arc)
                audit(frame+f,a+(b-a)*f,quat)
        endpose=rows[end]["parts"][part]
        audit(end,endpose["xyz"],endpose["wxyz"])
        stats["intervals"].append(interval)
    stats["minimum_unintended_sampled_bound_m"]=min_general
    stats["minimum_gauge_sampled_bound_m"]=min_tool
    stats["minimum_nonmating_sampled_bound_m"]=min_nonmating
    stats["nonmating_definition"]="Excludes only foot vs fitted belt, rest, tray seat or its two locator pins when the part origin is within 3 mm in both horizontal axes and 20 mm in Z of that station. The primary audit and separate radial bound still check these interfaces."
    stats["min_pin_hole_radial_bound_m"]=radial_min if math.isfinite(radial_min) else None
    stats["unresolved"]=list(failures.values())
    stats["passed"]=not failures and math.isfinite(radial_min)
    stats["run_inputs_sha256"]={p:hashlib.sha256((run/p).read_bytes()).hexdigest() for p in ("replay.usda","live.usda","motion.jsonl","events.json")}
    return stats


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("run",type=Path)
    args=parser.parse_args()
    result=evaluate(args.run)
    (args.run/"payload-clearance.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("passed","minimum_unintended_sampled_bound_m","min_pin_hole_radial_bound_m","unresolved")}),flush=True)
    raise SystemExit(0 if result["passed"] else 2)
