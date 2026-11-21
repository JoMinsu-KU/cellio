"""Cell C descriptor and control ports for the shared CellIO episode runner."""

from copy import deepcopy
from pathlib import Path

from cellio.core.models import (
    ActuationReceipt,
    AdvanceReceipt,
    AppliedCommand,
    CellIOError,
    CommandIntent,
    CommandKind,
    ControllerOutput,
    TruthSnapshot,
    Verdict,
)
from cellio.core.signals import NominalSampler, SignalBinding

from .decision import update_latch
from .framework_oracle import (
    ORDER,
    ROUTE,
    SEQUENCE,
    TAG,
    InspectionOracle,
    pose_state,
    requirement_presets,
)
from .replay import BINS

DEFAULT_RECIPE = {
    "errors_mm": [0.0, 0.60],
    "infeed_offsets_mm": [[0.0, 0.0], [0.0, 0.0]],
}
REFERENCE = "reference"
STALE = "mutant.stale_ok"


class InspectionPLC:
    def __init__(self, stale):
        self.stale = stale

    def reset(self, config, initial_view):
        self.attempt_id = config["attempt_id"]
        self.latch = None
        self.sequence = 0
        self.done = False

    def tick(self, view, tick):
        commands = []
        signals = view.signals
        if signals["gauge.ready"].value:
            part = "P2" if signals["gauge.part_p2"].value else "P1"
            self.latch = update_latch(
                self.latch, part, signals["gauge.pass"].value, self.stale
            )
            owner, route = self.latch
            for target, value in ((TAG, owner == "P2"), (ROUTE, route == "OK")):
                commands.append(
                    CommandIntent(
                        f"{self.attempt_id}:cmd:{self.sequence}",
                        self.sequence,
                        tick.episode_id,
                        tick.generation,
                        0,
                        tick.step,
                        target,
                        CommandKind.BOOL,
                        value,
                        0,
                    )
                )
                self.sequence += 1
        self.done = self.done or signals["sorting.complete"].value
        return ControllerOutput(
            tuple(commands),
            (),
            "complete" if self.done else "sort",
            0,
            0,
            "DONE" if self.done else "ACTIVE",
        )

    def reset_evidence(self):
        return self.latch is None and not self.done and self.sequence == 0

    def close(self):
        pass


class InspectionPredicates:
    def reset(self, generation):
        self.generation = generation

    def sample(self, truth, tick):
        if truth.tick != tick or tick.generation != self.generation:
            raise CellIOError("CLOCK_ORDER", "Incorrect inspection predicate clock")
        return {"world.gauge_ready": truth.plant_state.get("event") == "inspection"}


class MockInspection:
    """Deterministic logical fixture. Its synthetic tray and gauge values are labelled mock."""

    def __init__(self, recipe):
        self.recipe = recipe
        self.events = dict(
            zip(
                (0, 1, 2, 4, 5, 6, 7, 8, 10, 11, 12, 16, 17, 18, 20), ORDER, strict=True
            )
        )
        self.step = 0
        self.route = None

    @property
    def state(self):
        if self.step not in self.events:
            return {}
        part, event = self.events[self.step]
        state = {"part": part, "event": event}
        if event == "inspection":
            error = self.recipe["errors_mm"][int(part[1]) - 1]
            state.update(
                geometry_height_mm=33.0 + error,
                probe_height_mm=33.0 + error,
                probe_ok=abs(error) <= 0.2001,
            )
        elif event in ("grasp", "release"):
            stations = {
                1: "infeed",
                2: "fixture",
                5: "fixture",
                6: self.route,
                11: "infeed",
                12: "fixture",
                17: "fixture",
                18: self.route,
            }
            state["station"] = stations[self.step]
        elif event == "output_settled":
            x, y = BINS[self.route]
            state.update(
                pose_state(
                    {
                        "position_m": (x, y, 0.854),
                        "orientation_wxyz": (1.0, 0.0, 0.0, 0.0),
                        "linear_speed_m_s": 0.0,
                        "angular_speed_rad_s": 0.0,
                    }
                )
            )
        return state

    def decision(self, owner, route):
        self.route = route

    def advance(self):
        start = self.step
        self.step += 1
        return AdvanceReceipt(
            start, self.step, start / 120, self.step / 120, True, False
        )

    def close(self):
        pass


