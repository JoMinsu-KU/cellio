"""Independent physical measurements. Inputs are serialized physics-backed states."""

from collections.abc import Mapping
from itertools import product
from math import dist, hypot, sqrt

from .temporal import all3


def get(obj, key, default=None):
    return (
        obj.get(key, default)
        if isinstance(obj, Mapping)
        else getattr(obj, key, default)
    )


def measured(truth, collection, key):
    state = get(truth, collection, {}).get(key)
    return state if state is not None and get(state, "valid") is True else None


def rotate(vec, quat):
    w, x, y, z = quat
    vx, vy, vz = vec
    a, b, c = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (
        vx + w * a + y * c - z * b,
        vy + w * b + z * a - x * c,
        vz + w * c + x * b - y * a,
    )


def corners(body, dimensions=(0.04, 0.04, 0.04)):
    if body is None:
        return None
    q, p = get(body, "orientation_wxyz"), get(body, "position_m")
    if q is None or p is None:
        return None
    return tuple(
        tuple(a + b for a, b in zip(p, rotate(offset, q)))
        for offset in product(*[(-length / 2, length / 2) for length in dimensions])
    )


def body_in_box(body, center, size, orientation=(1.0, 0.0, 0.0, 0.0)):
    vertices = corners(body)
    if vertices is None:
        return None
    if tuple(orientation) == (1.0, 0.0, 0.0, 0.0):
        return all(
            all(c - s / 2 <= v <= c + s / 2 for v, c, s in zip(vertex, center, size))
            for vertex in vertices
        )
    w, x, y, z = orientation
    for vertex in vertices:
        local = rotate(tuple(a - b for a, b in zip(vertex, center)), (w, -x, -y, -z))
        if not all(abs(value) <= extent / 2 for value, extent in zip(local, size)):
            return False
    return True


def body_speed(body, linear, angular):
    if body is None:
        return None
    lv, av = get(body, "linear_velocity_mps"), get(body, "angular_velocity_rps")
    return (
        None
        if lv is None or av is None
        else hypot(*lv) <= linear and hypot(*av) <= angular
    )


def joint(truth, name, lo, hi, vmax=None):
    data = measured(truth, "joints", name)
    if data is None:
        return None
    q, v = get(data, "position"), get(data, "velocity")
    if q is None or vmax is not None and v is None:
        return None
    return lo <= q <= hi and (vmax is None or abs(v) <= vmax)


FINGERS = ("panda_finger_joint1", "panda_finger_joint2")


def gripper_open(truth):
    return all3(joint(truth, name, 0.037, float("inf"), 0.003) for name in FINGERS)


def active_contact(truth, name, other="part_000"):
    pairs = get(truth, "contacts")
    if pairs is None:
        return None
    if any(tuple(pair) in ((name, other), (other, name)) for pair in pairs):
        return True
    return False if get(truth, "complete") else None


def grasp(truth):
    a, b = (measured(truth, "joints", n) for n in FINGERS)
    gap = (
        get(a, "position") + get(b, "position")
        if a is not None and b is not None
        else None
    )
    pos, tcp = (
        get(measured(truth, "bodies", n), "position_m")
        for n in ("part_000", "cell_tcp")
    )
    return all3(
        (
            active_contact(truth, "finger_left"),
            active_contact(truth, "finger_right"),
            None if gap is None else 0.034 <= gap <= 0.052,
            None if pos is None or tcp is None else dist(pos, tcp) <= 0.018,
        )
    )


def clear_fixture(truth):
    # World AABBs of the composed collision meshes can be recomputed by the adapter
    # and persisted as scalar coordinates in immutable plant_state.
    state = get(truth, "plant_state", {})
    bounds = []
    for part in ("hand", "finger_left", "finger_right"):
        lo = [state.get(f"{part}.bounds.min_{axis}") for axis in "xyz"]
        hi = [state.get(f"{part}.bounds.max_{axis}") for axis in "xyz"]
        if None in (*lo, *hi):
            bounds.append(None)
            continue
        bounds.append(
            any(
                high < a or low > b
                for low, high, a, b in zip(
                    lo, hi, (0.39, 0.02, 0.79), (0.66, 0.22, 1.00)
                )
            )
        )
    return all3(bounds)


def fixture_loaded(truth):
    part = measured(truth, "bodies", "part_000")
    if part is None:
        return None
    x, y, z = get(part, "position_m")
    vertices = corners(part)
    return (
        0.501 <= x <= 0.513
        and 0.114 <= y <= 0.126
        and 0.804 <= z <= 0.816
        and hypot(*get(part, "linear_velocity_mps")) <= 0.01
        and all(0.481 <= v[0] <= 0.550 for v in vertices)
    )


def fixture_clamped(truth):
    return all3(
        (
            body_in_box(
                measured(truth, "bodies", "part_000"),
                (0.50, 0.12, 0.81),
                (0.046, 0.056, 0.048),
            ),
            joint(truth, "clamp", -float("inf"), -0.058),
            active_contact(truth, "fixed_jaw"),
            active_contact(truth, "moving_jaw"),
        )
    )


def goal_now(truth, center):
    part = measured(truth, "bodies", "part_000")
    hand = measured(truth, "bodies", "hand")
    if part is None:
        return None
    x, y, z = get(part, "position_m")
    cx, cy = center
    # Compare in world coordinates at the circular boundary. This keeps exactly
    # representable axis-aligned inclusive endpoints without expanding tolerance.
    dx = abs(x - cx)
    if y == cy:
        inside = cx - 0.015 <= x <= cx + 0.015
    else:
        reach = sqrt(max(0.0, 0.015**2 - dx**2))
        inside = dx <= 0.015 and cy - reach <= y <= cy + reach
    return all3(
        (
            body_in_box(part, (cx, cy, 0.815), (0.10, 0.10, 0.060)),
            inside,
            0.804 <= z <= 0.816,
            body_speed(part, 0.01, 0.10),
            gripper_open(truth),
            None if hand is None else get(hand, "position_m")[2] >= z + 0.08,
        )
    )


def quiet(truth, belt_present, clamp_present):
    values = []
    for n in (
        *("panda_joint" + str(i) for i in range(1, 8)),
        *FINGERS,
        *(("clamp",) if clamp_present else ()),
    ):
        j = measured(truth, "joints", n)
        v = get(j, "velocity")
        values.append(
            None
            if v is None
            else abs(v) <= (0.02 if n.startswith("panda_joint") else 0.003)
        )
    if belt_present:
        speed = get(truth, "plant_state", {}).get("belt.surface_speed_mps")
        values.append(None if speed is None else abs(speed) <= 0.001)
    return all3(values)
