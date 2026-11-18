# Registering a workcell

Install the scene package in the Windows Conda environment that runs the worker. For WSL replay, install the same package revision in the WSL analysis environment. Scene modules must import on a CPU-only machine. Create the simulator application inside the physical plant factory.

Publish a zero-argument descriptor factory using the workcell's stable ID in the package's `pyproject.toml`.

```toml
[project.entry-points."cellio.scene_plugins"]
lab_cell = "lab_workcell.descriptor:make_scene"
```

Add the installed ID to the comma-separated `CELLIO_SCENE_ALLOWLIST` in both environments. The two canonical scenes are included by default. Use the ID as `scene.cell_id` and `requirements.preset` in the scenario. Run `cellio validate --config <scenario.yaml>` in the environment that will execute it.

| Descriptor member | Required behavior |
| --- | --- |
| `scene_id`, `revision` | Match the entry point name and declare a numeric major.minor.patch release. |
| `build_config()`, `entity_registry()` | Supply geometry, asset references, materials and typed resource maps for bodies, joints, signals, actuators, zones, waypoints and world predicates. Signal types are `bool`, `int` or `float`. Actuator values list accepted command kinds. |
| `create_plant(profile, options)` | Return a plant supporting `load`, `reset`, `snapshot`, `apply`, `advance_one_step` and `close`. Bind the active generation to snapshots, commands and simulator handles. For F6, implement `driver_positions(target, truth)`, `coordinate(target, truth)` and `actuation_evidence(target)` using the actual actuator drivers. |
| `signal_bindings()`, `create_mapper()`, `create_world_predicates()` | Declare typed public signals. Map raw snapshots to nominal readings and sample physical trigger predicates before observation faults. Return immutable readings with the current generation and tick. |
| `controller_presets()`, `create_controller(id)` | List accepted IDs and instantiate a controller using the public observation/receipt view. |
| `requirement_presets(horizon)`, `create_oracle(horizon)` | Provide declared rules and a monitor that reads live and decoded frames. Supply boundary fixtures for each new atomic predicate. |
| `scenario_presets(arm, seed, controller, profile)`, `initial_state_sampler(seed)` | Provide healthy and applicable fault scenarios, seeded initial states, bounds and units. |
| `evidence_manifest()` | List the asset sources, license, units and supported fault families. |

The artifact stores the selected distribution, provider version, descriptor implementation hash, registry hash and declared evidence under `result.json → scene_plugin`. Its final manifest binds that file to the trace; actual plant calibration and asset digests belong in `assets.lock.json` and the plant provenance. New processes resolve the installed descriptor independently when evaluating the trace.

For hard reload, the worker reuses the plant and creates fresh signal, controller and oracle state. Supply `plant.reset_evidence(truth)` with named Boolean checks for measured post-reset state. The mapper, controller and oracle may expose `reset_evidence()` returning a Boolean or `None`. Missing evidence remains unknown. The previous attempt's committed manifest and generation identify the transition.

Exercise the common contract first with an 8-step mock attempt and offline replay. Repeat with a second generation, stale callbacks, a failed advance and a write failure. Run the same lifecycle, receipt and replay checks with the physical adapter, then execute the scene's own commissioning checks. The canonical research campaigns retain their predefined cells, seeds and thresholds.

The executable extension example is in [the registry and replay integration test](../tests/integration/test_scene_plugin.py). The canonical descriptor is in [reference.py](../src/cellio/plugins/reference.py).

To trigger a fault from a controller event, include `events` in the entity registry. Map each event ID to its scalar payload fields. Each declaration supplies a type from `string`, `integer`, `number` or `boolean` and can restrict the allowed values with `enum`. Register the scene's stage names in the `stage_id` field. The compiler rejects unknown events, payload names and stage IDs before launch.