class InspectionPlant:
    def __init__(self, profile, options):
        if profile not in ("mock", "isaac62"):
            raise CellIOError("CFG_PROFILE", f"Unknown Cell C profile {profile}")
        if not options.get("headless", True):
            raise CellIOError(
                "CFG_PROFILE", "The native Cell C inspection plant runs headless"
            )
        self.profile = profile
        self.assets = options.get("asset_root")
        self.device = None

    def configure_attempt(self, directory, config):
        self.folder = Path(directory)
        self.controller_id = config["controller"]["id"]

    def load(self, scene_config):
        self.recipe = {**deepcopy(DEFAULT_RECIPE), **scene_config.get("parameters", {})}
        self.warmup = scene_config["warmup_steps"]

    def reset(self, initial_state, generation):
        self.generation = generation
        self.targets = {}
        if self.profile == "isaac62":
            from .framework_native import NativeConnection

            self.device = NativeConnection(
                self.folder / "native",
                self.controller_id == STALE,
                self.assets,
                self.recipe,
                self.warmup,
            )
        else:
            self.device = MockInspection(self.recipe)
        self.provenance = {
            "scene": "cell_c",
            "backend": "native_isaac_physx_cpu_tgs"
            if self.profile == "isaac62"
            else "synthetic_logical_fixture",
            "physics_hz": 120,
            "control_hz": 30,
            "measurement_timing": "event at original boundary",
            "resource_measurement_scope": "common worker process; native child excluded"
            if self.profile == "isaac62"
            else "common worker process",
            "controller": "common episode, tag and route applied at gauge boundary",
            "recipe": self.recipe,
            "native_directory": "native" if self.profile == "isaac62" else None,
        }

    def snapshot(self, tick):
        if tick.step != self.device.step or tick.generation != self.generation:
            raise CellIOError("CLOCK_ORDER", "Incorrect inspection snapshot boundary")
        return TruthSnapshot(tick, {}, {}, (), self.device.state, True)

    def apply(self, commands, overrides, tick):
        if overrides or tick.step != self.device.step:
            raise CellIOError("SIM_ACTUATION_FAILED", "Unexpected inspection actuation")
        delivered = [c for c in commands if c.status == "DELIVERED"]
        applied = []
        if self.device.state.get("event") == "inspection":
            values = {c.intent.actuator_id: c.intent.value for c in delivered}
            if len(delivered) != 2 or set(values) != {TAG, ROUTE}:
                raise CellIOError(
                    "SIM_ACTUATION_FAILED",
                    "Inspection requires both tag and route commands",
                )
            self.device.decision(
                "P2" if values[TAG] else "P1", "OK" if values[ROUTE] else "NG"
            )
            self.targets.update(values)
            for command in delivered:
                intent = command.intent
                applied.append(
                    AppliedCommand(
                        intent.command_id,
                        intent.actuator_id,
                        tick.generation,
                        intent.issued_step,
                        command.due_step,
                        tick.step,
                        "APPLIED",
                        None,
                        intent.value,
                    )
                )
        elif delivered:
            raise CellIOError(
                "SIM_ACTUATION_FAILED", "Sorting command outside an inspection cycle"
            )
        return ActuationReceipt(tick, True, tuple(applied), dict(self.targets), ())

    def advance_one_step(self, render=False):
        return self.device.advance()

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None


