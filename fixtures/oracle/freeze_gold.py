"""Expand the hand-transcribed §27 cases. This authoring tool has no CellIO imports."""

import hashlib
import json
from pathlib import Path


# Each tuple contains the observations, verdict, decisive boundary, obligation count,
# and reason. A missing witness denotes absence of a decisive PASS/FAIL boundary.
FAMILIES = {
    "O1": [
        ({"before": [[2, 1, 0]], "after": [[4, 6, 0]]}, "PASS", 4, 1, "ORDER_SATISFIED"),
        ({"before": [[4, 1, 0]], "after": [[4, 6, 0]]}, "PASS", 4, 1, "ORDER_SATISFIED"),
        ({"before": [[2, 1, 0], [6, 1, 1]], "after": [[4, 6, 0], [8, 6, 1]]}, "PASS", 4, 2, "ORDER_SATISFIED"),
        ({"before": [[1, 1, 0]], "after": [[12, 6, 0]]}, "PASS", 12, 1, "ORDER_SATISFIED"),
        ({"before": [[5, 1, 0]], "after": [[4, 6, 0]]}, "FAIL", 4, 1, "ORDER_VIOLATION"),
        ({"after": [[4, 6, 0]]}, "FAIL", 4, 1, "ORDER_VIOLATION"),
        ({"before": [[4, 7, 0]], "after": [[4, 4, 0]]}, "FAIL", 4, 1, "ORDER_VIOLATION"),
        ({"before": [[2, 1, 0]], "after": [[4, 6, 1]]}, "FAIL", 4, 1, "ORDER_VIOLATION"),
        ({"missing_before": [2, 3], "after": [[4, 6, 0]]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
        ({"observed_end": 3}, "UNKNOWN", None, 0, "HORIZON_INCOMPLETE"),
    ],
    "O2": [
        ({"events": [[4, "cancel"], [5, "cancelled_old"]]}, "PASS", 12, 1, "INVARIANT_SATISFIED"),
        ({"events": [[4, "cancel"], [4, "cancelled_old"]]}, "PASS", 12, 1, "INVARIANT_SATISFIED"),
        ({"events": [[4, "cancel"], [4, "apply_hold_new"]]}, "PASS", 12, 1, "INVARIANT_SATISFIED"),
        ({"events": [[4, "apply_old"]]}, "PASS", 12, 1, "INVARIANT_SATISFIED"),
        ({"events": [[4, "cancel"], [5, "apply_old"]]}, "FAIL", 5, 1, "INVARIANT_VIOLATION"),
        ({"events": [[4, "cancel"], [4, "apply_old"]]}, "FAIL", 4, 1, "INVARIANT_VIOLATION"),
        ({"events": [[6, "reset"], [7, "apply_old"]]}, "FAIL", 7, 1, "INVARIANT_VIOLATION"),
        ({"events": [[4, "apply_old"], [5, "apply_conflict"]]}, "FAIL", 5, 1, "INVARIANT_VIOLATION"),
        ({"events": [[4, "missing_receipt"]]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
        ({"observed_end": 8}, "UNKNOWN", None, 1, "HORIZON_INCOMPLETE"),
    ],
    "O3": [
        ({"samples": [["GOOD", 20, True]]}, "PASS", 20, 1, "ADMISSION_SATISFIED"),
        ({"samples": [["GOOD", 8, True]]}, "PASS", 20, 1, "ADMISSION_SATISFIED"),
        ({"samples": [["GOOD", 16, True]]}, "PASS", 20, 1, "ADMISSION_SATISFIED"),
        ({"samples": [["GOOD", 12, True], ["GOOD", 8, True]]}, "PASS", 20, 1, "ADMISSION_SATISFIED"),
        ({"samples": [["GOOD", 7, True]]}, "FAIL", 20, 1, "ADMISSION_VIOLATION"),
        ({"samples": [["BAD", 16, None]]}, "FAIL", 20, 1, "ADMISSION_VIOLATION"),
        ({"samples": [["MISSING", None, None]]}, "FAIL", 20, 1, "ADMISSION_VIOLATION"),
        ({"samples": [["GOOD", 21, True]]}, "FAIL", 20, 1, "ADMISSION_VIOLATION"),
        ({"samples": [None]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
        ({"samples": [["GOOD", 16, True], None]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
    ],
    "O4": [
        ({"triggers": [[4, 0]], "true": [[5, 0]]}, "PASS", 5, 1, "RESPONSE_SATISFIED"),
        ({"triggers": [[4, 0]], "true": [[9, 0]]}, "PASS", 9, 1, "RESPONSE_SATISFIED"),
        ({"triggers": [[4, 0]], "dwell": 2, "true": [[7, 0], [8, 0], [9, 0]]}, "PASS", 9, 1, "RESPONSE_SATISFIED"),
        ({"triggers": [[2, 0], [4, 1]], "true": [[6, 0], [8, 1]]}, "PASS", 6, 2, "RESPONSE_SATISFIED"),
        ({"triggers": [[4, 0]], "true": [[4, 0]]}, "FAIL", 9, 1, "DEADLINE_VIOLATION"),
        ({"triggers": [[4, 0]], "true": [[10, 0]]}, "FAIL", 9, 1, "DEADLINE_VIOLATION"),
        ({"triggers": [[4, 0]]}, "FAIL", 9, 1, "DEADLINE_VIOLATION"),
        ({"triggers": [[4, 0]], "dwell": 2, "true": [[8, 0]]}, "FAIL", 9, 1, "DEADLINE_VIOLATION"),
        ({"triggers": [[4, 0]], "observed_end": 8}, "UNKNOWN", None, 1, "HORIZON_INCOMPLETE"),
        ({"triggers": [[4, 0]], "unknown": [[7, 0]]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
    ],
    "O5": [
        ({"placed_from": 140}, "PASS", 200, 1, "RESPONSE_SATISFIED"),
        ({"placed_from": 180}, "PASS", 240, 1, "RESPONSE_SATISFIED"),
        ({"placed_from": 140, "yaw": 0.02}, "PASS", 200, 1, "RESPONSE_SATISFIED"),
        ({"placed_from": 140, "horizontal_m": 0.015, "linear_mps": 0.01}, "PASS", 200, 1, "RESPONSE_SATISFIED"),
        ({"placed_from": 140, "placed_body": "Part_001"}, "FAIL", 240, 1, "DEADLINE_VIOLATION"),
        ({"placed_from": 140, "horizontal_m": 0.015001}, "FAIL", 240, 1, "DEADLINE_VIOLATION"),
        ({"placed_from": 140, "linear_mps": 0.010001}, "FAIL", 240, 1, "DEADLINE_VIOLATION"),
        ({"placed_from": 140, "finger_q": 0.020, "done": True, "destination_signal": True}, "FAIL", 240, 1, "DEADLINE_VIOLATION"),
        ({"placed_from": 180, "observed_end": 239}, "UNKNOWN", None, 1, "HORIZON_INCOMPLETE"),
        ({"placed_from": 180, "invalid_part_steps": [210]}, "UNKNOWN", None, 1, "EVIDENCE_INCOMPLETE"),
    ],
    "O6": [
        ({"events": [[6, "fresh_sample"]]}, "PASS", 6, 2, "RESTORED_NO_TAIL"),
        ({"events": [[6, "old_due_purged"], [6, "router_hold_applied"]]}, "PASS", 6, 2, "RESTORED_NO_TAIL"),
        ({"events": [[6, "override_removed"], [6, "normal_target_applied"]]}, "PASS", 6, 2, "RESTORED_NO_TAIL"),
        ({"events": [[6, "reset_clean"], [7, "old_packet_rejected"], [7, "new_packet_applied"]]}, "PASS", 12, 1, "INVARIANT_SATISFIED"),
        ({"events": [[6, "fresh_sample"], [7, "old_packet_applied"]]}, "FAIL", 7, 2, "INVARIANT_VIOLATION"),
        ({"events": [[6, "old_override_applied"]]}, "FAIL", 6, 2, "INVARIANT_VIOLATION"),
        ({"events": [[6, "reset_dirty_cache"]]}, "FAIL", 6, 1, "INVARIANT_VIOLATION"),
        ({"events": [[6, "reset_clean"]], "post_reset_position_delta_m": 0.00011}, "FAIL", 6, 1, "INVARIANT_VIOLATION"),
        ({"observed_end": 5}, "UNKNOWN", None, 0, "HORIZON_INCOMPLETE"),
        ({"events": [[6, "unknown_target_receipt"]]}, "UNKNOWN", None, 2, "EVIDENCE_INCOMPLETE"),
    ],
}


def main():
    root = Path(__file__).resolve().parent
    records = []
    for cell in ("cell_a", "cell_b"):
        for family, cases in FAMILIES.items():
            horizon = {"O3": 24, "O5": 240}.get(family, 12)
            for number, (inputs, verdict, step, count, reason) in enumerate(cases, 1):
                records.append({"id": f"ora.{cell}.{family}.c{number:02}", "cell": cell,
                                "family": family, "horizon": horizon, "input": inputs,
                                "expected": {"verdict": verdict, "first_witness_step": step,
                                             "obligation_count": count, "reason": reason,
                                             "coverage_missed": False}})
            for number, wrapper in enumerate(("absent_scope_event", "disabled"), 1):
                records.append({"id": f"ora.{cell}.{family}.n{number:02}", "cell": cell,
                                "family": family, "horizon": horizon, "input": {"wrapper": wrapper},
                                "expected": {"verdict": "NOT_APPLICABLE", "first_witness_step": None,
                                             "obligation_count": 0, "reason": "NOT_TRIGGERED",
                                             "coverage_missed": True}})
    raw = (json.dumps(records, indent=2) + "\n").encode("utf8")
    (root / "gold.json").write_bytes(raw)
    (root / "gold.sha256").write_bytes((hashlib.sha256(raw).hexdigest() + "  gold.json\n").encode("ascii"))


if __name__ == "__main__":
    main()
