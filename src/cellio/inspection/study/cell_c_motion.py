"""Joint-limited kinematic commissioning for the composed Franka visual asset."""
from itertools import product
from pathlib import Path
import math
import numpy as np
from pxr import Gf, Usd, UsdGeom
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from scipy.spatial import ConvexHull

FPS = 30
TCP_OFFSET = .1034
GRASP_OFFSET = np.array([-.013, .026, .049])
JOINT_LO = np.array([-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -.0175, -2.8973])
JOINT_HI = np.array([2.8973, 1.7628, 2.8973, -.0698, 2.8973, 3.7525, 2.8973])
JOINT_SPEED = np.array([2.175,2.175,2.175,2.175,2.61,2.61,2.61])
HOME = np.array([0.,-np.pi/4,0.,-3*np.pi/4,0.,np.pi/2,np.pi/4])
NAMES = [f"panda_link{i}" for i in range(8)] + ["panda_hand", "panda_leftfinger", "panda_rightfinger"]


def _array(matrix):
    return np.asarray(matrix).T.copy()


def _transform(matrix, points):
    return points @ matrix[:3,:3].T + matrix[:3,3]


def _matrix(value):
    return Gf.Matrix4d(value.T.tolist())


class CellCMotion:
    """Kinematic USD articulation playback. Clearance and FK are measured at authored frames."""
    def __init__(self, stage, start_frame=0, *, check_clearance=True):
        self.stage, self.frame = stage, start_frame
        self.robot = stage.GetPrimAtPath('/World/Robot/Panda')
        asset_file = self.robot.GetMetadata('references').GetAddedOrExplicitItems()[0].assetPath
        asset = Usd.Stage.Open(asset_file)
        model = asset.GetDefaultPrim()
        self.neutral = [_array(UsdGeom.Xformable(model.GetChild(n)).GetLocalTransformation()) for n in NAMES]
        self.prims = [self.robot.GetChild(n) for n in NAMES]
        self.ops = [UsdGeom.Xformable(p).MakeMatrixXform() for p in self.prims[1:]]
        self._relative = [np.linalg.inv(self.neutral[i-1]) @ self.neutral[i] for i in range(1,8)]
        self._wrist = [np.linalg.inv(self.neutral[7]) @ m for m in self.neutral[8:]]
        self.base = _array(UsdGeom.XformCache().GetLocalToWorldTransform(self.robot))
        self.q = HOME.copy()
        self.gap = .040
        # Sweep the elbow away from the comparator column during the low front insertion.
        self.fixture_swivel = -.8
        self.samples = 0
        self.max_error = self.max_angle = self.max_joint_speed = self.max_fk_difference = 0.
        self.min_limit = math.inf
        self._set_pose()
        self.clearance = _Clearance(stage, self.robot, self.prims) if check_clearance else None
        self._last_jaw = None
        self._pinch_rays = []
        for finger in self.prims[9:]:
            mesh = next(UsdGeom.Mesh(p) for p in Usd.PrimRange(finger) if p.IsA(UsdGeom.Mesh))
            indices = list(mesh.GetFaceVertexIndicesAttr().Get())
            tris, offset = [], 0
            for count in mesh.GetFaceVertexCountsAttr().Get():
                face = indices[offset:offset+count]
                tris.extend((face[0], face[k], face[k+1]) for k in range(1,count-1))
                offset += count
            local_points = np.asarray(mesh.GetPointsAttr().Get(),dtype=float)
            self._pinch_rays.append((mesh.GetPrim(),local_points[np.asarray(tris)]))

    def _chain(self, q):
        local = self.neutral[0]
        links, axes, origins = [local], [], []
        for neutral, theta in zip(self._relative, q):
            local = local @ neutral
            axes.append(local[:3,2].copy())
            origins.append(local[:3,3].copy())
            c, s = math.cos(theta), math.sin(theta)
            temp = local.copy()
            local[:3,0] = c*temp[:3,0] + s*temp[:3,1]
            local[:3,1] = -s*temp[:3,0] + c*temp[:3,1]
            links.append(local)
        hand = local @ self._wrist[0]
        fingers = [local @ rest for rest in self._wrist[1:]]
        for sign, finger in zip((1.,-1.), fingers):
            finger[:3,3] += sign * self.gap/2 * hand[:3,1]
        links.extend([hand,*fingers])
        worldhand = self.base @ hand
        tcp = worldhand[:3,3] + TCP_OFFSET * worldhand[:3,2]
        axes = np.array(axes) @ self.base[:3,:3].T
        origins = _transform(self.base, np.array(origins))
        return links, worldhand, tcp, axes, origins

    def solve(self, target, yaw=0.0, seed=None, orientation=None):
        desired = Rotation.from_euler('z',yaw).as_matrix() @ np.diag([1.,-1.,-1.]) if orientation is None else orientation
        seed = self.q if seed is None else seed
        saved = {}
        target = np.asarray(target)
        radial = np.linalg.norm(target[:2]-[.064,.027])
        blend = .5*(1+np.cos(np.pi*min(1.,radial/.30)))
        blend *= .5*(1+np.cos(np.pi*np.clip((target[2]-1.05)/.13,0.,1.)))
        def update(q):
            if 'q' not in saved or not np.array_equal(q, saved['q']):
                _, hand, tcp, axes, origins = self._chain(q)
                r = Rotation.from_matrix(hand[:3,:3] @ desired.T).as_rotvec()
                swivel = np.array([0.,0.,.003,0.,0.,0.,0.])
                J = np.vstack([np.cross(axes, tcp-origins).T, .22*axes.T, swivel, .00015*np.eye(7)])
                saved.update(q=q.copy(), r=np.r_[tcp-target,.22*r,swivel@q-.003*self.fixture_swivel*blend,.00015*(q-seed)], J=J)
            return saved
        solution = least_squares(lambda q: update(q)['r'], seed, jac=lambda q: update(q)['J'],
                                 bounds=(JOINT_LO, JOINT_HI), ftol=1e-10, xtol=1e-9,gtol=1e-9,
                                 max_nfev=45)
        _, hand, pos, _, _ = self._chain(solution.x)
        ep = np.linalg.norm(pos-target)
        ea = Rotation.from_matrix(hand[:3,:3] @ desired.T).magnitude()
        return solution.x, float(ep), float(ea)

    def _set_pose(self):
        local, _, expected, _, _ = self._chain(self.q)
        for op, transform in zip(self.ops, local[1:]):
            op.Set(_matrix(transform), self.frame)
        actual = self.read_tcp()
        self.max_fk_difference = max(self.max_fk_difference, np.linalg.norm(expected-actual))
        return local, actual

    def read_tcp(self, time=None):
        clock = Usd.TimeCode(float(self.frame if time is None else time))
        hand = UsdGeom.XformCache(clock).GetLocalToWorldTransform(self.prims[8])
        return tuple(hand.Transform(Gf.Vec3d(0,0,TCP_OFFSET)))

    def read_gap(self):
        cache = UsdGeom.XformCache(Usd.TimeCode(float(self.frame)))
        centers = [np.array(cache.GetLocalToWorldTransform(p).ExtractTranslation()) for p in self.prims[9:]]
        return float(np.linalg.norm(centers[0]-centers[1]))

    def read_pad_gap(self):
        """Raycast from the calibrated TCP sideways to both actual vendor finger meshes."""
        cache = UsdGeom.XformCache(Usd.TimeCode(float(self.frame)))
        origin = np.array(self.read_tcp())
        hand = _array(cache.GetLocalToWorldTransform(self.prims[8]))
        distances = []
        for direction,(prim,tris) in zip((hand[:3,1],-hand[:3,1]),self._pinch_rays):
            xyz = _transform(_array(cache.GetLocalToWorldTransform(prim)),tris)
            a,b,c = xyz[:,0],xyz[:,1],xyz[:,2]
            e1,e2 = b-a,c-a
            h = np.cross(direction,e2)
            det = np.einsum('ij,ij->i',e1,h)
            nonzero = abs(det)>1e-12
            inv = np.divide(1.,det,out=np.zeros_like(det),where=nonzero)
            source = origin-a
            u = inv*np.einsum('ij,ij->i', source,h)
            q = np.cross(source,e1)
            v = inv*(q @ direction)
            t = inv*np.einsum('ij,ij->i',e2,q)
            hits = t[nonzero & (u>=-1e-9) & (v>=-1e-9) & (u+v<=1.+1e-9) & (t>=0.)]
            if not len(hits):
                raise RuntimeError('Missing native finger mesh on TCP pinch ray')
            distances.append(float(hits.min()))
        return sum(distances)

    def _sample(self, phase, callback):
        self.frame += 1
        local, tcp = self._set_pose()
        self.samples += 1
        self.min_limit = min(self.min_limit, float(np.min(np.minimum(self.q-JOINT_LO,JOINT_HI-self.q))))
        if self.clearance:
            self.clearance.check([self.base @ m for m in local], self.frame, phase)
        if callback:
            callback(self.frame, tcp)
        return tcp

    def move_tcp(self, xyz, *, phase='', on_sample=None, yaw=0.0):
        start = np.array(self.read_tcp())
        end = np.asarray(xyz, dtype=float)
        desired = Rotation.from_euler('z',yaw).as_matrix() @ np.diag([1.,-1.,-1.])
        initial = self._chain(self.q)[1][:3,:3]
        arc = Rotation.from_matrix(desired @ initial.T).as_rotvec()
        n = max(1, math.ceil(max(np.linalg.norm(end-start)/.18, np.linalg.norm(arc)/.8)*FPS))
        for t in range(1,n+1):
            f = t/n
            target = start + f*(end-start)
            R = Rotation.from_rotvec(f*arc).as_matrix() @ initial
            next_q, ep, ea = self.solve(target, orientation=R)
            if ep > .0005 or ea > .005:
                raise RuntimeError(f'IK {phase}: target {target.tolist()}, error {ep:.6f} m/{ea:.6f} rad at frame {self.frame}')
            old = self.q
            delta = next_q-old
            # Bound individual joint velocities even near singular regions.
            count = max(1, math.ceil(np.max(np.abs(delta)/(.8*JOINT_SPEED))*FPS))
            for i in range(1,count+1):
                self.q = old+delta*(i/count)
                self.max_joint_speed = max(self.max_joint_speed,float(np.max(np.abs(delta)*FPS/count)))
                actual = self._sample(phase, on_sample)
            self.max_error = max(self.max_error,float(np.linalg.norm(actual-target)))
            self.max_angle = max(self.max_angle,ea)
        return self.read_tcp()

    def hold(self, *, phase='', on_sample=None):
        return self._sample(phase,on_sample)

    def set_gap(self, gap_m, *, phase='', on_sample=None):
        if not 0 <= gap_m <= .08:
            raise ValueError('Franka finger travel requires a gap between 0 and .08 m')
        old = self.gap
        n = max(1, math.ceil(abs(gap_m-old)/.08*FPS))
        for t in range(1,n+1):
            self.gap = old+(gap_m-old)*t/n
            self._sample(phase,on_sample)
        self._last_jaw = self.read_gap()
        return self._last_jaw

    def diagnostics(self):
        return dict(kinematics='native vendor zero-pose link transforms, constrained numerical IK, local USD pose samples',
                    samples=self.samples, fps=FPS, max_tcp_error_m=self.max_error, max_orientation_error_rad=self.max_angle,
                    max_stage_fk_difference_m=self.max_fk_difference, minimum_joint_limit_margin_rad=self.min_limit,
                    max_joint_speed_rad_s=self.max_joint_speed, last_jaw_origin_separation_m=self._last_jaw,
                    final_jaw_mesh_separation_at_tcp_m=self.read_pad_gap(),
                    clearance=self.clearance.report() if self.clearance else None)


