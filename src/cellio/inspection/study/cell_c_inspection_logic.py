"""Geometry-driven inspection and signal provenance for the Cell C study.

The comparator samples a vertical line through the actual USD workpiece. It
uses the fixture rests as its reference. The independent dimensional oracle
uses the extrema of the faced boss and foot in the moving part's frame.
Distances authored to the stage are metres; reported dimensions are millimetres.
"""

from dataclasses import dataclass
from functools import lru_cache
import math
import re

import numpy as np
from pxr import Gf, Usd, UsdGeom, Vt
from cellio.inspection.decision import update_latch


NOMINAL_MM = 33.0
TOLERANCE_MM = 0.20
HIGH_VARIANT_MM = 0.60
EVALUATION_EPS_MM = 0.0001  # USD mesh vertices are single precision
OPEN_LIFT_M = 0.110


class InspectionError(ValueError):
    """The part or fixture did not satisfy the prerequisites for inspection."""


@dataclass(frozen=True)
class ProbeReading:
    height_mm: float
    error_mm: float
    within_tolerance: bool
    contact_world_m: tuple
    seat_errors_mm: tuple
    tilt_deg: float
    locator_error_mm: float
    hit_mesh: str


@dataclass(frozen=True)
class Decision:
    part_id: str
    result_part_id: str
    route: str
    fresh: bool


class InspectionController:
    """Latch the result and its material identifier across successive cycles."""

    def __init__(self, stale_ok=False):
        self.stale_ok = stale_ok
        self._result = None
        self._seen = set()

    def inspect(self, part_id, reading):
        if part_id in self._seen:
            raise InspectionError(f"Duplicate inspection cycle for {part_id}")
        self._seen.add(part_id)
        # The fault suppresses the write after an OK result, including its tag.
        self._result = update_latch(self._result, part_id, reading.within_tolerance, self.stale_ok)
        origin, route = self._result
        return Decision(part_id, origin, route, origin == part_id)


def _at(time):
    if time is None:
        return Usd.TimeCode.Default()
    return time if isinstance(time, Usd.TimeCode) else Usd.TimeCode(time)


def _equipment(stage):
    for path in ("/World/Equipment", "/Equipment"):
        prim = stage.GetPrimAtPath(path)
        if prim and prim.GetChild("RubyContactBall"):
            return prim
    raise InspectionError("Cannot find the inspection tooling in the composed equipment")


def _meshes(root):
    return [UsdGeom.Mesh(p) for p in Usd.PrimRange(root) if p.IsA(UsdGeom.Mesh)]


def _feature(root, name):
    meshes = [m for m in _meshes(root) if name in m.GetPrim().GetName()]
    if len(meshes) != 1:
        raise InspectionError(f"Expected one {name} mesh below {root.GetPath()}, found {len(meshes)}")
    return meshes[0]


def _part(stage, path):
    root = stage.GetPrimAtPath(str(path))
    if not root:
        raise InspectionError(f"Missing part at {path}")
    return root


def _points(mesh, matrix, time):
    points = np.asarray(mesh.GetPointsAttr().Get(time), dtype=np.float64)
    affine = np.asarray(matrix)
    return points @ affine[:3, :3] + affine[3, :3]


def set_boss_error(stage, part_path, error_mm):
    """Machine a different boss height while preserving the flange, bore and chamfer.

    Both upper profile rings move together. Target heights are absolute, so
    applying a recipe twice leaves exactly the same geometry.
    """
    if not math.isfinite(error_mm) or abs(error_mm) > 2.0:
        raise InspectionError("Supported machining variation is ±2 mm")
    mesh = _feature(_part(stage, part_path), "FacedDatumBoss")
    points = np.array(mesh.GetPointsAttr().Get(), dtype=np.float64)
    top = points[:, 2].max()
    upper = points[:, 2] >= top - 0.00061
    points[upper, 2] += (NOMINAL_MM + error_mm) / 1000 - top
    authored = Vt.Vec3fArray.FromNumpy(points.astype(np.float32))
    if not mesh.GetPointsAttr().Set(authored):
        raise InspectionError(f"Failed to author {mesh.GetPath()}")
    mesh.CreateExtentAttr().Set(UsdGeom.PointBased.ComputeExtent(authored))


