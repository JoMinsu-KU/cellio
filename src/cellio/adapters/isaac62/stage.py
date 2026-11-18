"""Build CellIO stages after SimulationApp starts. Lengths and masses are SI."""

from itertools import product
from cellio.scenes.definition import ARM_NAMES, FINGER_NAMES, TABLE, Box
from cellio.core.models import CellIOError

BASE = "/World/Cell"
ROBOT = BASE + "/Robot"
PART = BASE + "/Parts/Part_000"
# Both 20 N baseline scenes showed persistent joint oscillation during a
# two-pad grasp of the 50 g part. Freeze the final value after commissioning.
FINGER_FORCE_LIMIT_N = 4.


def _vec(Gf, xyz):
    return Gf.Vec3d(*xyz)


class StageBuilder:
    def __init__(self, stage, scene, robot_asset, profile, *, robot_base=(0., 0., .750)):
        from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
        self.stage, self.scene, self.profile = stage, scene, profile
        self.Gf, self.Physx, self.Sdf, self.Usd = Gf, PhysxSchema, Sdf, Usd
        self.Geom, self.Physics, self.Shade = UsdGeom, UsdPhysics, UsdShade
        self.paths = {"part_000": PART}
        self.collider_ids = {}
        self.local_corners = {}
        self.pad_corners = {}
        self.pad_paths = {}
        self.material_readbacks = {}
        self.materials = {}
        self.finger_drivers = []
        self.finger_drive_settings = {}
        self.solver_settings = {}
        self.belt = None
        self.clamp = None
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        UsdGeom.Xform.Define(stage, BASE)
        self._material("PartContact", .8, .6)
        self._material("FingerPad", 1., .8)
        ground = UsdGeom.Plane.Define(stage, "/World/Ground")
        ground.CreateAxisAttr("Z")
        UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
        self._box(BASE + "/Table", Box("Table", *TABLE))
        for box in scene.boxes:
            self._box(BASE + "/" + box.name, box)
        for zone in scene.zones:
            visual = self._shape(BASE + "/Zones/" + zone.name, zone.center, zone.size)
            UsdGeom.Imageable(visual).MakeInvisible()
        self._box(PART, Box("Part_000", scene.part_center, (.04, .04, .04), "dynamic", .05))
        base = UsdGeom.Xform.Define(stage, ROBOT)
        base.AddTranslateOp().Set(Gf.Vec3d(*robot_base))
        if not base.GetPrim().GetReferences().AddReference(str(robot_asset)):
            raise CellIOError("ASSET_REFERENCE_FAILED", str(robot_asset))
        stage.Load()
        self._bind_robot()

    def _material(self, name, static, dynamic):
        material = self.Shade.Material.Define(self.stage, BASE + "/Materials/" + name)
        physics = self.Physics.MaterialAPI.Apply(material.GetPrim())
        physics.CreateStaticFrictionAttr(static)
        physics.CreateDynamicFrictionAttr(dynamic)
        physics.CreateRestitutionAttr(0.)
        px = self.Physx.PhysxMaterialAPI.Apply(material.GetPrim())
        px.CreateFrictionCombineModeAttr("average")
        px.CreateRestitutionCombineModeAttr("average")
        self.materials[name] = material

    def _shape(self, path, center, size):
        cube = self.Geom.Cube.Define(self.stage, path)
        cube.CreateSizeAttr(1.)
        cube.AddTranslateOp().Set(_vec(self.Gf, center))
        cube.AddScaleOp().Set(self.Gf.Vec3f(*size))
        return cube.GetPrim()

    def _bind_material(self, prim, material):
        api = self.Shade.MaterialBindingAPI.Apply(prim)
        api.Bind(self.materials[material], self.Shade.Tokens.strongerThanDescendants, "physics")
        bound, _ = api.ComputeBoundMaterial("physics")
        if bound.GetPath() != self.materials[material].GetPath():
            raise CellIOError("ASSET_MATERIAL_MISMATCH", str(prim.GetPath()))
        collider = self.Physx.PhysxCollisionAPI.Apply(prim)
        collider.CreateContactOffsetAttr(.001)
        collider.CreateRestOffsetAttr(0.)
        material_api = self.Physics.MaterialAPI(bound.GetPrim())
        self.material_readbacks[str(prim.GetPath())] = {
            "material": str(bound.GetPath()), "static_friction": float(material_api.GetStaticFrictionAttr().Get()),
            "dynamic_friction": float(material_api.GetDynamicFrictionAttr().Get()),
            "restitution": float(material_api.GetRestitutionAttr().Get()),
            "contact_offset": float(collider.GetContactOffsetAttr().Get()),
            "rest_offset": float(collider.GetRestOffsetAttr().Get())}

    def _box(self, path, box):
        P, X, G = self.Physics, self.Physx, self.Geom
        body = G.Xform.Define(self.stage, path)
        body.AddTranslateOp().Set(_vec(self.Gf, box.center))
        collider = self._shape(path + "/Geometry", (0., 0., 0.), box.size)
        P.CollisionAPI.Apply(collider)
        self._bind_material(collider, "PartContact")
        if box.kind not in {"static", "fixed_jaw"}:
            rb = P.RigidBodyAPI.Apply(body.GetPrim())
            if box.kind == "belt":
                rb.CreateKinematicEnabledAttr(True)
                self.belt = X.PhysxSurfaceVelocityAPI.Apply(body.GetPrim())
                self.belt.CreateSurfaceVelocityLocalSpaceAttr(False)
                self.belt.CreateSurfaceVelocityAttr(self.Gf.Vec3f(0))
            else:
                P.MassAPI.Apply(body.GetPrim()).CreateMassAttr(box.mass)
                X.PhysxRigidBodyAPI.Apply(body.GetPrim()).CreateSleepThresholdAttr(0.)
        entity = "part_000" if path == PART else box.name
        if box.kind in {"fixed_jaw", "moving_jaw"}:
            entity = box.kind
        if box.kind == "moving_jaw":
            self.paths[entity] = path
            joint = P.PrismaticJoint.Define(self.stage, BASE + "/Fixture/ClampJoint")
            joint.CreateBody1Rel().SetTargets([self.Sdf.Path(path)])
            joint.CreateAxisAttr("X")
            joint.CreateLocalPos0Attr(self.Gf.Vec3f(*box.center))
            joint.CreateLocalPos1Attr(self.Gf.Vec3f(0.))
            joint.CreateLocalRot0Attr(self.Gf.Quatf(1.))
            joint.CreateLocalRot1Attr(self.Gf.Quatf(1.))
            joint.CreateLowerLimitAttr(-.070)
            joint.CreateUpperLimitAttr(0.)
            self.clamp = P.DriveAPI.Apply(joint.GetPrim(), "linear")
            self.clamp.CreateTypeAttr("force")
            self.clamp.CreateMaxForceAttr(30.)
            self.clamp.CreateStiffnessAttr(4000.)
            self.clamp.CreateDampingAttr(100.)
            self.clamp.CreateTargetPositionAttr(0.)
            self.clamp.CreateTargetVelocityAttr(0.)
        self.collider_ids[str(collider.GetPath())] = entity
        if path == PART:
            X.PhysxContactReportAPI.Apply(body.GetPrim()).CreateThresholdAttr(0.)

    def _bind_robot(self):
        G, P, U = self.Geom, self.Physics, self.Usd
        robot = self.stage.GetPrimAtPath(ROBOT)
        # Known vendor assets instance their collision meshes. Local overrides need real prims.
        for prim in U.PrimRange(robot):
            if prim.IsInstance():
                prim.SetInstanceable(False)
        descendants = tuple(U.PrimRange(robot))
        roots = [p for p in descendants if p.HasAPI(P.ArticulationRootAPI)]
        if len(roots) != 1:
            raise CellIOError("ASSET_BINDING_MISMATCH", f"Expected one Franka articulation, found {len(roots)}")
        settings = self.Physx.PhysxArticulationAPI.Apply(roots[0])
        velocity_iterations = self.profile.get("articulation_velocity_iterations", 1)
        if type(velocity_iterations) is not int or not 1 <= velocity_iterations <= 255:
            raise CellIOError("ASSET_PHYSICS_PROFILE", "Invalid articulation velocity iteration count")
        settings.CreateSolverVelocityIterationCountAttr(velocity_iterations)
        self.solver_settings = {
            "position_iterations": settings.GetSolverPositionIterationCountAttr().Get(),
            "velocity_iterations": settings.GetSolverVelocityIterationCountAttr().Get()}
        name_map = {}
        for prim in descendants:
            name_map.setdefault(prim.GetName(), []).append(prim)
        def unique(name, test):
            matches = [p for p in name_map.get(name, ()) if test(p)]
            if len(matches) != 1:
                raise CellIOError("ASSET_BINDING_MISMATCH", f"Expected one {name}, found {len(matches)}")
            return matches[0]
        for joint_name in ARM_NAMES + FINGER_NAMES:
            joint = unique(joint_name, lambda p: p.IsA(P.Joint))
            if joint_name in FINGER_NAMES:
                if not any("MimicJointAPI" in s for s in joint.GetAppliedSchemas()):
                    drive = P.DriveAPI.Apply(joint, "linear")
                    drive.CreateTypeAttr("force")
                    drive.CreateStiffnessAttr(4000.)
                    drive.CreateDampingAttr(80.)
                    cap = self.profile.get("finger_force_limit_n", FINGER_FORCE_LIMIT_N)
                    if type(cap) not in (int, float) or not 0 < cap <= 20.:
                        raise CellIOError("ASSET_PHYSICS_PROFILE", "Finger force cap must be within (0,20] N")
                    drive.CreateMaxForceAttr(float(cap))
                    self.finger_drivers.append(joint_name)
                    self.finger_drive_settings[joint_name] = {
                        "force_cap_n": float(drive.GetMaxForceAttr().Get()),
                        "stiffness_n_per_m": float(drive.GetStiffnessAttr().Get()),
                        "damping_n_s_per_m": float(drive.GetDampingAttr().Get())}
        if not self.finger_drivers:
            raise CellIOError("ASSET_BINDING_MISMATCH", "No independently driven finger joint")
        for entity, name in (("hand", "panda_hand"), ("finger_left", "panda_leftfinger"),
                             ("finger_right", "panda_rightfinger")):
            link = unique(name, lambda p: p.HasAPI(P.RigidBodyAPI))
            self.paths[entity] = str(link.GetPath())
            colliders = [p for p in U.PrimRange(link) if p.HasAPI(P.CollisionAPI) and
                         P.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False]
            if not colliders:
                raise CellIOError("ASSET_BINDING_MISMATCH", f"No collision geometry for {name}")
            if entity.startswith("finger_"):
                selected = self.profile.get("pad_collider_paths", {}).get(entity, [])
                pads = [p for p in colliders if str(p.GetPath()) in selected] if selected else [
                    p for p in colliders if "pad" in str(p.GetPath()).lower()]
                if not selected and not pads and len(colliders) == 1:
                    pads = colliders
                if not pads or (selected and len(pads) != len(selected)):
                    raise CellIOError("ASSET_PAD_BINDING", f"Specify actual pad_collider_paths for {name}")
                for pad in pads:
                    self._bind_material(pad, "FingerPad")
                self.pad_paths[entity] = [str(pad.GetPath()) for pad in pads]
                self.pad_corners[entity] = self._geometry(link, pads)
            self.local_corners[entity] = self._geometry(link, colliders)
            for collider in colliders:
                self.collider_ids[str(collider.GetPath())] = entity
        tcp = G.Xform.Define(self.stage, self.paths["hand"] + "/CellTCP")
        local = self.profile.get("hand_T_tcp", (0., 0., .1034, 1., 0., 0., 0.))
        tcp.AddTranslateOp().Set(_vec(self.Gf, local[:3]))
        tcp.AddOrientOp(precision=G.XformOp.PrecisionDouble).Set(self.Gf.Quatd(local[3], *local[4:]))

    def _geometry(self, link, colliders):
        cache = self.Geom.BBoxCache(self.Usd.TimeCode.Default(), ["default", "render", "proxy", "guide"])
        points = []
        for prim in colliders:
            bounds = cache.ComputeRelativeBound(prim, link)
            local_range = bounds.GetRange()
            if local_range.IsEmpty():
                raise CellIOError("ASSET_COLLIDER_BOUNDS", str(prim.GetPath()))
            for p in product(*zip(local_range.GetMin(), local_range.GetMax())):
                points.append(tuple(bounds.GetMatrix().Transform(self.Gf.Vec3d(*p))))
        return tuple(points)