def _support_points(points):
    """Vertices of a conservative hull, including degenerate flat artwork."""
    points = np.unique(points, axis=0)
    if len(points) <= 8:
        return points
    if np.linalg.matrix_rank(points-points[0], tol=1e-10) < 3:
        return points
    return points[ConvexHull(points).vertices]


# Unit support directions. A positive separation on even one direction proves
# a lower bound on Euclidean clearance for the entire original pair of meshes.
DIRECTIONS = np.array([p for p in product((-1.,0.,1.),repeat=3)
                       if next((v for v in p if v != 0),0) > 0])
DIRECTIONS /= np.linalg.norm(DIRECTIONS,axis=1)[:,None]


def _bounds(points):
    p = points @ DIRECTIONS.T
    return p.min(axis=0), p.max(axis=0)


def _gjk_bound(a,b):
    """Conservative separation certificate of convex hulls, or zero if unresolved."""
    direction = a.mean(axis=0)-b.mean(axis=0)
    if np.linalg.norm(direction)<1e-12:
        direction = np.array([1.,0.,0.])
    simplex = []
    certified = 0.
    for _ in range(25):
        unit = direction/np.linalg.norm(direction)
        support = a[np.argmin(a @ unit)] - b[np.argmax(b @ unit)]
        lower = float(unit @ support)
        certified = max(certified, lower)
        if certified > .00002:
            return certified
        if any(np.linalg.norm(support-p)<1e-9 for p in simplex):
            return certified
        simplex.append(support)
        points = np.asarray(simplex)
        # Closest point on every feasible subsimplex. Four points suffice in R3.
        from itertools import combinations
        best, result = math.inf, None
        for size in range(1,min(4,len(points))+1):
            for ids in combinations(range(len(points)),size):
                subset = points[list(ids)]
                if size == 1:
                    weights = np.array([1.])
                else:
                    columns = (subset[1:]-subset[0]).T
                    coeff = np.linalg.lstsq(columns,-subset[0],rcond=None)[0]
                    weights = np.r_[1-coeff.sum(),coeff]
                if np.any(weights < -1e-9):
                    continue
                nearest = weights @ subset
                norm2 = float(nearest @ nearest)
                if norm2 < best:
                    best = norm2
                    result = ([v for v,w in zip(subset,weights) if w>1e-8],nearest)
        if result is None or best < 1e-16:
            return certified
        simplex,direction = result
    return certified