def _within(error_mm):
    return abs(error_mm) <= TOLERANCE_MM + EVALUATION_EPS_MM


def part_truth(stage, part_path, time=None):
    """Independent of the fixture, probe, input recipe, decision and part orientation."""
    at = _at(time)
    root = _part(stage, part_path)
    cache = UsdGeom.XformCache(at)
    inverse = cache.GetLocalToWorldTransform(root).GetInverse()

    def relative_surface(feature):
        mesh = _feature(root, feature)
        return _points(mesh, cache.GetLocalToWorldTransform(mesh.GetPrim()) * inverse, at)[:, 2]

    height = float((relative_surface("FacedDatumBoss").max() - relative_surface("_Foot").min()) * 1000)
    return {"height_mm": height, "error_mm": height - NOMINAL_MM,
            "within_tolerance": _within(height - NOMINAL_MM)}


def _ray_hits(mesh, cache, at, xy):
    """Intersect a world-vertical ray with triangle faces from the USD mesh."""
    points = _points(mesh, cache.GetLocalToWorldTransform(mesh.GetPrim()), at)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(at), dtype=int)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(at), dtype=int)
    starts = np.cumsum(counts) - counts
    hits = []
    # The bracket foot was triangulated at export; turned surfaces use convex quads.
    for corner in range(1, int(max(counts)) - 1):
        active = starts[counts > corner + 1]
        a, b, c = (points[indices[active + offset]] for offset in (0, corner, corner + 1))
        ab, ac, delta = b - a, c - a, xy - a[:, :2]
        cross = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
        valid = abs(cross) > 1e-15
        den = np.where(valid, cross, 1)
        u = (delta[:, 0] * ac[:, 1] - delta[:, 1] * ac[:, 0]) / den
        v = (ab[:, 0] * delta[:, 1] - ab[:, 1] * delta[:, 0]) / den
        valid &= (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7)
        hits.extend((a[:, 2] + u * ab[:, 2] + v * ac[:, 2])[valid])
    return hits


