"""Rigid transforms in metres, world position followed by wxyz quaternion."""

from itertools import product
from math import acos, dist, sqrt


def multiply(q, r):
    w, x, y, z = q
    a, b, c, d = r
    return (w*a-x*b-y*c-z*d, w*b+x*a+y*d-z*c, w*c-x*d+y*a+z*b, w*d+x*c-y*b+z*a)


def rotate(q, p):
    w, x, y, z = q
    return multiply(multiply(q, (0., *p)), (w, -x, -y, -z))[1:]


def compose(a, b):
    offset = rotate(a[3:], b[:3])
    return (*(a[i] + offset[i] for i in range(3)), *multiply(a[3:], b[3:]))


def inverse(pose):
    w, x, y, z = pose[3:]
    q = (w, -x, -y, -z)
    return (*rotate(q, tuple(-a for a in pose[:3])), *q)


def quat_distance(a, b):
    dot = abs(sum(x*y for x, y in zip(a, b, strict=True)))
    return 2 * acos(min(1., dot))


def interpolate_target(start, goal, speed, dt):
    """Translate at the target cap; use shortest-arc normalized quaternion interpolation."""
    distance = dist(start[:3], goal[:3])
    f = min(1., speed * dt / distance) if distance else 1.
    sign = 1 if sum(a*b for a, b in zip(start[3:], goal[3:])) >= 0. else -1
    q = tuple((1-f)*a + f*sign*b for a, b in zip(start[3:], goal[3:]))
    norm = sqrt(sum(v*v for v in q))
    return (*(a + f*(b-a) for a, b in zip(start[:3], goal[:3])), *(v/norm for v in q))


def corners(low, high):
    return tuple(product(*zip(low, high, strict=True)))


def world_aabb(pose, local_corners):
    world = [tuple(a+b for a, b in zip(pose[:3], rotate(pose[3:], p))) for p in local_corners]
    return tuple(map(min, zip(*world))), tuple(map(max, zip(*world)))
