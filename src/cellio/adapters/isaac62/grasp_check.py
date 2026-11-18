"""Commission a stationary two-second friction grasp and an empty jaw round trip."""

import argparse
from math import dist
from pathlib import Path

from cellio.core.models import CommandIntent, CommandKind, DeliveryDecision, Tick
from cellio.io import write_json
from cellio.scenes import CELL_B
from cellio.scenes.definition import DOWNWARD
from .application import standalone_main
from .plant import IsaacPlant


class FixtureCheck:
    def __init__(self, plant, generation=1):
        self.plant = plant
        self.generation = generation
        self.sequence = 0
        self.steps = []
        self.report = {"cell_id": CELL_B.scene_id, "profile": "isaac62_submotion_check",
                       "passed": False, "actions": self.steps}

    def snapshot(self):
        return self.plant.snapshot(Tick("commission-friction", self.generation, self.plant.step))

    def advance(self, endpoint=None, kind=None, value=None):
        truth = self.snapshot()
        decisions = ()
        if endpoint:
            i = CommandIntent(f"commission:cmd:{self.sequence}", self.sequence, truth.tick.episode_id,
                              self.generation, 0, truth.tick.step, endpoint, kind, value, 0)
            decisions = (DeliveryDecision(i, 0, truth.tick.step, truth.tick.step, "DELIVERED", None),)
            self.sequence += 1
        self.plant.apply(decisions, {}, truth.tick)
        self.plant.advance_one_step()
        return self.snapshot()

    def action(self, name, endpoint, kind, value, test, deadline, dwell=0):
        # Commissioning uses exactly the same 120 Hz actuator port and limits as the episode.
        while self.plant.step % 4:
            self.advance()
        initial = self.plant.step
        first_ok = None
        truth = self.advance(endpoint, kind, value)
        while True:
            if name == "friction_grasp":
                self.report.setdefault("grasp_samples", []).append({"step": truth.tick.step,
                    "bilateral": self.contacts(truth), "gap_m": truth.plant_state["gripper.gap_m"],
                    "speed_mps": truth.plant_state["gripper.max_speed_mps"],
                    "driver_positions": self.plant.driver_positions("actuator.gripper", truth)})
            if test(truth):
                first_ok = truth.tick.step if first_ok is None else first_ok
                if truth.tick.step-first_ok >= dwell:
                    self.steps.append({"name": name, "start_step": initial, "end_step": truth.tick.step,
                                       "passed": True})
                    return truth
            else:
                first_ok = None
            if truth.tick.step-initial >= deadline:
                self.steps.append({"name": name, "start_step": initial, "end_step": truth.tick.step,
                                   "passed": False})
                raise RuntimeError(f"{name} did not complete in {deadline} physics steps")
            truth = self.advance()

    def pose(self, name):
        sequence = self.sequence
        return self.action(name, "actuator.robot", CommandKind.POSE, (*CELL_B.waypoints[name], *DOWNWARD),
                           lambda t: t.plant_state["robot.completed_command_id"] == sequence, 960)

    @staticmethod
    def contacts(truth):
        pairs = {frozenset(p) for p in truth.contacts}
        return all(frozenset(("part_000", finger)) in pairs for finger in ("finger_left", "finger_right"))

    def run(self, initial_state=None):
        self.plant.load({"scene_id": CELL_B.scene_id})
        self.plant.reset(initial_state or {}, self.generation)
        base_z = self.snapshot().bodies["part_000"].position_m[2]
        self.pose("park")
        self.action("empty_clamp_close", "actuator.clamp", CommandKind.JOINT_POSITION, -.065,
                    lambda t: t.joints["clamp"].position <= -.058 and abs(t.joints["clamp"].velocity) <= .003, 360, 4)
        self.action("empty_clamp_open", "actuator.clamp", CommandKind.JOINT_POSITION, 0.,
                    lambda t: t.joints["clamp"].position >= -.002 and abs(t.joints["clamp"].velocity) <= .003, 360, 4)
        self.pose("source_hover")
        self.pose("source_grasp")
        self.action("friction_grasp", "actuator.gripper", CommandKind.GRIPPER_GAP, 0.,
                    lambda t: self.contacts(t) and t.plant_state["gripper.gap_m"] <= .055
                              and t.plant_state["gripper.max_speed_mps"] <= .003, 240, 12)
        truth = self.pose("source_hover")
        while self.plant.step % 4:
            self.advance()
        # HOLD the arm at the measured joint configuration. The gripper continues
        # its finite, calibrated force drive throughout the entire interval.
        truth = self.advance("actuator.robot", CommandKind.HOLD, ())
        origin = truth.tick.step
        samples = []
        self.report["hold_samples"] = samples
        for i in range(241):
            if i:
                truth = self.advance()
            part = truth.bodies["part_000"].position_m
            tcp = truth.bodies["cell_tcp"].position_m
            relative = tuple(a-b for a, b in zip(part, tcp))
            if i == 0:
                reference = relative
            samples.append({"step": origin+i, "part_m": part, "tcp_m": tcp,
                            "gap_m": truth.plant_state["gripper.gap_m"],
                            "speed_mps": truth.plant_state["gripper.max_speed_mps"],
                            "bilateral_contacts": self.contacts(truth), "offset_change_m": dist(relative, reference)})
        self.report["initial_part_z_m"] = base_z
        self.report["passed"] = all(s["bilateral_contacts"] and s["part_m"][2]-base_z >= .080
                                    and s["offset_change_m"] <= .018 for s in samples)
        return self.report["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plant = IsaacPlant(asset_root=args.assets, robot_profile=args.profile)
    check = FixtureCheck(plant)
    try:
        return 0 if check.run() else 4
    except Exception as exc:
        check.report["error"] = str(exc)
        raise
    finally:
        write_json(args.output, check.report)
        plant.close()


if __name__ == "__main__":
    raise SystemExit(standalone_main(main))