def measure_part(stage, part_path, time=None):
    """Sample the topmost workpiece surface at the gauge and check seating.

    Three underside-to-rest gaps, two hole-to-pin registration errors, and
    tilt guard against treating a displaced part as a dimensional defect.
    """
    at = _at(time)
    cache = UsdGeom.XformCache(at)
    root = _part(stage, part_path)
    equipment = _equipment(stage)
    ball = equipment.GetChild("RubyContactBall")
    probe_xy = np.asarray(cache.GetLocalToWorldTransform(ball).ExtractTranslation())[:2]
    transform = cache.GetLocalToWorldTransform(root)
    normal = np.asarray(transform.TransformDir(Gf.Vec3d(0, 0, 1)))
    tilt = math.degrees(math.acos(float(np.clip(normal[2] / np.linalg.norm(normal), -1, 1))))
    if tilt > 0.15:
        raise InspectionError(f"Part tilt {tilt:.4f}° exceeds the seating limit 0.15°")

    foot = _feature(root, "_Foot")
    rests = sorted((p for p in equipment.GetChildren() if p.GetName().startswith("DatumRest")), key=str)
    pins = sorted((p for p in equipment.GetChildren() if p.GetName().startswith("ToolingLocator")), key=str)
    if len(rests) != 3 or len(pins) != 2:
        raise InspectionError("Fixture requires three datum rests and two locating pins")
    heights, gaps = [], []
    for rest in rests:
        rest_mesh = _meshes(rest)[0]
        rest_points = _points(rest_mesh, cache.GetLocalToWorldTransform(rest_mesh.GetPrim()), at)
        top = rest_points[:, 2].max()
        centre = np.mean(rest_points[np.isclose(rest_points[:, 2], top, rtol=0, atol=1e-7), :2], axis=0)
        hits = _ray_hits(foot, cache, at, centre)
        if not hits:
            raise InspectionError(f"Foot does not overlap support {rest.GetName()}")
        heights.append(top)
        gaps.append(float((min(hits) - top) * 1000))
    if max(abs(gap) for gap in gaps) > 0.15:
        raise InspectionError(f"Underside-to-rest gaps outside ±0.15 mm: {gaps}")
    holes = np.array([transform.Transform(Gf.Vec3d(x, .026, 0)) for x in (-.045, .045)])[:, :2]
    pin_xy = np.array([cache.GetLocalToWorldTransform(p).ExtractTranslation() for p in pins])[:, :2]
    errors = np.linalg.norm(pin_xy[:, None, :] - holes[None, :, :], axis=2)
    locator_error = float(max(np.min(errors, axis=0)) * 1000)
    if locator_error > .35:
        raise InspectionError(f"Locating holes are {locator_error:.4f} mm off the pins (limit 0.35 mm)")
    surfaces = []
    for mesh in _meshes(root):
        # Ignore any invisible collision proxies added to the active workpiece.
        if any(tag in mesh.GetPrim().GetName() for tag in ("_Foot", "FacedDatumBoss", "CrossDrilledEar", "_Web")):
            hits = _ray_hits(mesh, cache, at, probe_xy)
            if hits:
                surfaces.append((max(hits), str(mesh.GetPath())))
    if not surfaces:
        raise InspectionError("No workpiece under probe; check the parked carriage and part pose")
    hit_z, hit_path = max(surfaces)
    if "FacedDatumBoss" not in hit_path:
        raise InspectionError(f"Probe missed the machined boss and hit {hit_path}")
    height_mm = float((hit_z - np.mean(heights)) * 1000)
    error = height_mm - NOMINAL_MM
    return ProbeReading(height_mm, error, _within(error), (*map(float, probe_xy), float(hit_z)),
                        tuple(gaps), tilt, locator_error, hit_path)


def _pivot_motion(pivot, angle, rise):
    origin = Gf.Vec3d(*pivot)
    return (Gf.Matrix4d(1).SetTranslate(-origin)
            * Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), angle))
            * Gf.Matrix4d(1).SetTranslate(origin + Gf.Vec3d(0, 0, rise)))


def _motion_op(prim):
    xform = UsdGeom.Xformable(prim)
    name = "xformOp:transform:cellCActuator"
    ops = xform.GetOrderedXformOps()
    for op in ops:
        if op.GetOpName() == name:
            return op
    op = xform.AddTransformOp(opSuffix="cellCActuator")
    xform.SetXformOpOrder([op] + ops)
    return op


