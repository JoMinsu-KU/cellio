"""Validated intent delivery, independent of physical actuation and task completion."""

from collections import Counter
from dataclasses import replace
from math import isfinite

from .models import (
    CellIOError,
    CommandKind as Kind,
    DeliveryDecision,
    PublicCommandReceipt,
    quaternion,
    uint,
)


NORMAL = {Kind.POSE, Kind.BOOL, Kind.JOINT_POSITION, Kind.GRIPPER_GAP}


def _safe_stop(intent):
    return intent.kind == Kind.HOLD or intent.kind == Kind.BOOL and intent.value is False


class CommandRouter:
    def __init__(
        self, cell_id, attempt_id, episode_id, generation, *, queue_limit=256, intent_limit=4096
    ):
        if cell_id not in {"cell_a", "cell_b"}:
            raise CellIOError("CFG_SCENE", cell_id)
        self.cell_id = cell_id
        self.attempt_id, self.episode_id, self.generation = attempt_id, episode_id, generation
        self.queue_limit, self.intent_limit = queue_limit, intent_limit
        self.epoch = 0
        self.cancelled = False
        self.desired, self.effective = {}, {}
        self._seen, self._original = {}, {}
        self._pending = []
        self._sequence = -1
        self._general_count = 0
        self._last_step = -1
        self._receipts = []
        self._applied = set()
        self._dispatched = {}

    def _validate(self, intent, k):
        key, kind, v = intent.actuator_id, intent.kind, intent.value
        uint(intent.sequence, "sequence")
        uint(intent.operation_epoch, "operation_epoch")
        if intent.command_id != f"{self.attempt_id}:cmd:{intent.sequence}":
            raise CellIOError(
                "CTRL_COMMAND_CONFLICT", "command ID does not match attempt and sequence"
            )
        if (
            intent.issued_step != k
            or type(intent.issued_step) is not int
            or type(intent.job_id) is not int
            or intent.job_id != 0
        ):
            raise CellIOError("CTRL_COMMAND_CONFLICT", "unexpected issue step or job")
        kinds = {
            "actuator.robot": {Kind.POSE, Kind.HOLD},
            "actuator.gripper": {Kind.GRIPPER_GAP, Kind.HOLD},
            "control.operation": {Kind.CANCEL_OPERATION},
        }
        if self.cell_id == "cell_a":
            kinds["actuator.belt"] = {Kind.BOOL}
        else:
            kinds.update(
                {
                    "actuator.clamp": {Kind.JOINT_POSITION, Kind.HOLD},
                    "actuator.process": {Kind.BOOL},
                }
            )
        if kind not in kinds.get(key, ()):
            raise CellIOError("CTRL_COMMAND_KIND", key)
        if kind == Kind.POSE:
            valid = (
                isinstance(v, tuple)
                and len(v) == 7
                and all(type(n) in (int, float) and isfinite(n) for n in v)
            )
            if valid:
                valid = all(
                    lo <= n <= hi
                    for n, (lo, hi) in zip(
                        v[:3], ((-0.2, 0.8), (-0.55, 0.55), (0.79, 1.3)), strict=True
                    )
                )
                try:
                    normalized = quaternion(v[3:])
                except CellIOError:
                    valid = False
                if valid and normalized != v[3:]:
                    return replace(intent, value=(*v[:3], *normalized))
        elif kind == Kind.HOLD:
            valid = v == ()
        elif kind == Kind.CANCEL_OPERATION:
            valid = type(v) is int and v >= 0
        elif kind == Kind.BOOL:
            valid = type(v) is bool
        else:
            lo, hi = (0, 0.08) if kind == Kind.GRIPPER_GAP else (-0.07, 0)
            valid = type(v) is float and isfinite(v) and lo <= v <= hi
        if not valid:
            raise CellIOError("CTRL_COMMAND_RANGE", key)
        return intent

    @staticmethod
    def _decision(intent, channel, due, status, step=None, reason=None):
        return DeliveryDecision(intent, channel, due, step, status, reason)

    def resolve(self, intents, faults, tick):
        k = tick.step
        if (tick.episode_id, tick.generation) != (
            self.episode_id,
            self.generation,
        ) or k != self._last_step + 1:
            raise CellIOError("CLOCK_ORDER", "invalid command tick")
        decisions = []
        new = []
        seen_this_tick = {}
        sequence = self._sequence
        channel = faults.channel_generation
        intents = tuple(intents)
        # An epoch can advance only through a same-generation cancellation. Stale packets
        # must never consume sequence numbers or disturb duplicate detection in the live epoch.
        proposed_cancel = any(
            i.kind == Kind.CANCEL_OPERATION and i.episode_id == self.episode_id
            and i.generation == self.generation and i.value == self.epoch
            and i.operation_epoch == self.epoch+1 for i in intents
        )
        eligible_epoch = self.epoch + int(proposed_cancel)
        for original in intents:
            if (original.episode_id, original.generation) != (self.episode_id, self.generation):
                decisions.append(self._decision(original, channel, k, "STALE", reason="GENERATION"))
                continue
            if original.kind != Kind.CANCEL_OPERATION and original.operation_epoch != eligible_epoch:
                decisions.append(self._decision(original, channel, k, "STALE", reason="OPERATION_EPOCH"))
                continue
            previous = self._original.get(
                original.command_id, seen_this_tick.get(original.command_id)
            )
            if previous is not None:
                if previous != original:
                    raise CellIOError("CTRL_COMMAND_CONFLICT", original.command_id)
                decisions.append(
                    self._decision(original, channel, k, "DUPLICATE", reason="RETRANSMISSION")
                )
                continue
            if original.sequence <= sequence or (sequence == -1 and original.sequence != 0):
                raise CellIOError("CTRL_COMMAND_CONFLICT", "nonmonotonic sequence")
            valid = self._validate(original, k)
            sequence = valid.sequence
            seen_this_tick[valid.command_id] = original
            new.append(valid)
        counts = Counter(i.actuator_id for i in new if i.kind in NORMAL)
        if any(n > 1 for n in counts.values()):
            raise CellIOError("CTRL_DUPLICATE_COMMAND", "same endpoint in one output")
        cancels = [i for i in new if i.kind == Kind.CANCEL_OPERATION]
        next_epoch = self.epoch
        if cancels:
            if len(cancels) != 1 or (cancels[0].value, cancels[0].operation_epoch) != (
                self.epoch,
                self.epoch + 1,
            ):
                raise CellIOError("CTRL_OPERATION_EPOCH", "invalid cancellation")
            next_epoch += 1
        valid_new = [i for i in new if i.operation_epoch == next_epoch]
        if (self.cancelled or cancels) and any(
            i.kind != Kind.CANCEL_OPERATION and not _safe_stop(i) for i in valid_new
        ):
            raise CellIOError("CTRL_OPERATION_EPOCH", "new work after cancellation")
        general_count = sum(i.kind in NORMAL for i in valid_new)
        if self._general_count + general_count > self.intent_limit:
            raise CellIOError("QUEUE_OVERFLOW", "episode intent limit")
        target = faults.spec.target_id if faults.spec else None
        ending = faults.ended_at("F4", k)
        if faults.active_for("F4") and not cancels:
            new_count = sum(i.kind in NORMAL and i.actuator_id == target for i in valid_new)
            if len(self._pending) + new_count > self.queue_limit:
                raise CellIOError("QUEUE_OVERFLOW", target)
        self._last_step, self._sequence, self.epoch = k, sequence, next_epoch
        self._general_count += general_count
        self._original.update(seen_this_tick)
        self._seen.update({i.command_id: i for i in new})
        for i in new:
            if i.operation_epoch != next_epoch:
                decisions.append(self._decision(i, channel, k, "STALE", reason="OPERATION_EPOCH"))
        flushed_due = {}
        if cancels or ending:
            for d in self._pending:
                reason = "CANCEL_OPERATION" if cancels else "RESTORE_FLUSH"
                decisions.append(replace(d, status="CANCELLED", reason=reason))
                flushed_due[d.intent.command_id] = d.due_step
                if cancels:
                    self._receipts.append(
                        PublicCommandReceipt(
                            d.intent.command_id,
                            d.intent.operation_epoch,
                            d.intent.actuator_id,
                            "CANCELLED",
                            k,
                            "CANCELLED",
                        )
                    )
            self._pending.clear()
        if cancels:
            self.cancelled = True
            self.effective.clear()
            self.desired.clear()
        ready = []
        for i in sorted(valid_new, key=lambda i: (i.kind != Kind.CANCEL_OPERATION, i.sequence)):
            self._receipts.append(
                PublicCommandReceipt(
                    i.command_id, i.operation_epoch, i.actuator_id, "ACCEPTED", k, None
                )
            )
            if i.kind != Kind.CANCEL_OPERATION:
                self.desired[i.actuator_id] = i
            if faults.active_for("F4", i.actuator_id) and i.kind in NORMAL and not self.cancelled:
                delayed = self._decision(
                    i, channel, k + faults.spec.parameters["delay_steps"], "QUEUED"
                )
                self._pending.append(delayed)
                decisions.append(delayed)
            else:
                ready.append(self._decision(i, channel, k, "DELIVERED", k))
        if ending and not cancels:
            desired = self.desired.get(target)
            if (
                desired
                and self.effective.get(target) != desired
                and all(d.intent.actuator_id != target for d in ready)
            ):
                ready.append(
                    self._decision(
                        desired,
                        channel,
                        flushed_due.get(desired.command_id, k),
                        "DELIVERED",
                        k,
                        "RESTORE_RESUME",
                    )
                )
        waiting = []
        for d in self._pending:
            if d.channel_generation != channel or d.intent.operation_epoch != self.epoch:
                raise CellIOError("QUEUE_STALE", d.intent.command_id)
            if d.due_step <= k:
                ready.append(replace(d, status="DELIVERED", delivery_step=k))
            else:
                waiting.append(d)
        self._pending = waiting
        latest = {
            d.intent.actuator_id: d.intent.sequence
            for d in sorted(ready, key=lambda d: d.intent.sequence)
        }
        for d in sorted(
            ready,
            key=lambda d: (d.intent.kind != Kind.CANCEL_OPERATION, d.due_step, d.intent.sequence),
        ):
            if d.intent.sequence != latest[d.intent.actuator_id]:
                decisions.append(
                    replace(d, delivery_step=None, status="SUPERSEDED", reason="NEWER_SEQUENCE")
                )
            else:
                if d.intent.kind != Kind.CANCEL_OPERATION:
                    self.effective[d.intent.actuator_id] = d.intent
                decisions.append(d)
        self._dispatched = {d.intent.command_id:d for d in decisions if d.status=="DELIVERED"}
        return tuple(decisions)

    def acknowledge(self, receipt):
        if (receipt.tick.episode_id, receipt.tick.generation, receipt.tick.step) != (
            self.episode_id, self.generation, self._last_step
        ):
            raise CellIOError("SIM_ACTUATION_FAILED", "receipt from another episode or tick")
        for cmd in receipt.applied_commands:
            intent = self._seen.get(cmd.command_id)
            dispatched = self._dispatched.get(cmd.command_id)
            if (not intent or dispatched is None or cmd.generation != self.generation or
                cmd.actuator_id != intent.actuator_id or cmd.issued_step != intent.issued_step or
                cmd.due_step != dispatched.due_step):
                raise CellIOError("SIM_ACTUATION_FAILED", "backend receipt does not match a delivered command")
            if cmd.command_id in self._applied:
                raise CellIOError("SIM_ACTUATION_FAILED", "duplicate backend application")
            if cmd.status == "APPLIED" and cmd.applied_step == receipt.tick.step:
                status, reason = "APPLIED", None
                self._applied.add(cmd.command_id)
            elif cmd.status in {"BACKEND_ERROR", "REJECTED"}:
                status, reason = "REJECTED", "UNAVAILABLE"
            elif cmd.status == "CANCELLED":
                status, reason = "CANCELLED", "CANCELLED"
            else:
                raise CellIOError("SIM_ACTUATION_FAILED", "invalid backend acknowledgement")
            self._receipts.append(
                PublicCommandReceipt(
                    cmd.command_id,
                    intent.operation_epoch,
                    cmd.actuator_id,
                    status,
                    receipt.tick.step,
                    reason,
                )
            )
        if not receipt.accepted:
            raise CellIOError("SIM_ACTUATION_FAILED", "backend rejected actuation")

    def public_receipts(self, tick):
        ready = tuple(r for r in self._receipts if r.receipt_step < tick.step)
        self._receipts = [r for r in self._receipts if r.receipt_step >= tick.step]
        return ready

    @property
    def pending_count(self):
        return len(self._pending)
