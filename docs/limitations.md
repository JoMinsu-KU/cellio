# Scope and evidence

The reference profile simulates a single Franka workcell in Isaac Sim 6.2 on Windows. Each episode has zero or one configured fault. The fault models operate on virtual signals, command paths or position targets. Their physical effect is measured within the simulated scene.

The mock backend tests phase order, queue behavior, reset, serialization and verdicts. Hardware-dependent claims require a run from the Windows Isaac profile and its actual environment, asset and protocol locks. The same evidence can be replayed from WSL2 without Kit.

The campaign contains two fixed workcells and a finite set of blocked initial states. Rankings or rates apply to that protocol and candidate. A software release report records expected thresholds, infrastructure failures, incomplete obligations and any optional study components that were not performed.

The primary evaluation includes all 820 registered rows. Seed 1000 had been observed during development and contributes 68 rows. Seeds 1001–1019 contribute 752 rows. Conditions and repeat indices can share the same physical starting state. Mutants were scheduled only on seeds 1000–1004, so the two seed groups have different mixes of reference and mutant trials. Comparisons across the two groups should match the cell, controller and fault condition. [Commissioning history](adr/002-gripper-commissioning.md)

Cell A's two gripper-signal conditions each produced 40 reference stop-time violations. The stop commands took effect at the recorded abort boundary. The fingers then exceeded their 0.003 m/s limit during the required dwell. Across all 80 attempts, the first uninterrupted dwell finished 26 steps (216.7 ms) after the deadline. These traces identify the limiting joint speeds. Establishing which physical or control parameter caused the residual motion would require a separate experiment. [Physical settling after abort](../results/publication/candidate-8ad96bd/primary/r06-physical-diagnosis.md)

Cell B's F4 clamp-command delay took effect in 40 of 50 activated trials (80%, against a 98% target). Each of the 40 reference trials delayed one command. All 10 trials using mutant M4 recorded zero delayed commands in the active interval. These 10 remain in the planned denominator and carry the result `NOT_EXERCISED` in the paired mutant analysis.
