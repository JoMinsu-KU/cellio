# Qualification of candidate a6f3f9d

The fixed-state suite passed. The coordinator then stopped during the boundary suite when Cell A violated the carry requirement. The pilot, protocol freeze and 820-run canonical evaluation were never reached for this candidate. The frozen plan and all first-attempt identities are preserved. [Candidate summary and hashes](summary.json)

| Suite and cell | Planned | Started and completed at 7,200 steps | Task SUCCESS | Compliance PASS | Compliance FAIL | Unstarted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed state, Cell A | 20 | 20 | 20 | 20 | 0 | 0 |
| Fixed state, Cell B | 20 | 20 | 20 | 20 | 0 | 0 |
| Boundaries, Cell A | 8 | 4 | 4 | 3 | 1 | 4 |
| Boundaries, Cell B | 8 | 4 | 4 | 4 | 0 | 4 |

Each cell repeated one nominal physical state 20 times. Across the 40 fixed-state attempts, Windows and WSL2 agreed on the 680 recorded requirement verdicts and first witnesses. The candidate summary binds these original records to the plan and per-attempt checksums. [Fixed-state plan](../commission-v1/campaign.plan.json) · [40 verified first attempts](../commission-v1/summary.json)

The boundary plan prescribed eight poses per cell. Four poses per cell ran before the stop. Independent WSL2 replay from the archived candidate source reproduced all 136 requirement verdicts, first witnesses and reasons from the eight sealed attempts. Each row records the exact pose, native and replay fingerprints, and the SHA-256 of the sealed trace. [Boundary plan](boundaries-v1/campaign.plan.json) · [Eight per-attempt records](boundaries-v1/rows)

Cell A boundary 3 used an x offset of −1 mm, a y offset of +4 mm and a yaw of +0.03 rad. Relative to the part-to-tool offset at grasp step 1,922, displacement was 17.632 mm at step 2,042 and 18.942 mm at step 2,043. This crossed the 18 mm carry limit while both fingers remained in contact with the workpiece. The part later reached its goal at step 3,500. That first attempt therefore finished with task SUCCESS and compliance FAIL. [First failed gate and physical witnesses](summary.json) · [Complete requirement record](boundaries-v1/rows/77685e226d0e9fcb02b51670.json)
