#!/usr/bin/env python3
"""The expectation ladder: the AI's own estimate (expect_min) is the clock.

Inside the estimate nothing fires; warm / yellow / red / overdue sit on ratios
of it.  Without an estimate the legacy yellow/red minutes still apply, and the
copy asks for a number instead of silently judging.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class EstimateLadderTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-est-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.core.WORK_START, self.core.WORK_END = "00:00", "23:59"
        self.sid = "est-1"
        self._health = core.work_health

    def tearDown(self):
        self.core.work_health = self._health
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    # -- helpers -----------------------------------------------------------
    def start_work(self, **extra):
        self.core.record_report(
            {"task_id": "est-work", "summary": "做点事", "phase": "start", **extra},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )

    def pretend(self, minutes, expect):
        self.core.work_health = lambda work_id=None: {
            "work_id": "est-work", "goal": "做点事", "minutes": minutes,
            "baseline": 30.0, "ratio": 0.5, "level": "green",
            "expect_min": expect, "expect_why": "", "expect_changes": 0,
        }

    def tier(self, minutes, expect):
        self.pretend(minutes, expect)
        return self.core.reminder_tier(self.core.work_health(), self.core.load_config())

    # -- ladder ------------------------------------------------------------
    def test_inside_the_estimate_nothing_fires(self):
        self.assertEqual(self.tier(20, 40), None)
        self.assertEqual(self.tier(31, 40), None)

    def test_warm_starts_at_eighty_percent(self):
        self.assertEqual(self.tier(32, 40), "warm")
        self.assertEqual(self.tier(39, 40), "warm")

    def test_yellow_after_the_estimate(self):
        self.assertEqual(self.tier(40, 40), "yellow")
        self.assertEqual(self.tier(59, 40), "yellow")

    def test_red_at_one_and_a_half(self):
        self.assertEqual(self.tier(60, 40), "red")
        self.assertEqual(self.tier(99, 40), "red")

    def test_overdue_at_two_and_a_half(self):
        self.assertEqual(self.tier(100, 40), "overdue")
        self.assertEqual(self.tier(600, 40), "overdue")

    def test_long_estimate_means_long_silence(self):
        # 3 小时的活，第 100 分钟连招呼都不用打
        self.assertEqual(self.tier(100, 180), None)
        self.assertEqual(self.tier(150, 180), "warm")

    def test_without_estimate_falls_back_to_yellow_minutes(self):
        self.assertEqual(self.tier(5, None), None)
        self.assertEqual(self.tier(12, None), "yellow")

    # -- payload -----------------------------------------------------------
    def test_yellow_reminder_is_recorded_with_its_tier(self):
        self.start_work(expect_min=40)
        self.pretend(48, 40)
        text = self.core.maybe_work_reminder(self.sid)
        self.assertIn("[crop]", text)
        self.assertIn("48", text)
        tiers = [e.get("tier") for e in self.core.load_events(sid=self.sid)
                 if e.get("ev") == "reminder"]
        self.assertEqual(tiers[-1], "yellow")

    def test_cooldown_is_shared_across_tiers(self):
        self.start_work(expect_min=40)
        self.pretend(33, 40)
        self.assertNotEqual(self.core.maybe_work_reminder(self.sid), "")
        # same tier right after: cooled down
        self.assertEqual(self.core.maybe_work_reminder(self.sid), "")
        # 档位升级不再绕过冷却：同一分钟内最多一条（卡片 82f42c50）
        self.pretend(120, 40)
        self.assertEqual(self.core.maybe_work_reminder(self.sid), "")

    def test_revision_restarts_the_window_instead_of_going_overdue(self):
        self.start_work(expect_min=40)
        now = self.core.now_ms()
        self.core.work_health = lambda work_id=None: {
            "work_id": "est-work", "goal": "做点事", "minutes": 25.0,
            "baseline": 30.0, "ratio": 0.8, "level": "yellow",
            "expect_min": 2.0, "expect_why": "", "expect_changes": 1,
            "expect_set_at": now, "expect_revised_at": now,
        }
        # 把预期改小后不该在同一分钟直接 overdue，先给一个冷静窗口
        self.assertEqual(self.core.maybe_work_reminder(self.sid), "")

    def test_report_returns_the_revision_for_the_tool_result(self):
        self.start_work(expect_min=40)
        record = self.core.record_report(
            {"task_id": "est-work", "summary": "改口", "phase": "progress", "expect_min": 60},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )
        self.assertEqual(record["estimate_revised"]["from"], 40.0)
        self.assertEqual(record["estimate_revised"]["to"], 60.0)
        self.assertEqual(record["estimate_revised"]["count"], 1)

    # -- estimate written, revised, counted --------------------------------
    def test_start_stores_the_estimate(self):
        self.start_work(expect_min=40, expect_why="先看清楚代码结构")
        task = self.core.load_open()["tasks"]["est-work"]
        self.assertEqual(task["expect_min"], 40.0)
        self.assertEqual(task["expect_why"], "先看清楚代码结构")

    def test_revision_is_logged_with_reason(self):
        self.start_work(expect_min=40)
        self.core.record_report(
            {"task_id": "est-work", "summary": "发现要重做数据层", "phase": "progress",
             "expect_min": 90, "cause": "数据层要重做"},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )
        task = self.core.load_open()["tasks"]["est-work"]
        self.assertEqual(task["expect_min"], 90.0)
        self.assertEqual(task["expect_history"][-1]["from"], 40.0)
        self.assertEqual(task["expect_history"][-1]["to"], 90.0)
        self.assertEqual(task["expect_history"][-1]["reason"], "数据层要重做")
        revisions = [e for e in self.core.load_events(sid=self.sid)
                     if e.get("ev") == "estimate_revised"]
        self.assertEqual(len(revisions), 1)

    def test_bad_estimate_is_refused(self):
        with self.assertRaises(ValueError):
            self.core.validate_report(
                {"phase": "start", "task_id": "x", "summary": "y", "expect_min": 0})
        with self.assertRaises(ValueError):
            self.core.validate_report(
                {"phase": "start", "task_id": "x", "summary": "y", "expect_min": "soon"})

    def test_status_line_shows_the_estimate(self):
        self.start_work(expect_min=40, goal="做点事")
        line = self.core.work_status_line("/tmp/ws", self.sid)
        self.assertIn("你报的 40 分钟", line)

    def test_status_line_nudges_for_a_missing_estimate(self):
        self.start_work(goal="做点事")
        line = self.core.work_status_line("/tmp/ws", self.sid)
        self.assertIn("还没报预期", line)

    # -- the tone travels with the first turn -------------------------------
    def test_first_turn_hint_carries_the_tone(self):
        text = self.core.format_start_hint([], 0, self.sid)
        self.assertIn("同事吐槽", text)
        self.assertIn("梗是包装", text)

    # -- miss nudge keeps its copy in the pool ------------------------------
    def test_miss_nudge_comes_from_the_pool(self):
        first = self.core.miss_nudge(1)
        second = self.core.miss_nudge(1)
        self.assertIn("[crop]", first)
        self.assertIn("crop_update", first)
        self.assertNotEqual(first, second, "two nudges in a row should not repeat")


if __name__ == "__main__":
    unittest.main()
