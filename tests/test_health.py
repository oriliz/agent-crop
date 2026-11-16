"""健康度聚合的单元测试。

健康度不是「用了哪个工具」，而是「工具本身是不是哑了」。所以这里的断言都盯在
两件事上：指标是否从原始事件正确派生；异常能不能变成一条可执行的诊断。
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import probe_core as core  # noqa: E402

NOW = core.now_ms()
MIN = 60 * 1000


class HealthSummaryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {}
        for key in ("KIT_DATA_DIR", "KIT_SHARED_HOME"):
            self._env[key] = os.environ.get(key)
            os.environ[key] = self._tmp.name
        self.events = []

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def emit(self, ev, sid="s1", minutes_ago=0, **fields):
        event = {"ev": ev, "t": NOW - minutes_ago * MIN, "host": "test", "sid": sid}
        event.update(fields)
        self.events.append(event)
        core.append_event(event)

    def seed_normal(self):
        """两个有效会话：有回合、有上报、有收尾。"""
        for sid in ("s1", "s2"):
            self.emit("prompt", sid=sid, minutes_ago=30)
            self.emit("prompt", sid=sid, minutes_ago=20)
            self.emit("stop", sid=sid, minutes_ago=20, minutes=10)
            self.emit("tool", sid=sid, minutes_ago=25, name="Bash")  # 故意不带 work_id
            self.emit("probe_report", sid=sid, minutes_ago=30, phase="start",
                      task_id="health-" + sid, summary="做健康度")
            self.emit("probe_report", sid=sid, minutes_ago=10, phase="end",
                      task_id="health-" + sid, summary="收尾")

    def test_short_sessions_are_excluded(self):
        self.seed_normal()
        self.emit("prompt", sid="short", minutes_ago=5)  # 只有一次提问，不算有效会话
        payload = core.health_summary(days=7)
        self.assertEqual(payload["sessions"]["total"], 3)
        self.assertEqual(payload["sessions"]["eligible"], 2)

    def test_passive_tool_reports_missing_work_id(self):
        self.seed_normal()
        payload = core.health_summary(days=7)
        tool_row = next(row for row in payload["passive"] if row["tool"] == "tool")
        self.assertEqual(tool_row["events"], 2)
        self.assertLess(tool_row["field_completeness"], 0.95)
        self.assertIn("work_id", tool_row["missing_fields"])
        self.assertEqual(tool_row["lamp"], "yellow")
        titles = [diag["title"] for diag in payload["diagnostics"]]
        self.assertIn("工具事件没有归属到工作", titles)

    def test_active_report_closure_and_adherence(self):
        self.seed_normal()
        # 缺一次 end：两条 start 只有一条 end → 闭环 50%
        self.emit("probe_report", sid="s1", minutes_ago=8, phase="start",
                  task_id="health-s3", summary="第三个工作")
        # 两次提醒：一次在 10 分钟内被跟进，一次没人理
        self.emit("reminder", sid="s1", minutes_ago=12, minutes=30)
        self.emit("probe_report", sid="s1", minutes_ago=10, phase="progress",
                  task_id="health-s1", summary="跟进")
        self.emit("reminder", sid="s2", minutes_ago=9, minutes=30)
        payload = core.health_summary(days=7)
        row = next(item for item in payload["active"] if item["tool"] == "probe_report")
        self.assertAlmostEqual(row["stage_closure"], 2 / 3, places=3)
        self.assertAlmostEqual(row["adherence"], 1 / 2, places=3)
        self.assertIn("有开始、没收尾", [diag["title"] for diag in payload["diagnostics"]])

    def test_missing_work_summary_is_flagged(self):
        self.seed_normal()
        payload = core.health_summary(days=7)
        row = next(item for item in payload["active"] if item["tool"] == "work_summary")
        self.assertEqual(row["events"], 0)
        self.assertEqual(row["lamp"], "red")
        self.assertIn("收尾一条小结都没有", [diag["title"] for diag in payload["diagnostics"]])

    def test_not_shipped_tool_is_gray(self):
        self.seed_normal()
        payload = core.health_summary(days=7)
        row = next(item for item in payload["active"] if item["tool"] == "adjustment")
        self.assertEqual(row["status"], "not-shipped")
        self.assertEqual(row["lamp"], "gray")
        self.assertIn("改进记录还没上线", [diag["title"] for diag in payload["diagnostics"]])

    def test_empty_window_is_honest(self):
        payload = core.health_summary(days=7)
        self.assertEqual(payload["sessions"]["eligible"], 0)
        for row in payload["passive"]:
            self.assertEqual(row["events"], 0)
            self.assertEqual(row["lamp"], "red")
        self.assertIn("样本太少，先别下结论", [diag["title"] for diag in payload["diagnostics"]])

    def test_small_sample_suppresses_conclusions(self):
        self.emit("prompt", sid="only", minutes_ago=10)
        self.emit("prompt", sid="only", minutes_ago=9)
        self.emit("stop", sid="only", minutes_ago=9, minutes=5)
        payload = core.health_summary(days=7)
        self.assertEqual(payload["sessions"]["eligible"], 1)
        self.assertIn("样本太少，先别下结论", [diag["title"] for diag in payload["diagnostics"]])


if __name__ == "__main__":
    unittest.main()
