"""Per-day health data behind the trend sparkline (#58)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import probe_core  # noqa: E402


class HealthDailyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"KIT_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        now = probe_core.now_ms()
        for work_id in ("w1", "w2", None):
            probe_core.append_event({"ev": "tool", "sid": "s1", "name": "bash",
                                     "is_error": False, "work_id": work_id, "t": now})

    def test_call_coverage_counts_work_id_per_day(self):
        rows = [event for event in probe_core.load_events() if event.get("ev") == "tool"]
        now = probe_core.now_ms()
        daily = probe_core._health_daily(rows, now - 86400000, now, "call", (), 2)
        last = daily[-1]
        self.assertEqual(last["events"], 3)
        self.assertAlmostEqual(last["coverage"], 2 / 3, places=3)
        self.assertAlmostEqual(last["completeness"], 1.0, places=3)

    def test_non_call_class_has_no_coverage(self):
        rows = [event for event in probe_core.load_events() if event.get("ev") == "tool"]
        now = probe_core.now_ms()
        daily = probe_core._health_daily(rows, now - 86400000, now, "session", (), 2)
        self.assertIsNone(daily[-1]["coverage"])
        self.assertIsNotNone(daily[-1]["completeness"])

    def test_empty_day_is_explicit(self):
        rows = []
        now = probe_core.now_ms()
        daily = probe_core._health_daily(rows, now - 86400000, now, "call", (), 2)
        self.assertEqual(daily[-1]["events"], 0)
        self.assertIsNone(daily[-1]["coverage"])


if __name__ == "__main__":
    unittest.main()
