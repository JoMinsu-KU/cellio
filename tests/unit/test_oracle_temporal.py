from cellio.core.models import Verdict
from cellio.core.oracle.temporal import Boundary, Monitor, Registry, all3, any3, not3


def obs(kind, step, ident="", job=0, **fields):
    return {
        "event_type": kind,
        "event_id": ident or f"{kind}:{step}",
        "job_id": job,
        "operation_epoch": 0,
        "phase": 4,
        **fields,
    }


def rule(kind="response", dwell=0):
    result = {
        "id": "test",
        "category": "compliance",
        "kind": kind,
        "scope": "operation",
        "trigger": "request",
        "predicate": {"op": "pred", "name": "evidence", "args": {}},
        "dwell_steps": dwell,
        "required_coverage": True,
    }
    if kind in {"response", "bounded_response_or_abort", "dwell"}:
        result["window"] = {"lower_steps": 1, "upper_steps": 5}
    if kind == "bounded_response_or_abort":
        result.update(abort_matcher="abort", abort_grace_steps=3)
    return result


def monitor(spec):
    reg = Registry()
    reg.register(
        "evidence", lambda b, e, a: b.facts.get("by_job", {}).get(e["job_id"], False)
    )
    return Monitor(spec, reg)


def test_truth_tables():
    for v in (True, False, None):
        assert all3((v, False)) is False
        assert all3((v, True)) is v
        assert any3((v, True)) is True
        assert any3((v, False)) is v
    assert not3(None) is None and not3(False) is True and not3(True) is False


def response(yes=(), unknown=(), extra=(), end=12, dwell=0):
    m = monitor(rule("bounded_response_or_abort", dwell))
    for k in range(end + 1):
        events = ([obs("request", k)] if k == 4 else []) + [
            e for step, e in extra if step == k
        ]
        value = None if k in unknown else k in yes
        m.feed(Boundary(k, {"by_job": {0: value}}, events))
    return m.finish(end, end == 12)


def test_response_or_abort_has_separate_closed_windows():
    assert response(yes=(9,)).first_witness_step == 9
    fail = response(yes=(10,))
    assert (fail.verdict, fail.first_witness_step) == (Verdict.FAIL, 12)
    assert response(extra=[(12, obs("abort", 12))]).verdict == Verdict.PASS
    assert response(end=11).verdict == Verdict.UNKNOWN
    assert response(extra=[(12, obs("abort", 12, job=1))]).verdict == Verdict.FAIL
    assert response(unknown=(8,)).verdict == Verdict.UNKNOWN


def test_possible_dwell_requires_a_contiguous_possible_witness():
    assert response(yes=(7, 9), unknown=(8,), dwell=2).verdict == Verdict.UNKNOWN
    assert response(yes=(5,), unknown=(9,), dwell=2).verdict == Verdict.FAIL
    assert response(yes=(7, 8, 9), dwell=2).first_witness_step == 9


def test_independent_obligations_and_absorbing_counterexample():
    m = monitor(rule())
    for k in range(21):
        events = [
            obs("request", k, job=job) for start, job in ((2, 0), (4, 1)) if start == k
        ]
        m.feed(Boundary(k, {"by_job": {0: k == 10, 1: k == 8}}, events))
    result = m.finish(20, True)
    assert (
        result.verdict,
        result.obligation_count,
        result.violated_count,
        result.first_witness_step,
    ) == (Verdict.FAIL, 2, 1, 7)


def test_absent_antecedent_depends_on_observation_completeness():
    m = monitor(rule())
    m.feed(Boundary(0, {}, ()))
    assert m.finish(0, True).verdict == Verdict.NOT_APPLICABLE
    assert m.finish(0, False).verdict == Verdict.UNKNOWN
