from __future__ import annotations

import unittest

from hub.timeseries.base import extract_scalars


class TelemetryScalarTest(unittest.TestCase):
    def test_container_update_count(self) -> None:
        values = extract_scalars(
            {
                "containers": [
                    {"state": "running", "update_available": True},
                    {"state": "running", "update_available": False},
                    {"state": "exited"},
                ]
            }
        )
        self.assertEqual(values["containers_total"], 3.0)
        self.assertEqual(values["containers_running"], 2.0)
        self.assertEqual(values["container_updates_available"], 1.0)
