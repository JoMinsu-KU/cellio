"""Track collider pairs until a LOST event; normalize to semantic IDs at snapshot."""


class ContactPairs:
    def __init__(self, collider_ids):
        from omni.physx import get_physx_simulation_interface
        from omni.physx.bindings._physx import ContactEventType
        from pxr import PhysicsSchemaTools

        self.keys = collider_ids
        self.to_path = PhysicsSchemaTools.intToSdfPath
        self.lost = ContactEventType.CONTACT_LOST
        self.active = set()
        self.subscription = get_physx_simulation_interface().subscribe_contact_report_events(self._event)
        if self.subscription is None:
            raise RuntimeError("PhysX contact subscription unavailable")

    def _event(self, headers, data):
        for h in headers:
            pair = tuple(sorted((str(self.to_path(h.collider0)), str(self.to_path(h.collider1)))))
            if h.type == self.lost:
                self.active.discard(pair)
            else:
                self.active.add(pair)

    def snapshot(self):
        return tuple(sorted({tuple(sorted((self.keys[a], self.keys[b])))
                             for a, b in self.active if a in self.keys and b in self.keys}))

    def close(self):
        self.subscription = None
        self.active.clear()