class InspectionScene:
    scene_id = "cell_c"
    revision = "1.0.0"

    def build_config(self):
        return {
            "scene_id": self.scene_id,
            "native_stage": "inspection_equipment.usdc",
            "nominal_height_mm": 33.0,
            "tolerance_mm": 0.20,
            "bins_xy_m": dict(BINS),
        }

    def controller_presets(self):
        return (REFERENCE, STALE)

    def create_controller(self, ident):
        if ident not in self.controller_presets():
            raise CellIOError("CFG_REFERENCE", f"Unknown Cell C controller {ident}")
        return InspectionPLC(ident == STALE)

    def signal_bindings(self):
        return (
            SignalBinding(
                "gauge.ready",
                "bool",
                lambda truth: truth.plant_state.get("event") == "inspection",
            ),
            SignalBinding(
                "gauge.part_p2",
                "bool",
                lambda truth: (
                    truth.plant_state.get("part") == "P2"
                    if truth.plant_state.get("event") == "inspection"
                    else None
                ),
            ),
            SignalBinding(
                "gauge.pass", "bool", lambda truth: truth.plant_state.get("probe_ok")
            ),
            SignalBinding(
                "sorting.complete",
                "bool",
                lambda truth: (
                    truth.plant_state.get("event") == "output_settled"
                    and truth.plant_state["part"] == "P2"
                ),
            ),
        )

    def create_mapper(self):
        return NominalSampler(self.signal_bindings())

    def create_world_predicates(self):
        return InspectionPredicates()

    def create_plant(self, profile, options=None):
        return InspectionPlant(profile, options or {})

    def entity_registry(self):
        return {
            "bodies": {},
            "joints": {},
            "zones": {"ok_tray": "box", "ng_tray": "box"},
            "waypoints": {},
            "actuators": {TAG: ["BOOL"], ROUTE: ["BOOL"]},
            "signals": {b.signal_id: b.dtype for b in self.signal_bindings()},
            "predicates": {"world.gauge_ready": "bool"},
            "events": {},
        }

    def scene_parameters_schema(self):
        def pair(item):
            return {"type": "array", "minItems": 2, "maxItems": 2, "items": item}

        number = {"type": "number"}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "errors_mm": pair(number),
                "infeed_offsets_mm": pair(pair(number)),
            },
        }

    def validate_config(self, cfg):
        from cellio.config.schema import CTRL_DEFAULTS

        if (
            cfg["episode"]["reset_mode"] != "fresh_process"
            or cfg["faults"]
            or cfg["artifacts"]["video"]
        ):
            raise CellIOError(
                "CFG_PROFILE",
                "Cell C supports fresh-process inspection controller variants; set faults to [] and video to false",
            )
        if any(
            CTRL_DEFAULTS.get(k, "hold" if k == "terminal_mode" else None) != v
            for k, v in cfg["controller"]["parameters"].items()
        ):
            raise CellIOError(
                "CFG_PROFILE", "Unsupported controller parameter for Cell C"
            )

    def scenario_presets(
        self, arm="healthy", seed=1000, controller=REFERENCE, profile="isaac62_lockstep"
    ):
        from cellio.config import resolve

        if arm != "healthy":
            raise CellIOError(
                "CFG_REFERENCE",
                "Cell C's stale-result condition is selected by controller ID",
            )
        return resolve(
            {
                "schema_version": "cellio.config/1.0",
                "scenario_id": f"cell_c.{controller}",
                "profile": profile,
                "scene": {"cell_id": self.scene_id},
                "controller": {"id": controller},
                "requirements": {"preset": self.scene_id},
                "seeds": {"scene": seed, "controller": 0, "fault": 0},
                "episode": {
                    "horizon_steps": 32 if profile == "mock_lockstep" else 7200,
                    "warmup_steps": 0,
                },
            }
        )

    def initial_state_sampler(self, seed):
        return {"seed": seed}

    def requirement_presets(self, horizon):
        return requirement_presets(horizon)

    def create_oracle(self, horizon):
        return InspectionOracle(horizon)

    def oracle_source_modules(self):
        return ("cellio.inspection.framework_oracle", "cellio.inspection.replay")

    def task_outcome(self, results, complete):
        goal = next((r for r in results if r.requirement_id == SEQUENCE), None)
        if goal and goal.verdict == Verdict.PASS:
            return "SUCCESS"
        return (
            "FAILED"
            if goal and goal.verdict == Verdict.FAIL and complete
            else "UNKNOWN"
        )

    def evidence_manifest(self):
        return {
            "code_license": "MIT",
            "asset_release": "6.2.0",
            "sequence": "two consecutive parts",
            "physics_hz": 120,
            "pose_hz": 30,
            "actuation": [TAG, ROUTE],
            "native_artifacts": [
                "native/result.json",
                "native/events.json",
                "native/motion.jsonl",
            ],
            "measurements": [
                "native probe",
                "mesh reference",
                "released bracket pose and terminal speeds",
            ],
        }


def cell_c():
    return InspectionScene()
