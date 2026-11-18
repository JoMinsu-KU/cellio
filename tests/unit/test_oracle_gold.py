import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from cellio.core.oracle.fixtures import evaluate_fixture

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "oracle"
RAW = (ROOT / "gold.json").read_bytes()
CASES = json.loads(RAW)


def test_frozen_independently_labeled_corpus():
    assert (
        hashlib.sha256(RAW).hexdigest() == (ROOT / "gold.sha256").read_text().split()[0]
    )
    assert len({case["id"] for case in CASES}) == 144
    assert Counter(case["expected"]["verdict"] for case in CASES) == {
        "PASS": 48,
        "FAIL": 48,
        "UNKNOWN": 24,
        "NOT_APPLICABLE": 24,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_against_frozen_label(case):
    answer = evaluate_fixture(case)
    observed = {
        k: getattr(answer, k)
        for k in ("verdict", "first_witness_step", "obligation_count", "reason")
    }
    observed["coverage_missed"] = answer.verdict == "NOT_APPLICABLE"
    assert observed == case["expected"]
