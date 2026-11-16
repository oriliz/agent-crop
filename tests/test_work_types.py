#!/usr/bin/env python3
"""Work types are guidance, not a closed enum.

`explore` ships as a built-in because research/triage behaves differently from
implementation; beyond that users may name their own type with a short slug.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class WorkTypeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {}
        for key in ("KIT_DATA_DIR", "KIT_SHARED_HOME", "KIT_HOST"):
            self._env[key] = os.environ.get(key)
        os.environ["KIT_DATA_DIR"] = self._tmp.name
        os.environ["KIT_SHARED_HOME"] = self._tmp.name
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def base(self, **overrides):
        return {"phase": "start", "task_id": "type-work", "summary": "做点事",
                **overrides}

    def test_explore_is_a_builtin_type(self):
        self.assertIn("explore", self.core.WORK_TYPES)
        report = self.core.validate_report(self.base(type="explore"))
        self.assertEqual(report["type"], "explore")

    def test_custom_type_is_accepted(self):
        report = self.core.record_report(
            self.base(type="research"),
            {"sid": "type-session", "workspace": "/tmp/ws"})
        self.assertEqual(report["type"], "research")
        task = self.core.load_open()["tasks"]["type-work"]
        self.assertEqual(task["type"], "research")

    def test_custom_type_keeps_working_through_baseline(self):
        self.core.record_report(self.base(type="research", difficulty="m"),
                                {"sid": "type-session", "workspace": "/tmp/ws"})
        health = self.core.work_health("type-work")
        self.assertEqual(health["type"], "research")
        self.assertEqual(health["baseline_source"], "default")

    def test_type_must_be_a_short_slug(self):
        for bad in ("Worse Type", "设计", "", "x" * 33, "-leading-hyphen"):
            if bad == "":
                continue
            with self.assertRaises(ValueError, msg=bad):
                self.core.validate_report(self.base(type=bad))


if __name__ == "__main__":
    unittest.main()
