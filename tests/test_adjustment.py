#!/usr/bin/env python3
"""改进台账：记录要有证据，判定看数据，不靠 AI 自评。"""

import json
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
        args = {"metric": "work_minutes", "before": 40, "evidence": "crop report --days 7",
                "work_id": "adj-work"}
        args.update(extra)
        return self.core.record_adjustment(args, {"sid": "adj-s"})

    def test_record_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.record(evidence="")

    def test_record_requires_before(self):
        with self.assertRaises(ValueError):
            self.record(before=None)

    def test_record_writes_an_adjustment_event(self):
        event = self.record(after=32)
        self.assertEqual(event["metric"], "work_minutes")
        rows = [e for e in self.core.load_events() if e.get("ev") == "adjustment"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"], "crop report --days 7")
        self.assertEqual(rows[0]["after"], 32.0)

    def test_pending_when_after_is_missing(self):
        self.record(after=None)
        ledger = self.core.improvement_ledger()
        self.assertEqual(ledger["rows"][0]["status"], "pending")

    def test_unknown_metric_uses_supplied_values(self):
        event = self.record(metric="my_custom_metric", before=10, after=8)
        self.assertEqual(event["metric"], "my_custom_metric")
        ledger = self.core.improvement_ledger()
        row = ledger["rows"][0]
        self.assertEqual(row["status"], "improving")
        self.assertAlmostEqual(row["delta"], -0.2, places=3)
        self.assertFalse(row["known_metric"])

    def test_small_sample_only_reports_insufficient(self):
        self.record(after=30)
        ledger = self.core.improvement_ledger()
        self.assertEqual(ledger["rows"][0]["status"], "insufficient")

    def ledger_with(self, rows_after, guardrail_after=None):
        """把派生指标换成一个可控的假函数：before 窗口返回 rows_after[0]。"""
        values = {
            "work_minutes": ((40.0, 30), rows_after),
            "reminder_per_turn": ((0.5, 30), guardrail_after or (0.5, 30)),
            "report_latency_min": ((5.0, 30), (5.0, 30)),
            "unowned_tool_share": ((0.1, 30), (0.1, 30)),
        }
        adopted = {row["adopted_at"] for row in self.core.load_adjustments()}
        fake = lambda metric, start, end, work_type="": (
            values[metric][0] if int(end) in adopted else values[metric][1])
        old = self.core._metric_value
        self.core._metric_value = fake
        try:
            return self.core.improvement_ledger()
        finally:
            self.core._metric_value = old

    def test_guardrail_worse_is_not_an_improvement(self):
        now = self.core.now_ms()
        self.record(after=30, adopted_at=now - 1000)
        ledger = self.ledger_with((30.0, 30), guardrail_after=(1.0, 30))
        self.assertEqual(ledger["rows"][0]["status"], "guardrail")

    def test_three_improving_measurements_reach_met(self):
        now = self.core.now_ms()
        for index in range(3):
            self.record(after=30, adopted_at=now - (30 - index * 10) * 86400000)
        ledger = self.ledger_with((30.0, 30))
        statuses = [row["status"] for row in ledger["rows"]]
        self.assertIn("met", statuses)

    def test_format_ledger_is_readable(self):
        self.record(after=32)
        text = self.core.format_ledger(self.core.improvement_ledger())
        self.assertIn("改进台账", text)
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
        # A：先调工具、后绑定
        core.record_prompt("sid-a", "干活", "/tmp/ws")
        core.record_tool("sid-a", "Read", False)
        core.record_prompt("sid-a", "继续", "/tmp/ws")
        core.record_report({"task_id": "a-work", "summary": "做", "phase": "start"},
                           {"sid": "sid-a", "workspace": "/tmp/ws"})
        # B：先绑定、后调工具
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
