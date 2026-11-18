# Fault timing

Every episode has a single integer clock. At 120 Hz the worker reads a physics snapshot, schedules faults, maps signals, optionally calls the controller, routes commands, applies actuator targets, evaluates rules, records evidence, and advances physics once. The controller executes every fourth step. An event emitted by that controller becomes eligible to activate a fault at the next physics boundary. Entries issue their command at the next controller tick.

The active interval includes its start and excludes its end. F1 substitutes a Boolean value for a valid observation. F2 retains the captured sample, including its age and transport identity. F3 queues observations for delayed delivery and discards pending delayed packets at release. F4 delays command delivery and applies the most recent valid desired target when the delay clears. A cancellation at that boundary takes precedence. F5 delivers an explicit invalid observation. F6 holds an actuator position target and retains its finite drive settings.

Activation, intervention, measured manifestation and restoration are separately recorded. A physical F6 hold needs at least 10 mm of conflicting commanded travel and 31 consecutive boundary samples within 0.5 mm. Recovery at the horizon can remain unobserved. The source trace retains all attempts, including those whose expected trigger was never reached.
