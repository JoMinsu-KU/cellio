"""Cell C workpiece handling. Positions in metres refer to the bracket underside.

A measured TCP carries the workpiece through an explicitly modelled virtual grasp.
Upon release both gravity and the free rigid-body/contact solve control the part.
The destination witness reads that solver, regardless of the requested route.
"""
from itertools import product
from math import cos, degrees, dist, hypot, pi, sin, tan

from cellio.adapters.isaac62.geometry import rotate


REGIONS = {
    "infeed": ((-.440, -.248, .899), (.190, .050)),
    "fixture": ((.077, .001, .882), (.066, .045)),
    "OK": ((.500, -.190, .854), (.068, .048)),
    "NG": ((.500, .160, .854), (.068, .048)),
}
STATIONS = {name: properties[0] for name, properties in REGIONS.items()}
GRIP = (-.013, .026, .049)
IDENTITY = (1., 0., 0., 0.)
ZERO = (0., 0., 0.)
DT = 1. / 120.
PARK = {"P1": (1.6, 0., -.6), "P2": (1.8, 0., -.6)}


def make_physics_scene(stage):
    from isaacsim.core.simulation_manager import PhysxScene, SimulationManager
    import omni.usd
    from pxr import UsdGeom, UsdPhysics

    if stage != omni.usd.get_context().get_stage():
        raise ValueError("Use the active Isaac USD stage for the physics scene")
    # Stage metrics belong on the active root, including when it wraps our design as a sublayer.
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    SimulationManager.switch_physics_engine("physx")
    scene = PhysxScene("/World/PhysicsScene")
    scene.set_steps_per_second(120)
    scene.set_solver_type("TGS")
    scene.set_enabled_gpu_dynamics(False)
    scene.set_gravity((0., 0., -9.81))
    authored_g = UsdPhysics.Scene.Get(stage, "/World/PhysicsScene").GetGravityMagnitudeAttr().Get()
    if abs(authored_g-9.81) > 1.e-5:
        raise RuntimeError(f"Incorrect authored gravity {authored_g} m/s²")
    return scene


def start_physics():
    import omni.timeline
    from isaacsim.core.simulation_manager import SimulationManager

    SimulationManager.setup_simulation(dt=DT, device="cpu")
    SimulationManager.enable_fabric(True)
    omni.timeline.get_timeline_interface().play()
    SimulationManager.initialize_physics()
    if SimulationManager.get_physics_simulation_view() is None:
        raise RuntimeError("Could not start native PhysX")
    return SimulationManager


