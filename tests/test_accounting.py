#!/usr/bin/env python3
"""Active-time accounting regression tests (Issue #18).

The probe is allowed to be late, noisy, or funny.  It is not allowed to charge
the AI for wall-clock time the human was away, or to let one block's clock leak
into the next session.  These cases pin the three symptoms in #18:

1. a dangling prompt must not carry its start time into the next session;
2. a block is capped at the last observed interaction, not at "now";
3. wait intervals are subtracted from the block, and a nudge is not fired in
   the same breath as an idle_gap.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

MIN = 60 * 1000


class ActiveTimeTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-acct-")
        self._env = {}
        for key in ("KIT_DATA_DIR", "KIT_SHARED_HOME", "KIT_HOST"):
            self._env[key] = os.environ.get(key)
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_SHARED_HOME"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self._work_start, self._work_end = core.WORK_START, core.WORK_END
        self._idle = core.IDLE_MIN
        core.WORK_START, core.WORK_END = "00:00", "23:59"
        core.IDLE_MIN = 15.0
        self.now = 1_800_000_000_000
        self._now_ms = core.now_ms
        core.now_ms = lambda: self.now
        self.sid = "acct-1"
        self.ws = "/tmp/ws"
        self.work = "acct-work"

    def tearDown(self):
        self.core.now_ms = self._now_ms
        self.core.WORK_START, self.core.WORK_END = self._work_start, self._work_end
        self.core.IDLE_MIN = self._idle
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # -- helpers -----------------------------------------------------------
    def tick(self, minutes):
        self.now += int(minutes * MIN)

    def report(self, **overrides):
        sid = overrides.pop("_sid", self.sid)
        args = {"task_id": self.work, "phase": "start", "summary": "做点事",
                "expect_min": 40, "expect_why": "先看清结构", **overrides}
        return self.core.record_report(args, {"sid": sid, "workspace": self.ws})

    def events(self, ev=None, sid=None):
        rows = self.core.load_events(sid=sid or self.sid)
        return [row for row in rows if ev is None or row.get("ev") == ev]

    def blocks(self):
        path = os.path.join(self.data, "blocks", self.core.today() + ".jsonl")
        try:
            with open(path, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []

    # -- 1. dangling block does not survive a session restart ---------------
    def test_session_start_closes_a_dangling_block(self):
        self.core.record_session_start(self.sid, self.ws)
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.tick(6)
        self.core.record_session_start(self.sid, self.ws)

        stops = self.events("stop")
        self.assertTrue(stops, "session_start must close the dangling block")
        self.assertTrue(stops[-1].get("abandoned"))
        self.assertLessEqual(stops[-1]["minutes"], 6.1)

        # The next turn starts from its own prompt, not from 17:19.
        self.core.record_prompt(self.sid, "继续", self.ws)
        self.tick(1)
        self.core.record_stop(self.sid)
        last = self.events("stop")[-1]
        self.assertLessEqual(last["minutes"], 1.1, "stop must stay inside this session")

    def test_current_block_start_stops_at_the_session_boundary(self):
        self.core.record_prompt(self.sid, "旧回合", self.ws)
        self.tick(5)
        self.core.append_event({"ev": "session_start", "sid": self.sid,
                                "workspace": self.ws})
        self.assertIsNone(self.core.current_block_start(self.sid))

    def test_a_dangling_prompt_survives_no_session_start_but_is_capped(self):
        # The other half of #18: a second host/session never sends a new
        # session_start, so only the stop-time cap can save the accounting.
        self.core.record_prompt("acct-other", "另一台机器上的回合", self.ws)
        self.tick(1)
        self.core.record_tool("acct-other", "Read", False)
        self.tick(180)
        self.core.record_stop("acct-other")
        last = self.events("stop", sid="acct-other")[-1]
        self.assertAlmostEqual(last["minutes"], 1.0, delta=0.2)

    # -- 2. a block is capped at the last observed interaction --------------
    def test_stop_does_not_charge_idle_wall_time(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.tick(2)
        self.core.record_tool(self.sid, "Read", False)
        self.tick(180)
        self.core.record_stop(self.sid)

        last = self.events("stop")[-1]
        self.assertAlmostEqual(last["minutes"], 2.0, delta=0.2)
        self.assertAlmostEqual(self.blocks()[-1]["minutes"], 2.0, delta=0.2)
        self.assertAlmostEqual(self.blocks()[-1]["wall_minutes"], 182.0, delta=0.2)

    def test_report_minutes_are_active_minutes_not_wall_clock(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.tick(120)
        record = self.report(phase="progress")
        self.assertEqual(record["block_start"], self.now - 120 * MIN)
        self.assertEqual(record["minutes"], 0.0,
                         "a two-hour silence is not two hours of work")

    # -- 3. wait intervals are subtracted -----------------------------------
    def test_human_wait_is_subtracted_from_the_block(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.tick(1)
        self.core.record_tool(self.sid, "Read", False)
        self.core.record_human_wait(self.sid, "ask_user_question",
                                    self.now, self.now + 10 * MIN)
        self.tick(10)
        self.core.record_tool(self.sid, "Read", False)
        self.tick(1)
        self.core.record_stop(self.sid)

        last = self.events("stop")[-1]
        # 1m prompt->tool + 1m tool->stop; the 10m human wait in between is not work.
        self.assertAlmostEqual(last["minutes"], 2.0, delta=0.3)

    def test_nudge_is_suppressed_right_after_the_human_returns(self):
        self.report(phase="start")
        self.core.append_event({"ev": "idle_gap", "sid": self.sid,
                                "gap_ms": 20 * MIN, "reason": "idle"})
        original = self.core.work_health
        self.core.work_health = lambda work_id=None: {
            "work_id": self.work, "goal": "g", "minutes": 99.0,
            "baseline": 30.0, "ratio": 3.3, "level": "red",
            "expect_min": 40.0, "expect_why": "", "expect_changes": 0,
        }
        try:
            self.assertEqual(self.core.maybe_work_reminder(self.sid), "")
            self.assertEqual(self.core.maybe_reminder(self.sid), "")
            self.tick(3)
            self.assertIn("[crop]", self.core.maybe_work_reminder(self.sid))
        finally:
            self.core.work_health = original

    def test_session_end_closes_a_dangling_block(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.tick(3)
        self.core.record_session_end(self.sid)
        stops = self.events("stop")
        self.assertTrue(stops and stops[-1].get("abandoned"))
        self.assertAlmostEqual(stops[-1]["minutes"], 3.0, delta=0.2)

    def test_legacy_nudge_uses_active_minutes(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.report(phase="start", expect_min=None, expect_why="")
        self.tick(1)
        self.core.record_tool(self.sid, "Read", False)
        self.tick(120)
        # 121 wall minutes, 1 active minute: the legacy 30-minute red line is
        # about activity, not about how long the tab stayed open.
        self.assertEqual(self.core.maybe_reminder(self.sid), "")

    def test_two_sessions_do_not_share_one_wall_clock(self):
        self.core.record_prompt("acct-a", "开工", self.ws)
        self.report(_sid="acct-a", task_id="work-a", phase="start", expect_min=None)
        self.tick(120)
        self.core.record_prompt("acct-b", "另一个会话", self.ws)
        self.report(_sid="acct-b", task_id="work-b", phase="start", expect_min=None)
        self.tick(2)
        self.core.record_tool("acct-b", "Read", False)
        self.tick(1)
        self.core.record_stop("acct-b")

        # acct-a was silent for the whole window; its open block must not
        # charge those 120 minutes, and acct-b only gets its own 3.
        self.assertAlmostEqual(self.core.work_minutes("work-a"), 0.0, delta=0.5)
        self.assertLessEqual(self.core.work_minutes("work-b"), 3.5)

    # -- 4. the open block counts, but only while it is active --------------
    def test_open_block_minutes_are_counted_and_capped(self):
        self.core.record_prompt(self.sid, "开工", self.ws)
        self.report(phase="start")
        self.tick(1)
        self.core.record_tool(self.sid, "Read", False)
        self.tick(180)

        minutes = self.core.work_minutes(self.work)
        self.assertAlmostEqual(minutes, 1.0, delta=0.3,
                               msg="the open block must count, but only its active part")

    # -- 5. runtime state cannot lend a block to another session ------------
    def test_report_ignores_a_block_from_another_session(self):
        self.core.write_runtime("other-session", self.ws, self.now - 60 * MIN)
        record = self.core.record_report(
            {"task_id": self.work, "phase": "start", "summary": "做点事"},
            {"sid": self.sid, "workspace": self.ws})
        self.assertIsNone(record["block_start"])
        self.assertEqual(record["minutes"], 0.0)

    def test_report_keeps_its_own_session_block(self):
        self.core.write_runtime(self.sid, self.ws, self.now - 5 * MIN)
        record = self.core.record_report(
            {"task_id": self.work, "phase": "start", "summary": "做点事"},
            {"sid": self.sid, "workspace": self.ws})
        self.assertEqual(record["block_start"], self.now - 5 * MIN)
        self.assertAlmostEqual(record["minutes"], 5.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
