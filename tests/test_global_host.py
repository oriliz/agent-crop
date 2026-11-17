"""Global page host dimension (issue #42)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import probe_core  # noqa: E402


class GlobalHostTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"KIT_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        for host, minutes in (("dsh", 10), ("qoder", 5)):
            probe_core.append_event({"ev": "stop", "sid": "s1", "host": host,
                                     "minutes": minutes, "t": probe_core.now_ms()})

    def test_blocks_are_split_by_host(self):
        both = probe_core.improvement_summary(7)
        dsh = probe_core.improvement_summary(7, host="dsh")
        qoder = probe_core.improvement_summary(7, host="qoder")
        self.assertEqual(both["current"]["blocks"], 2)
        self.assertEqual(dsh["current"]["blocks"], 1)
        self.assertEqual(qoder["current"]["blocks"], 1)
        self.assertEqual(dsh["current"]["total_minutes"], 10.0)
        self.assertEqual(qoder["current"]["total_minutes"], 5.0)

    def test_hosts_list_ignores_the_filter(self):
        both = probe_core.improvement_summary(7)
        dsh = probe_core.improvement_summary(7, host="dsh")
        self.assertEqual(both["hosts"], ["dsh", "qoder"])
        self.assertEqual(dsh["hosts"], ["dsh", "qoder"])
        self.assertEqual(dsh["host"], "dsh")


if __name__ == "__main__":
    unittest.main()
