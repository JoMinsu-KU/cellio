"""Independent command/token and signal checks using the actual backend receipt."""
from .geometry import get


def usable(sample, expected, step):
    if sample is None:
        return None
    source = get(sample, "source_step")
    value = get(sample, "value")
    return (get(sample,"quality") == "GOOD" and get(sample,"dtype") == expected
            and type(value) is {"bool": bool, "int": int, "float": float}[expected]
            and type(source) is int and 0 <= step-source <= 12)


class CommandAudit:
    def __init__(self, generation):
        self.generation = generation
        self.epoch = 0
        self.seen = {}
        self.last_issued = -1
        self.issued = {}

    def issue(self, intent):
        ident = get(intent,"command_id")
        sequence = get(intent,"sequence")
        old = self.issued.get(ident)
        if old is not None:
            return old == intent
        self.issued[ident] = intent
        good = type(sequence) is int and sequence > self.last_issued
        if good:
            self.last_issued = sequence
        return good

    def apply(self, intent, receipt):
        if receipt is None:
            return None
        status = get(receipt,"status")
        if status in ("CANCELLED","STALE","SUPERSEDED","DUPLICATE","REJECTED"):
            return True
        if status != "APPLIED" or intent is None:
            return None
        ident = get(intent,"command_id")
        if get(intent,"generation") != self.generation or get(receipt,"generation") != self.generation:
            return False
        epoch = get(intent,"operation_epoch")
        payload = (get(intent,"actuator_id"),get(intent,"kind"),get(intent,"value"),epoch)
        if ident in self.seen:
            return self.seen[ident] == payload and self.epoch == epoch
        self.seen[ident] = payload
        if get(intent,"kind") == "CANCEL_OPERATION":
            if epoch != self.epoch + 1 or get(intent,"value") != self.epoch:
                return False
            self.epoch = epoch
            return True
        return epoch == self.epoch
