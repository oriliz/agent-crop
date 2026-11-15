#!/usr/bin/env python3
"""Tier copy pools: every nudge is a contract, not a clock.

The AI reports its own expectation (expect_min) when it starts.  The whip only
cracks when the work runs past that number, never inside it.  These tests pin
the three things that make that safe: the ladder, the pool size/shape, and the
rotation (no line repeats before the whole pool has been shown).
"""

import os
import random
import string
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

import crop_voice as voice  # noqa: E402


PLACEHOLDERS = {"m", "e", "over", "left", "goal", "streak", "basis", "yellow", "default", "changes", "line"}


class PoolShapeTest(unittest.TestCase):
    def test_time_tiers_have_at_least_five_lines(self):
        for tier in ("warm", "yellow", "red", "overdue"):
            self.assertGreaterEqual(len(voice.POOLS[tier]), 5, tier)

    def test_miss_tiers_exist(self):
        self.assertGreaterEqual(len(voice.POOLS["miss"]), 4)
        self.assertGreaterEqual(len(voice.POOLS["miss_streak"]), 4)

    def test_placeholders_are_known(self):
        formatter = string.Formatter()
        for tier, pool in voice.POOLS.items():
            for line in pool:
                for _, field, _, _ in formatter.parse(line):
                    if field is None:
                        continue
                    self.assertIn(field, PLACEHOLDERS, f"{tier}: {line}")

    def test_overdue_copy_uses_current_slang(self):
        text = "".join(voice.POOLS["overdue"])
        self.assertIn("烧心", text)

    def test_yellow_copy_offers_pushback(self):
        text = "".join(voice.POOLS["yellow"])
        self.assertIn("顶回去", text)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(tempfile.mkdtemp(prefix="crop-voice-"), "voice.json")

    def test_every_line_shows_once_before_repeat(self):
        pool = voice.POOLS["yellow"]
        seen = [voice.pick("yellow", self.state) for _ in range(len(pool))]
        self.assertEqual(sorted(seen), sorted(pool))

    def test_no_two_consecutive_repeats_across_a_refill(self):
        pool = voice.POOLS["overdue"]
        picks = [voice.pick("overdue", self.state) for _ in range(len(pool) * 3)]
        for before, after in zip(picks, picks[1:]):
            self.assertNotEqual(before, after)

    def test_corrupt_state_file_still_rotates(self):
        with open(self.state, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertIn(voice.pick("warm", self.state), voice.POOLS["warm"])

    def test_tiers_keep_independent_bags(self):
        a = voice.pick("warm", self.state)
        b = voice.pick("yellow", self.state)
        self.assertIn(a, voice.POOLS["warm"])
        self.assertIn(b, voice.POOLS["yellow"])


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(tempfile.mkdtemp(prefix="crop-voice-"), "voice.json")

    def draw(self, tier, meta, times=None):
        return [voice.reminder(tier, meta, self.state)
                for _ in range(times or len(voice.POOLS[tier]))]

    def test_yellow_render_shows_numbers_and_action(self):
        for text in self.draw("yellow", {"m": 48, "e": 40, "goal": "做A"}):
            self.assertIn("[crop]", text)
            self.assertIn("48", text)
            self.assertIn("crop_report", text)
            self.assertIn("verdict", text)

    def test_warm_render_does_not_demand_a_report(self):
        texts = self.draw("warm", {"m": 32, "e": 40, "goal": "做A"})
        for text in texts:
            self.assertIn("32", text)
            self.assertNotIn("crop_report", text)
        self.assertTrue(any("8" in text for text in texts), "还剩 8 分钟该出现在某些句子里")

    def test_missing_estimate_says_so(self):
        text = voice.reminder("yellow", {"m": 12, "e": None, "goal": "做A"}, self.state)
        self.assertIn("没报预期", text)

    def test_hour_scale_formats_in_hours(self):
        self.assertEqual(voice.fmt_minutes(210), "3 小时 30 分")
        self.assertEqual(voice.fmt_minutes(180), "3 小时")
        self.assertEqual(voice.fmt_minutes(8), "8 分钟")

    def test_action_asks_for_a_user_facing_flavor_line(self):
        for tier, meta in (("warm", {"m": 32, "e": 40}),
                           ("yellow", {"m": 48, "e": 40}),
                           ("red", {"m": 70, "e": 40}),
                           ("overdue", {"m": 130, "e": 40})):
            text = voice.reminder(tier, meta, self.state)
            self.assertTrue("梗" in text or "自嘲" in text, tier)
            self.assertRegex(text, r"比如[：:]?「", tier)

    def test_yellow_example_carries_the_real_numbers(self):
        text = voice.reminder("yellow", {"m": 48, "e": 40}, self.state)
        self.assertIn("我报的 40 分钟到了", text)
        self.assertIn("现在 48 分钟", text)

    def test_red_fallback_names_the_red_line(self):
        text = voice.reminder("red", {"m": 35, "e": None, "default": 30, "line": "红线"}, self.state)
        self.assertIn("默认红线", text)

    def test_repeated_revisions_are_called_out(self):
        text = voice.reminder("yellow", {"m": 48, "e": 40, "changes": 2}, self.state)
        self.assertIn("改口 2 次", text)

    def test_miss_render_carries_streak(self):
        text = voice.miss(3, self.state)
        self.assertIn("3", text)
        self.assertIn("crop_report", text)


if __name__ == "__main__":
    unittest.main()
