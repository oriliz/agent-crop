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
        # Tests must not depend on the wall clock: treat all hours as work hours.
        self._saved = (core.WORK_START, core.WORK_END, core.IDLE_MIN)
        core.WORK_START, core.WORK_END = "00:00", "23:59"

    def tearDown(self):
        self.core.WORK_START, self.core.WORK_END, self.core.IDLE_MIN = self._saved
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

    def test_user_wait_after_short_gap(self):
        core = self.core
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_stop("s1")
        core.record_prompt("s1", "继续", "/tmp/ws")
        kinds = [event["ev"] for event in core.load_events(sid="s1")]
        self.assertIn("user_wait", kinds)

    def test_idle_gap_when_gap_exceeds_threshold(self):
        core = self.core
        previous = core.IDLE_MIN
        core.IDLE_MIN = -1.0  # any non-negative gap exceeds the threshold
        try:
            core.record_prompt("s1", "开工", "/tmp/ws")
            core.record_stop("s1")
            core.record_prompt("s1", "继续", "/tmp/ws")
            kinds = [event["ev"] for event in core.load_events(sid="s1")]
            self.assertIn("idle_gap", kinds)
        finally:
            core.IDLE_MIN = previous

    def test_work_status_line_has_goal(self):
        core = self.core
        core.record_report(
            {"phase": "start", "task_id": "t1", "summary": "做设计",
             "goal": "把探针开源", "type": "design", "difficulty": "m"},
            {"sid": "s1", "workspace": "/tmp/ws"},
        )
        line = core.work_status_line("/tmp/ws")
        self.assertIn("[crop]", line)
        self.assertIn("t1", line)
        # 未认领时只能列候选，不能自称“当前工作”
        self.assertNotIn("当前工作", line)
        self.assertIn("认领", line)

    def test_record_report_syncs_runtime_sid(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t1", "summary": "做设计", "goal": "把探针开源"},
                           {"sid": "s-fork", "workspace": "/tmp/ws"})
        self.assertEqual(core.read_runtime().get("sid"), "s-fork")

    def test_work_status_line_reflects_claim(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t1", "summary": "做设计", "goal": "把探针开源"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        line = core.work_status_line("/tmp/ws", "s1")
        self.assertIn("当前工作", line)
        self.assertIn("把探针开源", line)
        # 认领之后不再劝它认领
        self.assertNotIn("认领", line)
        # 别的会话看到的是候选列表，不是别人的“当前工作”
        other = core.work_status_line("/tmp/ws", "s2")
        self.assertNotIn("当前工作", other)
        self.assertIn("认领", other)

    def test_work_status_line_prefers_recent(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t-old", "summary": "旧任务", "goal": "旧目标"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        core.record_report({"phase": "start", "task_id": "t-new", "summary": "新任务", "goal": "新目标"},
                           {"sid": "s2", "workspace": "/tmp/ws"})
        value = core.load_open()
        value["tasks"]["t-old"]["last_seen_at"] = 1000
        value["tasks"]["t-new"]["last_seen_at"] = 2000
        core.save_open(value)
        line = core.work_status_line("/tmp/ws")
        self.assertLess(line.index("t-new"), line.index("t-old"))

    def test_work_status_line_prefers_same_session(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t-old", "summary": "旧任务", "goal": "旧目标"},
                           {"sid": "s-me", "workspace": "/tmp/ws"})
        core.record_report({"phase": "start", "task_id": "t-new", "summary": "新任务", "goal": "新目标"},
                           {"sid": "s-other", "workspace": "/tmp/ws"})
        value = core.load_open()
        value["tasks"]["t-old"]["last_seen_at"] = 1000
        value["tasks"]["t-new"]["last_seen_at"] = 2000
        core.save_open(value)
        self.assertIn("旧目标", core.work_status_line("/tmp/ws", "s-me"))

    def test_suspend_resume_archive(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "w1", "summary": "做设计", "goal": "目标"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        self.assertTrue(core.set_work_state("w1", "suspended"))
        self.assertEqual(core.load_open()["tasks"]["w1"]["state"], "suspended")
        self.assertTrue(core.set_work_state("w1", "open"))
        self.assertTrue(core.archive_work("w1"))
        self.assertNotIn("w1", core.load_open()["tasks"])
        archived = os.path.join(self.data, "tasks", "archive")
        files = [name for name in os.listdir(archived)] if os.path.isdir(archived) else []
        self.assertTrue(files)

    def test_work_summary_written_on_end(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "w2", "summary": "做设计"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        core.record_report({"phase": "end", "task_id": "w2", "summary": "完成",
                            "what": "写了设计", "cost": "查资料", "next_time": "先定目录"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        kinds = [event["ev"] for event in core.load_events()]
        self.assertIn("work_summary", kinds)

    def test_gc_archives_stale_suspended(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "w3", "summary": "旧活"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        core.set_work_state("w3", "suspended")
        value = core.load_open()
        value["tasks"]["w3"]["state_changed_at"] = 0
        core.save_open(value)
        archived = core.archive_stale(14.0)
        self.assertIn("w3", archived)
        self.assertNotIn("w3", core.load_open()["tasks"])

    def test_period_summary_counts_blocks(self):
        core = self.core
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_stop("s1")
        summary = core.period_summary(1)
        self.assertGreaterEqual(summary["blocks"], 1)
        self.assertIn("total_minutes", summary)
        self.assertTrue(core.format_report(summary).startswith("Crop 周报"))

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
