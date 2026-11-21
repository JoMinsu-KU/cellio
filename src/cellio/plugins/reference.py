"""Factories for the two canonical Franka workcells. Kit imports occur only at simulator startup."""

from cellio.core.signals import NominalSampler

from .canonical_signals import CanonicalWorldPredicates, canonical_bindings


class ReferenceScene:
    revision = "1.0.0"

    def __init__(self, name):
        self.scene_id = name

    def oracle_source_modules(self):
        return tuple(
            "cellio.core.oracle." + name
            for name in (
                "audit",
                "engine",
                "geometry",
                "presets",
                "projector",
                "temporal",
            )
        )

    def build_config(self):
        from cellio.scenes import get_scene

        return get_scene(self.scene_id)

    def signal_bindings(self, **options):
        return canonical_bindings(self.scene_id, **options)

    def create_mapper(self):
        return NominalSampler(self.signal_bindings())

    def create_world_predicates(self, **options):
        return CanonicalWorldPredicates(self.scene_id, **options)

    def create_plant(self, profile, options=None):
        if profile == "mock":
            from cellio.adapters.mock import MockPlant

            return MockPlant(**(options or {}))
        if profile == "isaac62":
            from cellio.adapters.isaac62 import IsaacPlant

            return IsaacPlant(**(options or {}))
        raise ValueError(f"Unknown plant profile {profile}")

    def entity_registry(self):
        from cellio.controllers.stages import CELL_A
        from cellio.controllers.stages_b import CELL_B

        a = self.scene_id == "cell_a"
        scene = self.build_config()
        stage_ids = [*(s.name for s in (CELL_A if a else CELL_B)), "done", "aborted"]
        event_fields = {
            "stage_id": {"type": "string", "enum": stage_ids},
            "job_id": {"type": "integer", "enum": [0]},
            "operation_epoch": {"type": "integer", "minimum": 0},
        }
        events = {
            "controller.stage_enter": event_fields,
            "controller.done": event_fields,
            "controller.abort": {
                **event_fields,
                "reason": {"type": "string"},
                "cancelled_epoch": {"type": "integer", "minimum": 0},
            },
            "controller.substage_enter": {
                **event_fields,
                "waypoint": {"type": "string", "enum": list(scene.waypoints)},
            },
        }
        actuators = {
            "actuator.robot": ["POSE", "HOLD"],
            "actuator.gripper": ["GRIPPER_GAP", "HOLD"],
            "control.operation": ["CANCEL_OPERATION"],
        }
        actuators.update(
            {"actuator.belt": ["BOOL"]}
            if a
            else {
                "actuator.process": ["BOOL"],
                "actuator.clamp": ["JOINT_POSITION", "HOLD"],
            }
        )
        joints = {f"panda_joint{i}": "revolute" for i in range(1, 8)}
        joints.update({f"panda_finger_joint{i}": "prismatic" for i in (1, 2)})
        if not a:
            joints["clamp"] = "prismatic"
        bodies = {
            key: "body"
            for key in ("part_000", "hand", "cell_tcp", "finger_left", "finger_right")
        }
        if not a:
            bodies["moving_jaw"] = "body"
        predicates = ["world.gripper_closed", "world.part_dropped"] + (
            ["world.pickup_present"]
            if a
            else ["world.fixture_loaded", "world.clamp_closed"]
        )
        return {
            "bodies": bodies,
            "joints": joints,
            "actuators": actuators,
            "signals": {b.signal_id: b.dtype for b in self.signal_bindings()},
            "zones": {z.name.lower().replace("/", "."): "box" for z in scene.zones},
            "waypoints": {p: "pose" for p in scene.waypoints},
            "predicates": {p: "bool" for p in predicates},
            "events": events,
        }

    def controller_presets(self):
        return ("reference", *(f"mutant.c_m{i}" for i in range(1, 7)))

    def create_controller(self, ident):
        from cellio.controllers import ReferenceController

        return ReferenceController(self.scene_id, ident)

    def create_oracle(self, horizon):
        from cellio.core.oracle import Oracle

        return Oracle(self.scene_id, horizon)

    def requirement_presets(self, horizon):
        from cellio.core.oracle import preset

        return preset(self.scene_id, horizon)

    def scenario_presets(
        self,
        arm="healthy",
        seed=1000,
        controller="reference",
        profile="isaac62_lockstep",
    ):
        from cellio.runtime.campaign import scenario

        return scenario(self.scene_id, arm, seed, controller, profile)

    def initial_state_sampler(self, seed):
        from cellio.runtime.campaign import initial_state

        return initial_state(self.scene_id, seed)

    def evidence_manifest(self):
        return {
            "code_license": "MIT",
            "asset_license": "NVIDIA Isaac Sim asset terms",
            "asset_release": "6.2.0",
            "units": {"length": "m", "mass": "kg", "angle": "rad", "time": "step/120"},
            "supports": ["F1", "F2", "F3", "F4", "F5", "F6"],
        }


def reference_scene(name):
    return ReferenceScene(name)


def cell_a():
    return reference_scene("cell_a")


def cell_b():
    return reference_scene("cell_b")
