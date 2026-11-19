#!/usr/bin/env python3
"""End-to-end simulation of the reminder ladder across an estimate revision.

Unlike the unit tests that fake ``work_health``, this drives the real
lifecycle on a controllable clock: a prompt opens the block, tool calls keep
the work "active", ``record_report`` revises the estimate, and every half
minute the probe runs ``maybe_work_reminder`` exactly as a host step would.
The minutes the ladder sees are therefore measured from the same events the
production code reads.

Scenarios are taken from #68: a 5-minute work revised late (the old code
landed straight on overdue), revised early (the old grace swallowed the whole
task), and not revised at all (the ladder must still climb every notch).
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

T0 = 1_760_000_000_000
STEP_MIN = 0.5


class RevisionSimulationTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-sim-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.core.WORK_START, self.core.WORK_END = "00:00", "23:59"
        self.sid = "sim-1"
        self.now = T0
        self._now = core.now_ms
        core.now_ms = lambda: self.now
        self.core.record_prompt(self.sid, "开始", "/tmp/ws")

    def tearDown(self):
        self.core.now_ms = self._now
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    # -- harness -----------------------------------------------------------
    def report(self, **args):
        return self.core.record_report(
            {"task_id": "sim-work", "summary": "做点事", "phase": "start", **args},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )

    def advance(self, minutes):
        self.now += int(minutes * 60000)

    def tick(self):
        """One host step: a tool call keeps the block active, then the probe."""
        name = "Bash" if (self.now // 60000) % 2 else "Read"
        self.core.record_tool(self.sid, name, False)
        return self.core.maybe_work_reminder(self.sid)

    def run_until(self, minutes, start_expect=5, revise_at=None, revise_expect=5):
        """Simulate half-minute steps; return [(minute, tier)] after each nudge."""
        self.report(expect_min=start_expect)
        reminders = []
        elapsed = 0.0
        revised = False
        while elapsed < minutes - 1e-9:
            self.advance(STEP_MIN)
            elapsed += STEP_MIN
            if revise_at is not None and not revised and elapsed + 1e-9 >= revise_at:
                self.core.record_report(
                    {"task_id": "sim-work", "summary": "改口", "phase": "progress",
                     "expect_min": revise_expect},
                    {"sid": self.sid, "workspace": "/tmp/ws"},
                )
                revised = True
            if self.tick():
                event = [item for item in self.core.load_events(sid=self.sid)
                         if item.get("ev") == "reminder"][-1]
                reminders.append((round(elapsed, 2), str(event.get("tier") or "")))
        return reminders

    def tiers_after(self, reminders, since):
        return [tier for minute, tier in reminders if minute > since]

    def minutes_after(self, reminders, since):
        return [minute for minute, _ in reminders if minute > since]

    # -- scenarios ---------------------------------------------------------
    def test_unrevised_short_work_climbs_every_notch(self):
        reminders = self.run_until(30, start_expect=5)
        tiers = [tier for _, tier in reminders]
        self.assertEqual(tiers[:4], ["warm", "yellow", "red", "overdue"])
        # After overdue the same tier may repeat, but only on the global cadence.
        self.assertTrue(all(tier == "overdue" for tier in tiers[4:]))
        minutes = [minute for minute, _ in reminders]
        for actual, expected in zip(minutes[:4], (4.0, 5.0, 7.5, 12.5)):
            self.assertAlmostEqual(actual, expected, delta=STEP_MIN + 1e-6)
        if len(minutes) > 4:
            self.assertGreaterEqual(minutes[4] - minutes[3], 15.0 - 1e-6)

    def test_late_revision_resumes_at_yellow_not_overdue(self):
        reminders = self.run_until(40, start_expect=3, revise_at=15.5, revise_expect=5)
        after = self.tiers_after(reminders, 15.5)
        self.assertEqual(after[:3], ["yellow", "red", "overdue"])
        minutes = self.minutes_after(reminders, 15.5)
        self.assertGreaterEqual(minutes[0], 16.5, "改口后 1 分钟内不该有提醒")
        self.assertLess(minutes[0], 17.5)
        self.assertEqual([tier for _, tier in reminders if 15.5 < _ <= 16.4], [])

    def test_early_revision_does_not_swallow_the_task(self):
        reminders = self.run_until(30, start_expect=3, revise_at=0.8, revise_expect=5)
        after = self.tiers_after(reminders, 0.8)
        self.assertEqual(after[:4], ["warm", "yellow", "red", "overdue"])
        minutes = self.minutes_after(reminders, 0.8)
        self.assertGreaterEqual(minutes[0], 1.8, "改口后 1 分钟内不该有提醒")
        self.assertLessEqual(minutes[0], 4.5, "旧行为会整段免鞭到 15 分钟以后")
        self.assertEqual(self.tiers_after(reminders, 15.5), ["overdue"])


if __name__ == "__main__":
    unittest.main()