class CellHandling:
    def __init__(self, stage):
        self.stage = stage
        self.paths = {part: "/World/ActiveParts/" + part for part in PARK}
        self.states = dict.fromkeys(self.paths, "parked")
        self.held = None
        self.offset = self.held_orientation = None
        self.transforms = {}
        self.body = None

    def _box(self, path, center, size, *, cylinder=False, angle=0.):
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

        if cylinder:
            shape = UsdGeom.Cylinder.Define(self.stage, path)
            shape.CreateAxisAttr("Z")
            shape.CreateRadiusAttr(size[0] / 2)
            shape.CreateHeightAttr(size[2])
            shape.AddTranslateOp().Set(Gf.Vec3d(*center))
        else:
            shape = UsdGeom.Cube.Define(self.stage, path)
            shape.CreateSizeAttr(1.)
            shape.AddTranslateOp().Set(Gf.Vec3d(*center))
            if angle:
                shape.AddRotateZOp().Set(degrees(angle))
            shape.AddScaleOp().Set(Gf.Vec3f(*size))
        shape.MakeInvisible()
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        contact = PhysxSchema.PhysxCollisionAPI.Apply(shape.GetPrim())
        contact.CreateRestOffsetAttr(0.)
        contact.CreateContactOffsetAttr(.00010)

    def _station_supports(self):
        from pxr import UsdGeom

        base = "/World/CellCContact"
        UsdGeom.Xform.Define(self.stage, base)
        self._box(base + "/Deck", (0., 0., .801), (1.56, .96, .038))
        self._box(base + "/IndexedBelt", (-.425, -.248, .890), (.397, .112, .018))
        for index, sign in enumerate((-1, 1)):
            self._box(base + f"/BeltGuide{index}", (-.439, -.248+sign*.0525, .905),
                      (.344, .0045, .009))
        fx, fy, fz = REGIONS["fixture"][0]
        self._box(base + "/GroundFace", (fx, fy, .863), (.198, .148, .014))
        for i, (dx, dy) in enumerate(((-.043, -.012), (.044, -.012), (0., .029))):
            self._box(base + f"/Datum{i}", (fx+dx, fy+dy, fz-.006),
                      (.0108, .0108, .012), cylinder=True)
        for i, dx in enumerate((-.045, .045)):
            self._box(base + f"/Locator{i}", (fx+dx, fy+.026, .88075),
                      (.0073, .0073, .0215), cylinder=True)
        for name in ("OK", "NG"):
            x, y, z = REGIONS[name][0]
            self._box(base + "/" + name + "Nest", (x, y, z-.008), (.212, .153, .016))
            for i, (sx, sy) in enumerate(product((-1, 1), repeat=2)):
                self._box(base + f"/{name}End{i}", (x+sx*.071, y+sy*.040, z+.005),
                          (.006, .020, .010))
                self._box(base + f"/{name}Side{i}", (x+sx*.063, y+sy*.051, z+.005),
                          (.022, .006, .010))
            for i, sign in enumerate((-1, 1)):
                self._box(base + f"/{name}Sidewall{i}", (x+sign*.138, y, .846),
                          (.0026, .214, .024))
                self._box(base + f"/{name}Endwall{i}", (x, y+sign*.105, .849),
                          (.276, .003, .030))

    def _workpiece_colliders(self, root):
        """Compound sole with four 8 mm pilot bores, plus boss and lug."""
        from pxr import UsdGeom

        base = root + "/Collision"
        UsdGeom.Xform.Define(self.stage, base)
        # The sole spans around the bore inserts. Trapezoid sectors retain
        # actual pin registration: a solid convex hull of the sole would close the holes.
        outer, pilot = .062, .045
        margin = .00455
        for i, (left, right) in enumerate(((-outer, -pilot-margin),
                                            (-pilot+margin, pilot-margin),
                                            (pilot+margin, outer))):
            self._box(base + f"/SoleSpan{i}", ((left+right)/2, 0., .006),
                      (right-left, .082, .012))
        for i, sx in enumerate((-1, 1)):
            for j, (front, rear) in enumerate(((-.041, -.026-margin), (-.026+margin, .026-margin),
                                               (.026+margin, .041))):
                self._box(base + f"/SoleBridge{i}_{j}", (sx*pilot, (front+rear)/2, .006),
                          (margin*2, rear-front, .012))
            for iy, sy in enumerate((-1, 1)):
                # Tangent boxes form a regular 16-sided circumscribed bore without
                # PhysX convex-hull cooking of very thin submillimetre mesh wedges.
                sides = 16
                radial_thickness = .0005
                inner_r = .004
                centre_r = inner_r+radial_thickness/2
                width = 2*(inner_r+radial_thickness)*tan(pi/sides)
                for k in range(sides):
                    angle = 2*pi*k/sides
                    center = (sx*pilot+centre_r*cos(angle), sy*.026+centre_r*sin(angle), .00325)
                    self._box(base + f"/Pilot_{i}_{iy}_{k}",center,
                              (radial_thickness,width,.0065),angle=angle)
        self._box(base+"/Eye", (-.013,.026,.035), (.048,.012,.046))
        self._box(base+"/Boss", (.017,-.009,.0225), (.0412,.0412,.021), cylinder=True)

    def prepare(self):
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

        self._station_supports()
        names = {"Infeed_bracket", "WB01_InspectionBracket", "Accepted_bracket"}
        originals = {p.GetName(): p for p in self.stage.Traverse() if p.GetName() in names}
        if set(originals) != names:
            raise ValueError(f"Missing original bracket assemblies: {names-set(originals)}")
        source = originals["Infeed_bracket"]
        UsdGeom.Xform.Define(self.stage, "/World/ActiveParts")
        for part, path in self.paths.items():
            root = UsdGeom.Xform.Define(self.stage, path)
            translate = root.AddTranslateOp()
            orient = root.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
            translate.Set(Gf.Vec3d(*PARK[part]))
            orient.Set(Gf.Quatd.GetIdentity())
            root.MakeInvisible()
            self.transforms[part] = (translate, orient)
            visual = UsdGeom.Xform.Define(self.stage, path + "/Visual")
            visual.GetPrim().GetReferences().AddInternalReference(source.GetPath())
            visual.MakeMatrixXform().Set(Gf.Matrix4d(1.))
            visual.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            # A subprim reference does not import material targets outside its subtree.
            for item in Usd.PrimRange(source):
                target = self.stage.GetPrimAtPath(item.GetPath().ReplacePrefix(source.GetPath(), visual.GetPath()))
                for rel in item.GetRelationships():
                    if rel.GetName().startswith("material:binding") and rel.GetTargets():
                        target.CreateRelationship(rel.GetName()).SetTargets(rel.GetTargets())
            self._workpiece_colliders(path)
            UsdPhysics.RigidBodyAPI.Apply(root.GetPrim()).CreateRigidBodyEnabledAttr(True)
            UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(.38)
            px = PhysxSchema.PhysxRigidBodyAPI.Apply(root.GetPrim())
            px.CreateDisableGravityAttr(True)
            px.CreateSolverPositionIterationCountAttr(12)
            px.CreateSolverVelocityIterationCountAttr(4)
        for original in originals.values():
            UsdGeom.Imageable(original).MakeInvisible()
        return self.paths

    def bind(self):
        from isaacsim.core.experimental.prims import RigidPrim

        self.body = RigidPrim(list(self.paths.values()), reset_xform_op_properties=False)
        if not self.body.is_physics_tensor_entity_valid() or set(self.body.paths) != set(self.paths.values()):
            raise RuntimeError("The workpieces did not bind to separate native PhysX bodies")
        self.indices = {part: self.body.paths.index(path) for part, path in self.paths.items()}

    def _idx(self, part):
        return [self.indices[part]]

    def _place(self, part, xyz, orientation=IDENTITY):
        idx = self._idx(part)
        self.body.set_world_poses(positions=[xyz], orientations=[orientation], indices=idx)
        self.body.set_velocities(linear_velocities=[ZERO], angular_velocities=[ZERO], indices=idx)

    def _visible(self, part, visible):
        from pxr import UsdGeom

        image = UsdGeom.Imageable(self.stage.GetPrimAtPath(self.paths[part]))
        image.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)

    def load(self, part, offset_mm=(0., 0.)):
        if self.states[part] != "parked":
            raise ValueError(f"Duplicate feed of {part}")
        x, y, z = REGIONS["infeed"][0]
        self._place(part, (x + offset_mm[0]/1000, y + offset_mm[1]/1000, z))
        self.body.set_enabled_gravities(True, indices=self._idx(part))
        self._visible(part, True)
        self.states[part] = "infeed"
        self.sync_visual(part)

    def pose(self, part):
        xyz, wxyz = self.body.get_world_poses(indices=self._idx(part))
        return tuple(map(float, xyz.numpy()[0])), tuple(map(float, wxyz.numpy()[0]))

    def sync_visual(self, part):
        """Synchronize world pose from the tensor backend for a default-time USD ray query."""
        from pxr import Gf

        xyz, wxyz = self.pose(part)
        translate, orient = self.transforms[part]
        translate.Set(Gf.Vec3d(*xyz))
        orient.Set(Gf.Quatd(wxyz[0], Gf.Vec3d(*wxyz[1:])).GetNormalized())
        return xyz, wxyz

    def acquire(self, part, tcp_xyz):
        if self.held is not None or self.states[part] not in ("infeed", "free"):
            raise ValueError(f"Cannot grasp {part} in {self.states[part]}; currently holding {self.held}")
        xyz, q = self.pose(part)
        grip = tuple(a+b for a, b in zip(xyz, rotate(q, GRIP)))
        error = dist(grip, tuple(tcp_xyz)[:3])
        if error > .003:
            raise ValueError(f"{part}: measured TCP misses the lug grasp by {1000*error:.2f} mm")
        self.offset = tuple(xyz[i]-tcp_xyz[i] for i in range(3))
        self.held_orientation = q
        self.held = part
        self.states[part] = "held"
        self.body.set_enabled_gravities(False, indices=self._idx(part))
        return {"part": part, "grasp": "virtual_rigid_translation", "grasp_error_m": error}

    def follow(self, part, tcp_xyz):
        if self.held != part:
            raise ValueError(f"Cannot transport ungrasped workpiece {part}")
        xyz = tuple(tcp_xyz[i]+self.offset[i] for i in range(3))
        self._place(part, xyz, self.held_orientation)

    def release(self, part):
        if self.held != part:
            raise ValueError(f"Cannot release {part} while holding {self.held}")
        self.held = self.held_orientation = self.offset = None
        self.states[part] = "free"
        self.body.set_enabled_gravities(True, indices=self._idx(part))
        return self.pose(part)

    def classify(self, part):
        xyz, q = self.pose(part)
        linear, angular = self.body.get_velocities(indices=self._idx(part))
        speed = hypot(*linear.numpy()[0])
        spin = hypot(*angular.numpy()[0])
        base = [tuple(a+b for a,b in zip(xyz, rotate(q, (x,y,0.))))
                for x, y in product((-.062, .062), (-.041, .041))]
        region, height_error = "outside", None
        for key, ((cx,cy,h), (hx,hy)) in REGIONS.items():
            if all(abs(x-cx) <= hx and abs(y-cy) <= hy for x,y,z in base):
                region = key
                height_error = max(abs(z-h) for x,y,z in base)
                break
        seated = (height_error is not None and height_error < .0015
                  and self.states[part] in ("free","infeed") and speed < .005 and spin < .10)
        return {"part": part, "region": region, "seated": bool(seated),
                "position_m": xyz, "orientation_wxyz": q, "corner_height_error_m": height_error,
                "linear_speed_m_s": speed, "angular_speed_rad_s": spin}

    def take_away(self, part):
        witness = self.classify(part)
        if not witness["seated"] or witness["region"] not in ("OK", "NG"):
            raise ValueError(f"Cannot service output before independent seating witness: {witness}")
        self.body.set_enabled_gravities(False, indices=self._idx(part))
        self._place(part, PARK[part])
        self._visible(part, False)
        self.states[part] = "removed"
        self.sync_visual(part)
        return {"part": part, "event": "external_tray_service", "witness_before_takeaway": witness}