class _Clearance:
    """Conservative convex support separation of moving vendor link meshes vs equipment."""
    def __init__(self, stage, robot, links):
        self.stage = stage
        self.minimum = math.inf
        self.closest = None
        self.failure_pairs = {}
        self.frames_checked = 0
        self.by_phase = {}
        cache = UsdGeom.XformCache()
        self.robot = []
        for idx,link in enumerate(links[1:],1):
            pieces = []
            inv = np.linalg.inv(_array(cache.GetLocalToWorldTransform(link)))
            for prim in Usd.PrimRange(link):
                if prim.IsA(UsdGeom.Mesh):
                    mesh = UsdGeom.Mesh(prim)
                    points = np.asarray(mesh.GetPointsAttr().Get(),dtype=float)
                    if len(points):
                        tf = inv @ _array(cache.GetLocalToWorldTransform(prim))
                        pieces.append(_transform(tf,points))
            self.robot.append((idx,link.GetName(),_support_points(np.vstack(pieces))))
        equipment = stage.GetPrimAtPath('/World/Equipment')
        self.env = []
        mins,maxs=[],[]
        self.dynamic=[]
        for prim in Usd.PrimRange(equipment):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            path = str(prim.GetPath())
            top = path.split('/')[3]
            if any(s in top.lower() for s in ('bracket','architecture')) or top=='_materials':
                continue
            if top=='InspectionSkid' and '/SignalTower/' not in path:
                continue
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(),dtype=float)
            if not len(points):
                continue
            local = _support_points(points)
            world = _transform(_array(cache.GetLocalToWorldTransform(prim)),local)
            lo,hi = _bounds(world)
            idx = len(self.env)
            self.env.append([path,world])
            mins.append(lo)
            maxs.append(hi)
            if top not in ('Part_handling','InspectionSkid'):
                self.dynamic.append((idx,mesh,local))
        self.mins, self.maxs = np.vstack(mins),np.vstack(maxs)

    def check(self, transforms, frame, phase):
        time = Usd.TimeCode(float(frame))
        cache = UsdGeom.XformCache(time)
        # Static equipment stays cached. Fixture components may be actuated
        # in another controller; read the temporally composed USD transforms.
        for idx,mesh,local in self.dynamic:
            world = _transform(_array(cache.GetLocalToWorldTransform(mesh.GetPrim())),local)
            if mesh.GetPointsAttr().ValueMightBeTimeVarying():
                world = _transform(_array(cache.GetLocalToWorldTransform(mesh.GetPrim())),
                                   np.asarray(mesh.GetPointsAttr().Get(time)))
            self.env[idx][1] = world
            self.mins[idx],self.maxs[idx] = _bounds(world)
        minimum = math.inf
        record = None
        for idx,name,local in self.robot:
            verts = _transform(transforms[idx],local)
            lo, hi = _bounds(verts)
            gaps = np.maximum(self.mins-hi,lo-self.maxs).max(axis=1)
            for ix in np.flatnonzero(gaps < .0005):
                obstacle = self.env[ix]
                gaps[ix] = max(0.,gaps[ix],_gjk_bound(verts,obstacle[1]))
                if gaps[ix] < .00001:
                    key = (phase,name,obstacle[0])
                    self.failure_pairs[key] = self.failure_pairs.get(key,0)+1
            near = int(np.argmin(gaps))
            deck = float(verts[:,2].min()-.82)
            if deck < gaps[near]:
                sep,obstacle = deck,'tooling table top plane'
            else:
                sep,obstacle = float(gaps[near]),self.env[near][0]
            if sep < minimum:
                minimum,record = sep,dict(frame=frame,phase=phase,link=name,obstacle=obstacle)
        self.frames_checked += 1
        previous = self.by_phase.get(phase)
        if previous is None or minimum < previous['lower_bound_m']:
            self.by_phase[phase] = dict(lower_bound_m=minimum,**record)
        if minimum < self.minimum:
            self.minimum,self.closest = minimum,record

    def report(self):
        return dict(method='Conservative convex hulls of EACH moving vendor link and EACH cell functional equipment mesh; 13 separating axes plus GJK fallback for near pairs; table top plane.',
                    excluded='Fixed mounting base, below-deck hardware, architectural walls, carried/other parts, self-collision; discrete samples, no force/contact dynamics.',
                    moving_links=len(self.robot),equipment_meshes=len(self.env),frames=self.frames_checked,
                    minimum_sampled_lower_bound_m=self.minimum,nearest=self.closest,
                    unresolved_pairs=[dict(phase=k[0],link=k[1],obstacle=k[2],frames=v)
                                      for k,v in self.failure_pairs.items()],by_phase=self.by_phase)
