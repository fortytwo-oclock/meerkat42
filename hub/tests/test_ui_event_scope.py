from __future__ import annotations

import unittest

from hub.deps import Principal
from hub.models.auth import Role
from hub.ws.ui import _event_visible


class UiEventScopeTest(unittest.TestCase):
    def test_owner_only_sees_own_tenant(self) -> None:
        owner = Principal(Role.OWNER, "tenant-a", None, "owner")
        self.assertTrue(_event_visible(owner, {"tenant_id": "tenant-a", "group_id": "g-1"}))
        self.assertFalse(_event_visible(owner, {"tenant_id": "tenant-b", "group_id": "g-1"}))
        self.assertFalse(_event_visible(owner, {"node_id": "legacy-unscoped"}))

    def test_scoped_viewer_only_sees_its_group(self) -> None:
        viewer = Principal(Role.VIEWER, "tenant-a", "g-2", "viewer")
        self.assertTrue(_event_visible(viewer, {"tenant_id": "tenant-a", "group_id": "g-2"}))
        self.assertFalse(_event_visible(viewer, {"tenant_id": "tenant-a", "group_id": "g-1"}))
        self.assertFalse(_event_visible(viewer, {"tenant_id": "tenant-b", "group_id": "g-2"}))


if __name__ == "__main__":
    unittest.main()
