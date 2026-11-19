# Physical settling following the Cell A reference R06 failures

Secondary, retrospective diagnosis of 40 Cell A F2 and 40 Cell A F3 failures. Each arm has 20 initial states and two repeats. Time zero is the recorded abort declaration. An interval of 12 elapsed simulation steps spans 13 boundary observations at 120 Hz.

| Arm | Attempts | R05 receipt | First 12-sample completion | Registered dwell start | Registered dwell completion | Lateness |
|---|---:|---:|---:|---:|---:|---:|
| Pooled | 80 | 0/0/0 | 97/97/97 | 86/86/86 | 98/98/98 | 26/26/26 |
| f2 | 40 | 0/0/0 | 97/97/97 | 86/86/86 | 98/98/98 | 26/26/26 |
| f3 | 40 | 0/0/0 | 97/97/97 | 86/86/86 | 98/98/98 | 26/26/26 |

Table entries give minimum/median/maximum steps after abort, except lateness, which measures steps beyond the registered 72-step deadline. R05 covers operation cancellation, robot and gripper HOLD, a zero-speed belt target and an empty queue. The evidence joins native command IDs to accepted actuator receipts.
Among the 80 attempts with an observed dwell, lateness was 216.7/216.7/216.7 ms (minimum/median/maximum).

The retrospective search inspected 7,920 boundary observations from abort through first completed dwell or horizon. 0 attempts never reached the dwell, and 0 contained invalid measurements within the inspected interval.

| Boundary | Channels exceeding their limits | Counts |
|---|---|---:|
| Latest eligible dwell start, abort + 60 | panda_finger_joint1,panda_finger_joint2 | 80 |
| R06 deadline, abort + 72 | (none) | 80 |
| Last boundary before actual qualifying dwell | panda_finger_joint1,panda_finger_joint2 | 80 |

Absolute speed limits are 0.020 rad/s for each arm joint, 0.003 m/s for each finger and 0.001 m/s for the belt.
At abort + 60, finger 1 measured 0.003798–0.003800 m/s and finger 2 measured 0.003798–0.003800 m/s in absolute speed among over-limit observations.
Immediately before the qualifying dwell, finger 1 measured 0.003071–0.003072 m/s and finger 2 measured 0.003071–0.003072 m/s in absolute speed among over-limit observations.
From the last eligible start, at least one finger exceeded the speed bound on +60 to +61, +73 to +85 in 80 attempts.
The longest uninterrupted stretch contained 11 quiet observations within the registered window. The stretch ending at the deadline contained 11.
Considering arm and belt speeds alone, the first full dwell ended 13 steps after abort in 80 attempts.
The measured finger velocities prevented the full-system stop dwell. A causal explanation of the residual finger movement would require additional evidence.
