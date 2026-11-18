"""Finite-trace, three-valued monitoring over ordered boundary evidence."""

from collections.abc import Mapping
from dataclasses import dataclass

from cellio.core.models import CellIOError, RequirementResult, Verdict


def all3(values):
    uncertain = False
    for value in values:
        if value is False:
            return False
        uncertain |= value is None
    return None if uncertain else True


def any3(values):
    uncertain = False
    for value in values:
        if value is True:
            return True
        uncertain |= value is None
    return None if uncertain else False


def not3(value):
    return None if value is None else not value


@dataclass(frozen=True)
class Boundary:
    step: int
    facts: Mapping
    events: tuple | list = ()
    raw: object = None


class Registry:
    def __init__(self):
        self.atoms = {}

    def register(self, name, predicate, arguments=()):
        if name in self.atoms:
            raise CellIOError("CFG_ORACLE_SCHEMA", f"duplicate predicate {name}")
        self.atoms[name] = predicate, frozenset(arguments)

    def compile(self, expression):
        atoms = 0

        def parse(node, level):
            nonlocal atoms
            if level > 8 or not isinstance(node, Mapping):
                raise CellIOError(
                    "CFG_ORACLE_SCHEMA", "invalid predicate or nesting exceeds eight"
                )
            op = node.get("op")
            if op == "pred" and set(node) <= {"op", "name", "args"}:
                atoms += 1
                entry = self.atoms.get(node.get("name"))
                args = node.get("args", {})
                if (
                    not entry
                    or not isinstance(args, Mapping)
                    or set(args) != entry[1]
                    or atoms > 64
                ):
                    raise CellIOError(
                        "CFG_ORACLE_SCHEMA",
                        "unregistered predicate or invalid arguments",
                    )
                return lambda boundary, event: entry[0](boundary, event, args)
            if op == "not" and set(node) == {"op", "item"}:
                operand = parse(node["item"], level + 1)
                return lambda boundary, event: not3(operand(boundary, event))
            if op in ("all", "any") and set(node) == {"op", "items"}:
                items = node["items"]
                if isinstance(items, (list, tuple)) and items:
                    operands = [parse(item, level + 1) for item in items]
                    reducer = all3 if op == "all" else any3
                    return lambda boundary, event: reducer(
                        f(boundary, event) for f in operands
                    )
            raise CellIOError("CFG_ORACLE_SCHEMA", "invalid predicate expression")

        return parse(expression, 1)


def match(event, matcher):
    if isinstance(matcher, str):
        return event.get("event_type") == matcher
    if not isinstance(matcher, Mapping):
        return False
    fields = matcher.get("event_filter", {})
    return event.get("event_type") == matcher.get("event_type") and all(
        event.get("payload", {}).get(k, event.get(k)) == v for k, v in fields.items()
    )


@dataclass
class Obligation:
    event: dict
    start: int
    definite_start: int | None = None
    possible_start: int | None = None
    uncertain_witness: bool = False

    def dwell(self, step, value, duration):
        if value is not True:
            self.definite_start = None
        elif self.definite_start is None:
            self.definite_start = step
        if value is False:
            self.possible_start = None
        elif self.possible_start is None:
            self.possible_start = step
        definite = (
            self.definite_start is not None and step - self.definite_start >= duration
        )
        possible = (
            self.possible_start is not None and step - self.possible_start >= duration
        )
        self.uncertain_witness |= possible and not definite
        return definite


