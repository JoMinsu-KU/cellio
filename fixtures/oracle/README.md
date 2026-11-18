# CellIO independent oracle fixtures

`gold.json` expands the finite traces in specification §27. Each record has sparse
inputs and independently transcribed expected output. `freeze_gold.py` recreates
the file without importing the evaluator. `gold.sha256` pins the exact UTF-8
bytes, including the final newline.

Unlisted steps carry the normal physical state. O4 response witnesses only occur
at explicitly listed boundaries. O1 triples mean (step, phase, job). In O2/O6
events represent actual router receipts, reset comparisons, or actuator evidence.
O3 triples record (quality, source step, value). A missing list item represents
absent oracle evidence, distinct from a recorded MISSING sensor placeholder.
O5 uses the named cube, the declared world location and the measured joint state.

A witness is the first decisive boundary for the winning verdict. For invariant
success this is the final observed boundary. A censored or indeterminate result
has a null witness. A finite invariant counts as one obligation once applicable.
O6 restore cases combine the response and the post-end invariant. An unexercised
wrapper counts zero obligations. All case verdicts refer solely to the analytic
fixtures.
