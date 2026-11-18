"""Cross-check finger velocity measurements during free travel, grasp and induced slip."""

import argparse
from math import dist
from pathlib import Path
from statistics import mean

from cellio.core.models import CommandKind
from cellio.io import write_json
from cellio.scenes.definition import FINGER_NAMES
from .application import standalone_main
from .geometry import rotate
from .grasp_check import FixtureCheck
from .plant import DT, IsaacPlant, _cross


def _minus(a, b):
    return tuple(x-y for x, y in zip(a, b, strict=True))


class FingerCheck(FixtureCheck):
    def prepare(self):
        self.plant.load({"scene_id": "cell_b"})
        self.plant.reset({}, self.generation)
        builder = self.plant.builder
        hand = builder.paths["hand"]
        bodies = {path: entity for entity, path in builder.paths.items()}
        self.axes = {}
        for prim in builder.Usd.PrimRange(builder.stage.GetPrimAtPath("/World/Cell/Robot")):
            name = prim.GetName()
            if name not in FINGER_NAMES or not prim.IsA(builder.Physics.PrismaticJoint):
                continue
            joint = builder.Physics.PrismaticJoint(prim)
            path0, path1 = (str(rel.GetTargets()[0]) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel()))
            if hand != path0 or path1 not in bodies:
                raise ValueError(f"Unexpected finger joint bodies: {path0}, {path1}")
            quat = joint.GetLocalRot0Attr().Get()
            axis = tuple(float(joint.GetAxisAttr().Get() == a) for a in 'XYZ')
            self.axes[name] = bodies[path1], rotate((quat.GetReal(), *quat.GetImaginary()), axis)
        if set(self.axes) != set(FINGER_NAMES):
            raise ValueError("Failed to resolve both prismatic finger axes")
        relative, _ = self.plant.views['bodies'].get_coms()
        self.local_com = dict(zip(self.plant.body_order, relative.numpy().tolist(), strict=True))
        self.report['axes_in_hand'] = self.axes
        self.report['com_in_body_m'] = self.local_com
        self.report['samples'] = []
        self.previous = self.snapshot()

    def origin_velocity(self, truth, body_id):
        body = truth.bodies[body_id]
        offset = rotate(body.orientation_wxyz, self.local_com[body_id])
        return _minus(body.linear_velocity_mps, _cross(body.angular_velocity_rps, offset))

    def sample(self, truth, phase):
        hand = truth.bodies['hand']
        rv = self.origin_velocity(truth, 'hand')
        entry = {'phase': phase, 'step': truth.tick.step, 'bilateral': self.contacts(truth),
                 'part_m': truth.bodies['part_000'].position_m, 'tcp_m': truth.bodies['cell_tcp'].position_m,
                 'part_v_mps': truth.bodies['part_000'].linear_velocity_mps, 'fingers': {}}
        for joint_id, (body_id, axis) in self.axes.items():
            reach = _minus(truth.bodies[body_id].position_m, hand.position_m)
            relative = _minus(_minus(self.origin_velocity(truth, body_id), rv), _cross(hand.angular_velocity_rps, reach))
            world_axis = rotate(hand.orientation_wxyz, axis)
            joint = truth.joints[joint_id]
            entry['fingers'][joint_id] = {
                'q_m': joint.position, 'qdot_mps': joint.velocity,
                'encoder_delta_mps': (joint.position-self.previous.joints[joint_id].position)/DT,
                'link_twist_mps': sum(a*b for a, b in zip(world_axis, relative, strict=True))}
        self.report['samples'].append(entry)
        self.previous = truth

    def record(self, phase, n, command=None):
        while self.plant.step % 4:
            self.advance()
        self.previous = self.snapshot()
        for index in range(n):
            truth = self.advance(*command) if command and index == 0 else self.advance()
            self.sample(truth, phase)

    def run_diagnostic(self):
        self.prepare()
        self.pose('park')
        gripper = 'actuator.gripper', CommandKind.GRIPPER_GAP
        self.record('free_close', 240, (*gripper, 0.))
        self.record('free_open', 240, (*gripper, .080))
        self.pose('source_hover')
        self.pose('source_grasp')
        self.record('contact_approach', 240, (*gripper, 0.))
        self.record('sustained_contact', 120)
        self.pose('source_hover')
        self.record('lifted_grasp', 120, ('actuator.robot', CommandKind.HOLD, ()))
        # Reduce the physical driver's force ceiling while its position target remains
        # closed, so any geometry or contact change is a physical slip response.
        drivers = self.plant.robot.get_dof_indices(self.plant.builder.finger_drivers).numpy().tolist()
        self.plant.robot.set_dof_max_efforts([[.05]*len(drivers)], dof_indices=drivers)
        self.report['slip_force_limit_n'] = self.plant.robot.get_dof_max_efforts(dof_indices=drivers).numpy().tolist()
        self.record('reduced_effort', 180)
        self.report['summary'] = {}
        for phase in dict.fromkeys(sample['phase'] for sample in self.report['samples']):
            rows = [r for r in self.report['samples'] if r['phase'] == phase]
            data = [j for r in rows for j in r['fingers'].values()]
            relative = [_minus(r['part_m'], r['tcp_m']) for r in rows]
            self.report['summary'][phase] = {
                'samples': len(rows), 'bilateral_samples': sum(r['bilateral'] for r in rows),
                'mean_abs_qdot_minus_encoder_mps': mean(abs(j['qdot_mps']-j['encoder_delta_mps']) for j in data),
                'mean_abs_qdot_minus_twist_mps': mean(abs(j['qdot_mps']-j['link_twist_mps']) for j in data),
                'max_part_tcp_offset_change_m': max(dist(relative[0], p) for p in relative)}
        self.report['passed'] = (self.report['summary']['lifted_grasp']['bilateral_samples'] == 120 and
                                  self.report['summary']['reduced_effort']['max_part_tcp_offset_change_m'] >= .04)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('assets', 'profile', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    plant = IsaacPlant(asset_root=args.assets, robot_profile=args.profile)
    check = FingerCheck(plant)
    check.report['profile'] = 'isaac62_finger_velocity_diagnostic'
    try:
        check.run_diagnostic()
        return 0 if check.report['passed'] else 4
    finally:
        write_json(args.output, check.report)
        plant.close()


if __name__ == '__main__':
    raise SystemExit(standalone_main(main))
