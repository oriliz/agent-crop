#!/usr/bin/env python3
"""改进台账：AI 只交声明与主观判断，数值由事件派生、状态由规则算。"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class AdjustmentTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-adj-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.core.WORK_START, self.core.WORK_END = "00:00", "23:59"

    def tearDown(self):
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    def record(self, **extra):
        args = {"metric": "work_minutes",
                "evidence": "PR #29 改了收尾路径",
                "note": "少一轮提醒，同类工作应该更快收尾"}
        args.update(extra)
        return self.core.record_adjustment(args, {"sid": "adj-s"})

    def ledger_with(self, adopted_at, values):
        """values[metric] = ((基线值, 基线样本), (当前值, 当前样本))"""
        self.record(adopted_at=adopted_at)

        def fake(metric, start, end, work_type=""):
            before, after = values.get(metric, ((None, 0), (None, 0)))
            return before if end <= adopted_at else after

        old = self.core._metric_value
        self.core._metric_value = fake
        try:
            return self.core.improvement_ledger()
        finally:
            self.core._metric_value = old

    # -- 写入：只收声明与主观判断 -----------------------------------------
    def test_record_requires_a_known_metric(self):
        with self.assertRaises(ValueError):
            self.record(metric="whatever_i_like")

    def test_record_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.record(evidence="")

    def test_record_requires_a_subjective_note(self):
        with self.assertRaises(ValueError):
            self.record(note="")

    def test_event_stores_no_numbers(self):
        event = self.record()
        self.assertNotIn("before", event)
        self.assertNotIn("after", event)
        self.assertEqual(event["direction"], "lower")
        self.assertEqual(event["note"], "少一轮提醒，同类工作应该更快收尾")

    # -- 判定：数值从事件派生 ---------------------------------------------
    def test_ledger_derives_baseline_and_current(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((40.0, 30), (30.0, 30)),
        })
        row = ledger["rows"][0]
        self.assertEqual(row["before"], 40.0)
        self.assertEqual(row["after"], 30.0)
        self.assertEqual(row["samples"], {"before": 120, "after": 150})
        self.assertEqual(row["status"], "met")

    def test_small_sample_only_reports_insufficient(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((40.0, 2), (30.0, 2)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "insufficient")

    def test_guardrail_worse_is_not_an_improvement(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((40.0, 30), (30.0, 30)),
            "reminder_per_turn": ((0.5, 30), (1.0, 30)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "guardrail")

    def test_single_week_only_counts_as_improving(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 10 * 86400000, {
            "work_minutes": ((40.0, 30), (30.0, 30)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "improving")

    def test_partial_week_does_not_drive_the_verdict(self):
        # #31：判定只看最后一个「完整周」。尾部不满一周的桶样本少，
        # 哪怕数字很夸张也不能把结论从 improving 翻成 not-improved。
        now = self.core.now_ms()
        adopted = now - 10 * 86400000  # 1 个完整周 + 3 天尾巴
        self.record(adopted_at=adopted)

        def fake(metric, start, end, work_type=""):
            if end <= adopted:
                return (40.0, 25)  # 基线：4 个完整周，每周 40 分钟
            if end - start < 7 * 86400000 - 1:
                return (999.0, 1)  # 部分周噪音：只有 1 个样本
            return (30.0, 25)  # 完整周：确实降到 30 分钟

        old = self.core._metric_value
        self.core._metric_value = fake
        try:
            ledger = self.core.improvement_ledger()
        finally:
            self.core._metric_value = old
        row = ledger["rows"][0]
        self.assertEqual(row["weeks"]["complete_after"], 1)
        self.assertEqual(row["after"], 30.0)
        self.assertEqual(row["status"], "improving")

    def test_fresh_adjustment_is_observing(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 2 * 86400000, {
            "work_minutes": ((40.0, 30), (None, 0)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "observing")

    def test_no_baseline_is_stated_not_judged(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((None, 0), (30.0, 30)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "no-baseline")

    def test_flat_metric_is_not_improved(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((40.0, 30), (40.0, 30)),
        })
        self.assertEqual(ledger["rows"][0]["status"], "not-improved")

    def test_format_ledger_shows_baseline_current_and_hypothesis(self):
        now = self.core.now_ms()
        ledger = self.ledger_with(now - 30 * 86400000, {
            "work_minutes": ((40.0, 30), (30.0, 30)),
        })
        text = self.core.format_ledger(ledger)
        self.assertIn("基线 40.0", text)
        self.assertIn("当前 30.0", text)
        self.assertIn("假设", text)
        self.assertIn("证据", text)


class BindingMetricTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-bind-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.core.WORK_START, self.core.WORK_END = "00:00", "23:59"

    def tearDown(self):
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    def test_early_tools_before_binding_are_measured(self):
        core = self.core
        core.record_prompt("sid-a", "干活", "/tmp/ws")
        core.record_tool("sid-a", "Read", False)
        core.record_prompt("sid-a", "继续", "/tmp/ws")
        core.record_report({"task_id": "a-work", "summary": "做", "phase": "start"},
                           {"sid": "sid-a", "workspace": "/tmp/ws"})
        core.record_prompt("sid-b", "干活", "/tmp/ws")
        core.record_report({"task_id": "b-work", "summary": "做", "phase": "start"},
                           {"sid": "sid-b", "workspace": "/tmp/ws"})
        core.record_tool("sid-b", "Read", False)
        core.record_prompt("sid-b", "继续", "/tmp/ws")

        binding = core.health_summary(7)["binding"]
        self.assertEqual(binding["sessions"], 2)
        self.assertEqual(binding["sessions_with_early_work"], 1)
        self.assertEqual(binding["early_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
