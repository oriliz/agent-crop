#!/usr/bin/env python3
"""Tests for the crop CLI status view (temp data dir, never touches real data)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class StatusViewTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-test-")
        self.env = dict(os.environ, KIT_DATA_DIR=self.data, KIT_HOST="test")
        os.environ.update({"KIT_DATA_DIR": self.data, "KIT_HOST": "test"})
        import probe_core as core
        self.core = core

    def tearDown(self):
        for name in ("KIT_DATA_DIR", "KIT_HOST"):
            os.environ.pop(name, None)

    def test_status_counts_today_events(self):
        core = self.core
        core.record_session_start("s1", "/tmp/ws")
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_tool("s1", "read", False)
        summary = core.status_summary("/tmp/ws")
        self.assertEqual(summary["today_events"], 3)
        self.assertEqual(summary["today_by_kind"].get("tool"), 1)
        self.assertGreaterEqual(summary["current_block_minutes"], 0)

    def test_format_has_header_and_tasks(self):
        core = self.core
        core.record_prompt("s1", "开工", "/tmp/ws")
        text = core.format_status(core.status_summary("/tmp/ws"))
        self.assertIn("Crop 状态", text)
        self.assertIn("今日事件", text)

    def test_cli_json_output(self):
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "crop"), "status", "--json"],
            capture_output=True, text=True, env=self.env, check=True,
        )
        payload = json.loads(out.stdout)
        self.assertIn("today_events", payload)
        self.assertIn("data_dir", payload)


if __name__ == "__main__":
    unittest.main()