@lru_cache(maxsize=6)
def _actuators(stage):
    equipment = _equipment(stage)
    head, slide, probe, toes = [], [], [], []
    pointer = None
    head_names = {"CantileverSpine", "QuillLock", "GaugeStem_Upper", "GaugeStem_Collet",
                  "ComparatorCase", "ScaleBezel", "GaugeScale", "Engraved100Divisions",
                  "PointerHub", "GaugeLiftPlunger", "GaugeLiftThumb", "GaugeConnector"}
    slide_names = {"SlidingCarriage", "CarriageCap", "CrossLockHub", "CrossLockKnob", "KnobKnurl"}
    for child in equipment.GetChildren():
        original_name = child.GetName()
        name = re.sub(r"_\d{3}$", "", original_name)
        if name in head_names or name.startswith("Scale_"):
            head.append(_motion_op(child))
        elif name in slide_names:
            slide.append(_motion_op(child))
        elif name in {"StylusStem", "RubyContactBall"}:
            probe.append(_motion_op(child))
        elif name == "ComparatorPointer":
            head.append(_motion_op(child))
            xform = UsdGeom.Xformable(child)
            pointer = next((op for op in xform.GetOrderedXformOps()
                            if op.GetOpName() == "xformOp:rotateY:cellCReading"), None)
            if pointer is None:
                pointer = xform.AddRotateYOp(opSuffix="cellCReading")
        elif name in {"HoldDownToe", "FlatSwivelContact"}:
            xform = UsdGeom.Xformable(child)
            base = next(op.Get() for op in xform.GetOrderedXformOps()
                        if op.GetOpName() == "xformOp:translate")
            left = base[0] < .077
            heel = (-.006, -.002, 0) if left else (.160, .009, 0)
            toes.append((_motion_op(child), heel, 1 if left else -1))
    if not head or not slide or len(probe) != 2 or len(toes) != 4 or pointer is None:
        raise InspectionError("Original tool geometry was renamed or is incomplete")
    cable = _meshes(equipment.GetChild("SignalCable"))[0]
    # Read the unactuated mesh from its referenced source. This remains correct
    # when the animated stage is saved, reopened and edited a second time.
    points_attr = cable.GetPointsAttr()
    specs = points_attr.GetPropertyStack(Usd.TimeCode.Default())
    source = next((spec.default for spec in reversed(specs) if spec.HasInfo("default")), None)
    if source is None:
        raise InspectionError("Cannot find reference cable points")
    neutral = np.asarray(source, dtype=float)
    weight = np.clip((.188 - neutral[:, 1]) / .155, 0, 1)
    weight = (weight * weight * (3 - 2 * weight))[:, None]
    return head, slide, probe, toes, pointer, cable, neutral, weight


def set_tooling(stage, lift_m, clamp_open, time=None, reading=None):
    """Position the gauge slide, parking swivel, clamps and spring-loaded tip.

    At full lift the comparator is out of the loading corridor. On the first
    20 mm of vertical lift the probe leaves the part before the head swivels.
    The clamp toes initially lift from the workpiece and then swing 90 degrees
    around the heels. The operator dial is zeroed at the reference height.
    """
    lift = float(lift_m)
    opening = float(clamp_open)
    if not (0 <= lift <= OPEN_LIFT_M + 1e-9 and 0 <= opening <= 1):
        raise InspectionError(f"Unsupported tool command: lift={lift}, clamp_open={opening}")
    if reading is not None and (lift > 1e-9 or opening > 1e-9):
        raise InspectionError("Gauge reading requires closed clamps and lowered head")
    at = _at(time)
    head, slide, probe, toes, pointer, cable, neutral, weights = _actuators(stage)
    swing = 75 * max(0, lift - .020) / (OPEN_LIFT_M - .020)
    pivot = (.107, .139, 0)
    carriage = Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0, 0, lift))
    boom = _pivot_motion(pivot, swing, lift)
    error_mm = reading.error_mm if reading is not None else 0.0
    stylus = _pivot_motion(pivot, swing, lift + error_mm / 1000)
    for op in head:
        op.Set(boom, at)
    for op in slide:
        op.Set(carriage, at)
    for op in probe:
        op.Set(stylus, at)
    for op, heel, sign in toes:
        release = min(opening / .15, 1) * .004
        rotation = max(opening - .15, 0) / .85 * 90 * sign
        op.Set(_pivot_motion(heel, rotation, release), at)
    # Original mesh pointer was authored 0.36 radians counterclockwise.
    pointer.Set(math.degrees(.36) + error_mm * 360, at)
    mat = np.asarray(boom)
    moved = neutral @ mat[:3, :3] + mat[3, :3]
    points = Vt.Vec3fArray.FromNumpy((neutral + weights * (moved - neutral)).astype(np.float32))
    cable.GetPointsAttr().Set(points, at)
    cable.CreateExtentAttr().Set(UsdGeom.PointBased.ComputeExtent(points), at)