class Monitor:
    """One reusable temporal rule, retaining pending obligations and summarized outcomes."""

    def __init__(self, spec, registry):
        fields = {
            "id",
            "category",
            "kind",
            "scope",
            "trigger",
            "predicate",
            "window",
            "dwell_steps",
            "abort_grace_steps",
            "abort_matcher",
            "applicability",
            "evidence",
            "required_coverage",
            "parameters",
        }
        if not isinstance(spec, Mapping) or set(spec) - fields:
            raise CellIOError("CFG_ORACLE_SCHEMA", "unknown rule field")
        self.spec = dict(spec)
        self.id = spec.get("id")
        self.kind = spec.get("kind")
        if (
            not isinstance(self.id, str)
            or not self.id
            or self.kind
            not in {
                "invariant",
                "admission",
                "order",
                "response",
                "bounded_response_or_abort",
                "dwell",
            }
            or spec.get("category") not in {"task", "compliance", "coverage"}
            or spec.get("scope") not in {"episode", "operation", "after_event"}
        ):
            raise CellIOError(
                "CFG_ORACLE_SCHEMA", "invalid rule identity, category, scope, or kind"
            )
        self.predicate = registry.compile(spec.get("predicate"))
        self.applicable = (
            registry.compile(spec["applicability"])
            if "applicability" in spec
            else lambda b, e: True
        )
        self.trigger = spec.get("trigger")
        window = spec.get("window", {})
        self.lower, self.upper = (
            window.get("lower_steps", 0),
            window.get("upper_steps", 0),
        )
        self.dwell, self.grace = (
            spec.get("dwell_steps", 0),
            spec.get("abort_grace_steps", 0),
        )
        if (
            any(
                type(n) is not int or n < 0
                for n in (self.lower, self.upper, self.dwell, self.grace)
            )
            or self.lower > self.upper
            or self.grace
            and self.kind != "bounded_response_or_abort"
        ):
            raise CellIOError(
                "CFG_ORACLE_SCHEMA", "invalid closed window, dwell, or abort grace"
            )
        self.active = {}
        self.count = self.failed = self.unknown = 0
        self.first_fail = self.first_pass = None
        self.bad_scope = self.inv_unknown = self.inv_bad = False
        self.scoped = self.trigger is None
        self.last = -1
        self.first_reason = {}

    def _outcome(self, verdict, step, reason):
        self.first_reason.setdefault(verdict, reason)
        if verdict == Verdict.FAIL:
            self.failed += 1
            if self.first_fail is None:
                self.first_fail = step
        elif verdict == Verdict.UNKNOWN:
            self.unknown += 1
        elif self.first_pass is None:
            self.first_pass = step

    def feed(self, boundary):
        k = boundary.step
        if k != self.last + 1:
            raise CellIOError("CLOCK_ORDER", "noncontiguous oracle boundaries")
        self.last = k
        episode = {
            "event_id": "episode",
            "event_type": "episode",
            "job_id": 0,
            "operation_epoch": 0,
        }
        triggers = (
            [episode]
            if self.trigger is None and k == 0
            else [e for e in boundary.events if match(e, self.trigger)]
        )
        if self.kind == "invariant":
            self.scoped |= bool(triggers)
            if not self.scoped:
                return
            applies = self.applicable(boundary, episode)
            if applies is not True:
                self.bad_scope |= applies is None
                return
            self.count = 1
            value = self.predicate(boundary, episode)
            self.inv_unknown |= value is None
            if value is False and not self.inv_bad:
                self.inv_bad = True
                self._outcome(Verdict.FAIL, k, "INVARIANT_VIOLATION")
            return

        seen = set()
        for event in triggers:
            key = (event["event_id"], event.get("job_id", 0))
            if key in seen or key in self.active:
                continue
            seen.add(key)
            applies = self.applicable(boundary, event)
            if applies is not True:
                self.bad_scope |= applies is None
                continue
            self.count += 1
            if self.kind in ("order", "admission"):
                value = self.predicate(boundary, event)
                prefix = self.kind.upper()
                if value is None:
                    self._outcome(Verdict.UNKNOWN, k, "EVIDENCE_INCOMPLETE")
                elif value is True:
                    self._outcome(Verdict.PASS, k, prefix + "_SATISFIED")
                else:
                    self._outcome(Verdict.FAIL, k, prefix + "_VIOLATION")
            else:
                if len(self.active) >= 4096:
                    raise CellIOError(
                        "ORACLE_CAPACITY", "active obligation limit reached"
                    )
                self.active[key] = Obligation(event, k)

        closed = []
        for key, obligation in self.active.items():
            elapsed = k - obligation.start
            upper = min(self.upper, obligation.event.get("upper_steps", self.upper))
            good = False
            if self.lower <= elapsed <= upper:
                good = obligation.dwell(
                    k, self.predicate(boundary, obligation.event), self.dwell
                )
            if (
                self.kind == "bounded_response_or_abort"
                and elapsed <= upper + self.grace
            ):
                good |= any(
                    match(e, self.spec.get("abort_matcher"))
                    and e.get("job_id", 0) == obligation.event.get("job_id", 0)
                    and e.get("operation_epoch", 0)
                    == obligation.event.get("operation_epoch", 0)
                    for e in boundary.events
                )
            if good:
                self._outcome(Verdict.PASS, k, "RESPONSE_SATISFIED")
            elif elapsed >= upper + self.grace:
                if obligation.uncertain_witness:
                    self._outcome(Verdict.UNKNOWN, k, "EVIDENCE_INCOMPLETE")
                else:
                    self._outcome(Verdict.FAIL, k, "DEADLINE_VIOLATION")
            else:
                continue
            closed.append(key)
        for key in closed:
            del self.active[key]

    def finish(self, last, complete):
        if last != self.last:
            raise CellIOError("CLOCK_ORDER", "final boundary mismatch")
        unresolved = (
            self.unknown + len(self.active) + int(self.inv_unknown or self.bad_scope)
        )
        if self.failed:
            v, witness, why = (
                Verdict.FAIL,
                self.first_fail,
                self.first_reason[Verdict.FAIL],
            )
        elif (
            unresolved or not complete and (not self.count or self.kind == "invariant")
        ):
            v, witness = Verdict.UNKNOWN, None
            why = (
                "HORIZON_INCOMPLETE"
                if not complete or self.active
                else "EVIDENCE_INCOMPLETE"
            )
        elif not self.count:
            v, witness, why = Verdict.NOT_APPLICABLE, None, "NOT_TRIGGERED"
        elif self.kind == "invariant":
            v, witness, why = Verdict.PASS, last, "INVARIANT_SATISFIED"
        else:
            v, witness, why = (
                Verdict.PASS,
                self.first_pass,
                self.first_reason[Verdict.PASS],
            )
        return RequirementResult(
            self.id, v, witness, last, self.count, self.failed, unresolved, why
        )
