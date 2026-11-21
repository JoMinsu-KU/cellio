# Which rules did the replay use?

An online verdict belongs to the rule specification and monitor used when the workcell ran. A replay can produce the same verdict and first witness under a different deadline. CellIO reports the rule comparison and the observed agreement independently.

## Running a comparison

Replace `ATTEMPT` with the extracted directory containing `COMPLETE`.

```sh
python -m cellio verify-artifact --artifact ATTEMPT
python -m cellio evaluate --artifact ATTEMPT
python -m cellio evaluate --artifact ATTEMPT --recorded-rules
python -m cellio evaluate --artifact ATTEMPT --requirements revised-rules.json
```

The default uses the installed preset identified in the scenario. `--recorded-rules` supplies the original ordered rule array to the installed evaluator. `--requirements` accepts a JSON or YAML rule array, a named preset, or a compatible CellIO rule lock. Both options use the evaluator installed on the analysis machine. Its schema support determines which older or revised rule definitions it can interpret. The CLI writes `evaluation.json` alongside the attempt tree. Its destination includes a digest of the mode, evaluated rules and evaluator identity.

| Report field | Interpretation |
| --- | --- |
| `recorded_rules_digest`, `rules_digest` | Canonical SHA-256 of the rules at recording and in this analysis. |
| `same_rule_specification`, `same_oracle_implementation` | Each comparison returns `true`, `false` or `null` when the evidence is unavailable. |
| `same_rule_revision` | `true` when both identities match, `false` on a known difference, otherwise `null`. The corresponding `rule_identity_status` is `matched`, `different` or `unavailable`. |
| `observed_results_equal` | Compares verdict and first witness step by requirement ID. Returns `null` when online results are absent. The `differences` map retains mismatches even if the rules changed. |
| `online_offline_equal` | Reports observed agreement only if rule and evaluator identity match and online results exist. Otherwise returns `null` and `online_comparable` is `false`. |

For example, increasing Cell A's R04 deadline from 960 to 961 steps leaves the verdicts on a short healthy trace unchanged. Replaying a newly recorded 36-step mock attempt after that change yields `same_rule_revision=false`, `observed_results_equal=true` and `online_offline_equal=null`.

## Evidence recorded by the writer

Dense attempts with manifest schema `cellio.attempt/1.1` include `rules.resolved.json`. Its `cellio.rule-lock/1` document contains the scene, horizon, ordered rules, their canonical SHA-256, and the evaluator class and source fingerprints. The first trace record binds the canonical digest of this lock into the trace chain. The final manifest also seals the lock's byte length and file SHA-256. The completion marker seals the manifest. A separately published archive digest provides a reference for a downloaded copy.

The bundled plugins identify their rule, projection, predicate and monitor sources. Digesting the UTF-8 Python text with LF line endings permits comparison across Windows and Linux checkouts. A comment change in a fingerprinted module can therefore mark the implementations as different. The interpreter, simulator, external resources and operating system remain separate environment provenance.

An external Python scene descriptor can implement `oracle_source_modules()` and return the importable module names for all source dependencies that determine its evaluation. CellIO also fingerprints the descriptor class module, evaluator class module and shared CellIO model, serialization and evaluation modules. A missing declaration or unreadable module source leaves implementation identity unverified. A benchmark-mode B0 run stores its resolved rules and records the absence of an online monitor.

Older dense attempts, including the public `mock-36` example and the original A/B campaign, lack a sealed rule specification. On those records the offline evaluation still returns requirement results and can report observed agreement. The compact historical Cell C observations also lack online verdicts. For both formats the recorded rule identity is unavailable and `--recorded-rules` reports an error.
