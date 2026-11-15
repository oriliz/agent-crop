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
        self.assertIn("把探针开源", line)
        # 只有一条活跃工作时不存在“选哪个”，直接继承（例如重启/换会话）
        self.assertIn("上个会话认领", line)
        self.assertNotIn("哪个属于本会话", line)

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
        # 自己认领的，不提示认领
        self.assertNotIn("认领", line)
        # 别的会话看到的是“上个会话认领”，不是认领提示
        other = core.work_status_line("/tmp/ws", "s2")
        self.assertIn("上个会话认领", other)
        self.assertNotIn("哪个属于本会话", other)

    def test_work_status_line_lists_candidates_when_ambiguous(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "a1", "summary": "甲", "goal": "甲"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        core.record_report({"phase": "start", "task_id": "a2", "summary": "乙", "goal": "乙"},
                           {"sid": "s2", "workspace": "/tmp/ws"})
        line = core.work_status_line("/tmp/ws", "s3")
        self.assertIn("未完成的工作", line)
        self.assertIn("a1", line)
        self.assertIn("a2", line)
        self.assertIn("哪个属于本会话", line)

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

    def test_start_hint_requires_binding(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t1", "summary": "做设计", "goal": "甲"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        hint = core.format_start_hint(core.open_tasks("/tmp/ws", 3))
        self.assertIn("绑定", hint)
        self.assertIn("crop switch", hint)
        self.assertIn("crop switch t1", hint)

    def test_side_issue_report_does_not_create_task(self):
        core = self.core
        core.record_report({"phase": "progress", "task_id": "side-x",
                            "summary": "支线：mermaid 渲染", "category": "side-issue"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        self.assertNotIn("side-x", core.load_open()["tasks"])
        events = [e for e in core.load_events() if e.get("ev") == "side_issue"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["note"], "支线：mermaid 渲染")

    def test_side_issue_does_not_change_claim(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "a", "summary": "甲", "goal": "甲"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        self.assertTrue(core.record_side_issue("DSH 不渲染 mermaid", "later"))
        events = [e for e in core.load_events() if e.get("ev") == "side_issue"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["suggest"], "later")
        # 记录支线不改变当前工作
        self.assertIn("甲", core.work_status_line("/tmp/ws", "s1"))

    def test_suspended_task_is_hidden_from_open(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "t1", "summary": "做设计", "goal": "暂停我"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        self.assertTrue(core.set_work_state("t1", "suspended"))
        ids = [t["task_id"] for t in core.open_tasks("/tmp/ws", 5)]
        self.assertNotIn("t1", ids)

    def test_switch_moves_focus_without_state_change(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "a", "summary": "甲", "goal": "甲"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        core.record_report({"phase": "start", "task_id": "b", "summary": "乙", "goal": "乙"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        self.assertIn("乙", core.work_status_line("/tmp/ws", "s1"))
        self.assertTrue(core.switch_claim("a", "s1"))
        self.assertIn("甲", core.work_status_line("/tmp/ws", "s1"))
        tasks = core.load_open()["tasks"]
        self.assertEqual(tasks["a"].get("state", "open"), "open")
        self.assertEqual(tasks["b"].get("state", "open"), "open")

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

    def test_work_level_reminder_fires_past_sixty_percent(self):
        core = self.core
        core.record_report({"phase": "start", "task_id": "w9", "summary": "慢活", "goal": "慢活"},
                           {"sid": "s1", "workspace": "/tmp/ws"})
        original = core.work_health
        core.work_health = lambda work_id=None: {
            "work_id": "w9", "goal": "慢活", "minutes": 25.0,
            "baseline": 30.0, "ratio": 0.83, "level": "yellow",
        }
        try:
            text = core.maybe_work_reminder("s1")
        finally:
            core.work_health = original
        self.assertIn("[crop]", text)
        self.assertIn("verdict", text)

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


    def test_block_closes_into_claimed_work(self):
        core = self.core
        core.record_report(
            {"phase": "start", "task_id": "t1", "summary": "做面板"},
            {"sid": "s1", "workspace": "/tmp/ws"},
        )
        core.record_prompt("s1", "开工", "/tmp/ws")
        start = core.current_block_start("s1")
        core.record_stop("s1")
        links = [e for e in core.load_events() if e.get("ev") == "task_link" and e.get("block_start") == start]
        self.assertEqual([e["task_id"] for e in links], ["t1"])
        stop = [e for e in core.load_events() if e.get("ev") == "stop"][-1]
        self.assertEqual(stop.get("work_id"), "t1")

    def test_wait_is_attributed_to_previous_work(self):
        core = self.core
        core.record_report(
            {"phase": "start", "task_id": "t1", "summary": "做面板"},
            {"sid": "s1", "workspace": "/tmp/ws"},
        )
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_stop("s1")
        core.record_prompt("s1", "继续", "/tmp/ws")
        waits = [e for e in core.load_events(sid="s1") if e.get("ev") == "user_wait"]
        self.assertEqual(waits[-1].get("work_id"), "t1")

    def test_one_block_belongs_to_last_linked_work(self):
        core = self.core
        block_start = 1700000000000
        core.append_jsonl(
            os.path.join(core.paths()["blocks_dir"], core.today() + ".jsonl"),
            {"start": block_start, "end": block_start + 600000, "minutes": 10.0},
        )
        core.link_task("old", block_start)
        core.link_task("new", block_start)
        self.assertEqual(core.work_minutes("old"), 0.0)
        self.assertEqual(core.work_minutes("new"), 10.0)

    def test_period_summary_reports_active_and_completed(self):
        core = self.core
        core.record_report(
            {"phase": "start", "task_id": "t1", "summary": "做面板"},
            {"sid": "s1", "workspace": "/tmp/ws"},
        )
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_stop("s1")
        core.record_report(
            {"phase": "end", "task_id": "t1", "summary": "面板收尾"},
            {"sid": "s1", "workspace": "/tmp/ws"},
        )
        summary = core.period_summary(7, start=0)
        self.assertEqual(summary["completed"], 1)
        self.assertAlmostEqual(
            summary["active_minutes"],
            round(summary["total_minutes"] - summary["wait_minutes"], 1),
        )
        self.assertTrue(any(row["work_id"] == "t1" for row in summary["work_list"]))

    def test_daily_breakdown_splits_active_and_wait(self):
        core = self.core
        core.record_prompt("s1", "开工", "/tmp/ws")
        core.record_stop("s1")
        core.record_prompt("s1", "继续", "/tmp/ws")
        rows = core.daily_breakdown(1, start=0)
        self.assertTrue(rows)
        self.assertIn("wait_minutes", rows[-1])
        self.assertIn("active_minutes", rows[-1])

    def test_natural_day_window_starts_at_midnight(self):
        import crop_serve
        start = crop_serve._range_start(7)
        self.assertEqual(crop_serve._range_start(1), self.core.start_of_today_ms())
        self.assertEqual(start, self.core.start_of_today_ms() - 6 * 86400000)

    def test_block_start_millisecond_offset_still_counts(self):
        core = self.core
        block_start = 1700000000000
        core.append_jsonl(
            os.path.join(core.paths()["blocks_dir"], core.today() + ".jsonl"),
            {"start": block_start, "end": block_start + 600000, "minutes": 10.0},
        )
        # DSH host reports a block_start a few ms off the Python prompt timestamp
        core.link_task("t1", block_start + 33)
        self.assertEqual(core.work_minutes("t1"), 10.0)

    def test_offset_duplicates_count_once(self):
        core = self.core
        block_start = 1700000000000
        core.append_jsonl(
            os.path.join(core.paths()["blocks_dir"], core.today() + ".jsonl"),
            {"start": block_start, "end": block_start + 600000, "minutes": 10.0},
        )
        core.link_task("t1", block_start)
        core.link_task("t1", block_start + 33)
        self.assertEqual(core.work_minutes("t1"), 10.0)


if __name__ == "__main__":
    unittest.main()
