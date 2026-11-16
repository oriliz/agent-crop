#!/usr/bin/env python3
"""Event-flow tests: what the probe writes, in what order, for each lifecycle.

Every case uses a throwaway KIT_DATA_DIR; the real data directory is never touched.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class EventFlowTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-flow-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.core.WORK_START, self.core.WORK_END = "00:00", "23:59"
        self.core.IDLE_MIN = 15.0
        self.sid = "flow-1"

    def tearDown(self):
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    # -- helpers -----------------------------------------------------------
    def kinds(self, sid=None):
        return [event["ev"] for event in self.core.load_events(sid=sid or self.sid)]

    def events(self, sid=None):
        return self.core.load_events(sid=sid or self.sid)

    def report(self, **args):
        return self.core.record_report(
            {"task_id": "flow-work", "summary": "做点事", **args},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )

    # -- 1. turn lifecycle -------------------------------------------------
    def test_turn_lifecycle_order(self):
        self.core.record_session_start(self.sid, "/tmp/ws")
        self.core.record_prompt(self.sid, "开始", "/tmp/ws")
        self.core.record_tool(self.sid, "Read", False)
        self.core.record_stop(self.sid)
        self.assertEqual(self.kinds(), ["session_start", "prompt", "tool", "stop"])
        blocks = [json.loads(line) for line in open(
            os.path.join(self.data, "blocks", self.core.today() + ".jsonl"), encoding="utf-8")]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["sid"], self.sid)

    # -- 2. waiting vs leaving --------------------------------------------
    def test_short_gap_is_user_wait(self):
        self.core.record_prompt(self.sid, "一", "/tmp/ws")
        self.core.record_stop(self.sid)
        self.core.record_prompt(self.sid, "二", "/tmp/ws")
        self.assertIn("user_wait", self.kinds())

    def test_long_gap_is_idle(self):
        self.core.IDLE_MIN = -1.0  # any non-negative gap exceeds the threshold
        self.core.record_prompt(self.sid, "一", "/tmp/ws")
        self.core.record_stop(self.sid)
        self.core.record_prompt(self.sid, "二", "/tmp/ws")
        self.assertIn("idle_gap", self.kinds())

    # -- 3. report shapes --------------------------------------------------
    def test_start_report_links_block_and_stores_goal(self):
        self.core.record_prompt(self.sid, "开工", "/tmp/ws")
        self.report(phase="start", goal="把面板做完", type="design", difficulty="m")
        kinds = self.kinds()
        self.assertIn("probe_report", kinds)
        self.assertIn("task_link", kinds)
        task = self.core.load_open()["tasks"]["flow-work"]
        self.assertEqual(task["goal"], "把面板做完")
        self.assertEqual(task["type"], "design")
        self.assertEqual(task["difficulty"], "m")

    def test_start_report_emits_work_claim(self):
        self.report(phase="start")
        kinds = self.kinds()
        self.assertIn("work_claim", kinds)
        self.assertIn("work_open", kinds)

    def test_session_summary_exposes_timeline(self):
        self.report(phase="start")
        self.core.record_prompt(self.sid, "hi", "/tmp/ws")
        self.core.record_stop(self.sid)
        data = self.core.session_summary(self.sid)
        self.assertGreater(data["session_started_at"], 0)
        self.assertTrue(data["timeline"]["blocks"])
        self.assertTrue(data["work_titles"])
        self.assertIn("reflection", data)
        self.assertIn("improvements", data)

    def test_stop_does_not_borrow_another_sessions_work(self):
        self.report(phase="start")  # this session owns flow-work now
        self.core.record_prompt("other-session", "hi", "/tmp/ws")
        self.core.record_stop("other-session")
        stops = [e for e in self.core.load_events(sid="other-session") if e["ev"] == "stop"]
        self.assertEqual(stops[-1]["work_id"], "")

    def test_tool_events_carry_the_bound_work(self):
        self.report(phase="start")
        self.core.record_tool(self.sid, "Bash", False)
        tools = [e for e in self.events() if e["ev"] == "tool"]
        self.assertEqual(tools[-1].get("work_id"), "flow-work")

    def test_tool_churn_carries_the_bound_work(self):
        self.report(phase="start")
        for _ in range(5):
            self.core.record_tool(self.sid, "Bash", False)
        churn = [e for e in self.events() if e["ev"] == "tool_churn"]
        self.assertTrue(churn)
        self.assertEqual(churn[-1].get("work_id"), "flow-work")

    def test_tool_events_without_binding_have_no_work_id(self):
        self.core.record_tool(self.sid, "Bash", False)
        tools = [e for e in self.events() if e["ev"] == "tool"]
        self.assertNotIn("work_id", tools[-1])

    def test_end_requires_what(self):
        self.report(phase="start")
        with self.assertRaises(ValueError):
            self.report(phase="end", summary="收尾")
        with self.assertRaises(ValueError):
            self.report(phase="end", cost="查资料")
        self.report(phase="end", what="做完")

    def test_end_report_persists_artifact(self):
        self.report(phase="start")
        self.report(phase="end", what="做完", cost="查资料", next_time="先画图",
                    artifact="docs/plan.md")
        summaries = [e for e in self.events() if e["ev"] == "work_summary"]
        self.assertEqual(summaries[-1]["artifact"], "docs/plan.md")

    def test_report_keeps_possible_improvements(self):
        self.report(phase="blocked", cause="文档顺序错", category="tool-misuse",
                    improve="先读文档再选工具", verdict="not-justified")
        events = [e for e in self.events() if e.get("ev") == "probe_report"]
        self.assertEqual(events[-1]["improve"], "先读文档再选工具")

    def test_side_issue_does_not_touch_the_work_state(self):
        self.report(phase="start")
        self.report(phase="progress", category="side-issue", summary="顺手看到的界面问题")
        self.assertIn("side_issue", self.kinds())
        self.assertEqual(self.core.load_open()["tasks"]["flow-work"].get("state", "open"), "open")

    def test_end_report_writes_summary_and_closes(self):
        self.report(phase="start")
        self.report(phase="end", what="做完了", cost="查资料", next_time="先画图", verdict="justified")
        self.assertIn("work_summary", self.kinds())
        self.assertNotIn("flow-work", self.core.load_open()["tasks"])

    # -- 4. no double counting --------------------------------------------
    def test_two_reports_in_one_block_count_once(self):
        self.core.record_prompt(self.sid, "开工", "/tmp/ws")
        self.report(phase="start")
        self.report(phase="progress", summary="进展")
        self.core.record_stop(self.sid)
        minutes = self.core.work_minutes("flow-work")
        self.assertLessEqual(minutes, 1.0)

    # -- 5. red lines -----------------------------------------------------
    def test_work_level_reminder_respects_threshold(self):
        self.report(phase="start")
        original = self.core.work_health
        self.core.work_health = lambda work_id=None: {
            "work_id": "flow-work", "goal": "g", "minutes": 5.0,
            "baseline": 30.0, "ratio": 0.17, "level": "green",
        }
        try:
            self.assertEqual(self.core.maybe_work_reminder(self.sid), "")
            self.core.work_health = lambda work_id=None: {
                "work_id": "flow-work", "goal": "g", "minutes": 12.0,
                "baseline": 30.0, "ratio": 0.4, "level": "green",
            }
            self.assertIn("[crop]", self.core.maybe_work_reminder(self.sid))
        finally:
            self.core.work_health = original

    def test_tool_churn_after_repeated_tool(self):
        self.core.record_prompt(self.sid, "开工", "/tmp/ws")
        for _ in range(self.core.REPEAT_TOOL_THRESHOLD):
            self.core.record_tool(self.sid, "bash", True)
        self.assertIn("tool_churn", self.kinds())

    def test_review_levels_are_ai_judged(self):
        self.report(phase="start")
        result = self.core.record_review({"rework": "major", "sentiment": "neg", "reason": "要重写"},
                                         {"sid": self.sid})
        self.assertEqual(result["rework"], "major")
        self.assertIn("self_review", self.kinds())
        with self.assertRaises(ValueError):
            self.core.record_review({"rework": "bad"}, {"sid": self.sid})

    # -- 6. bindings and lifecycle events ---------------------------------
    def test_start_opens_and_binds_then_end_closes(self):
        self.report(phase="start", goal="甲")
        self.assertIn("work_open", self.kinds())
        self.assertEqual(self.core.resolve_binding(self.sid), "flow-work")
        self.report(phase="end", what="完")
        self.assertIn("work_close", self.kinds())
        self.assertEqual(self.core.resolve_binding(self.sid), "")

    def test_binding_survives_another_session(self):
        self.report(phase="start")
        other = "flow-2"
        self.assertEqual(self.core.resolve_binding(other), "")
        self.assertTrue(self.core.bind_session(other, "flow-work"))
        self.assertEqual(self.core.resolve_binding(other), "flow-work")
        # one session, one active binding: binding the other work releases the first
        self.report(phase="start", task_id="flow-other")
        self.core.bind_session(other, "flow-other")
        self.assertEqual(self.core.resolve_binding(other), "flow-other")

    def test_migrate_legacy_last_sid(self):
        self.report(phase="start")
        value = self.core.load_open()
        value["tasks"]["flow-work"].pop("last_sid", None)
        self.core.save_open(value)
        self.core.load_bindings()  # ensure file initialised
        moved = self.core.migrate_bindings()
        self.assertGreaterEqual(moved, 0)

    def test_one_active_binding_per_session(self):
        self.report(phase="start")
        value = self.core.load_bindings()
        value["bindings"].append({"sid": self.sid, "work_id": "other-work",
                                  "claimed_at": 1, "expires_at": 9999999999999})
        self.core.save_bindings(value)
        self.assertGreaterEqual(self.core.normalize_bindings(), 1)
        active = [b for b in self.core.load_bindings()["bindings"] if b["sid"] == self.sid]
        self.assertEqual(len(active), 1)
        self.assertEqual(self.core.resolve_binding(self.sid), "flow-work")

    def test_migration_is_idempotent_and_keeps_newest(self):
        self.report(phase="start", task_id="older")
        self.report(phase="start", task_id="newer")
        value = self.core.load_open()
        value["tasks"]["older"]["last_seen_at"] = 1000
        value["tasks"]["newer"]["last_seen_at"] = 2000
        self.core.save_open(value)
        self.core.save_bindings({"bindings": []})
        self.core.migrate_bindings()
        first = self.core.load_bindings()["bindings"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["work_id"], "newer")
        self.core.migrate_bindings()
        self.assertEqual(len(self.core.load_bindings()["bindings"]), 1)

    def test_human_wait_span(self):
        event = self.core.record_human_wait(self.sid, "ask_user_question", 1000, 1000 + 65000)
        self.assertEqual(event["waiting_ms"], 65000)
        self.assertIn("human_wait", self.kinds())

    # -- 7. work lifecycle -------------------------------------------------
    def test_claim_suspend_archive(self):
        self.report(phase="start")
        self.assertTrue(self.core.switch_claim("flow-work", self.sid))
        self.assertIn("work_claim", self.kinds())
        self.assertTrue(self.core.set_work_state("flow-work", "suspended", self.sid))
        self.assertIn("work_suspend", self.kinds())
        self.assertTrue(self.core.archive_work("flow-work", "manual", self.sid))
        self.assertIn("work_archive", self.kinds())
        self.assertNotIn("flow-work", self.core.load_open()["tasks"])


if __name__ == "__main__":
    unittest.main()
