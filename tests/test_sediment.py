#!/usr/bin/env python3
"""复盘沉淀：sediment 落点字段、二次收尾、健康度沉淀闭合率。

每个用例用独立的 KIT_DATA_DIR，绝不碰真实数据目录。
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


class SedimentTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="crop-sediment-")
        os.environ["KIT_DATA_DIR"] = self.data
        os.environ["KIT_HOST"] = "test"
        import probe_core as core
        self.core = core
        self.sid = "sed-1"

    def tearDown(self):
        os.environ.pop("KIT_DATA_DIR", None)
        os.environ.pop("KIT_HOST", None)

    def report(self, **args):
        return self.core.record_report(
            {"task_id": "sed-work", "summary": "做点事", **args},
            {"sid": self.sid, "workspace": "/tmp/ws"},
        )

    def events(self):
        return self.core.load_events(sid=self.sid)

    def test_end_stores_sediment_pointer(self):
        self.report(phase="start")
        self.report(phase="end", what="做完了", sediment="docs:WORKFLOW.md#复盘沉淀")
        sediments = [event for event in self.events() if event["ev"] == "work_sediment"]
        self.assertEqual(len(sediments), 1)
        self.assertEqual(sediments[0]["sediment"], "docs:WORKFLOW.md#复盘沉淀")
        self.assertEqual(sediments[0]["work_id"], "sed-work")

    def test_second_end_only_patches_sediment(self):
        self.report(phase="start")
        self.report(phase="end", what="做完了", sediment="none:一次性")
        second = self.report(phase="end", what="补沉淀", summary="补沉淀", sediment="skill:crop-retro")
        self.assertTrue(second.get("already_closed"))
        closes = [event for event in self.events() if event["ev"] == "work_close"]
        self.assertEqual(len(closes), 1, "二次收尾不能重复关单")
        sediments = [event for event in self.events() if event["ev"] == "work_sediment"]
        self.assertEqual(sediments[-1]["sediment"], "skill:crop-retro")

    def test_end_without_sediment_still_closes(self):
        self.report(phase="start")
        self.report(phase="end", what="完")
        kinds = [event["ev"] for event in self.events()]
        self.assertIn("work_close", kinds)
        self.assertNotIn("work_sediment", kinds)

    def test_health_counts_sediment_closure(self):
        self.core.record_session_start(self.sid, "/tmp/ws")
        self.core.record_prompt(self.sid, "开工", "/tmp/ws")
        self.report(phase="start")
        self.report(phase="end", what="做完了", sediment="docs:x")
        payload = self.core.health_summary(days=7)
        block = payload.get("sediment") or {}
        self.assertEqual(block.get("ended"), 1)
        self.assertEqual(block.get("with_sediment"), 1)
        self.assertEqual(block.get("rate"), 1.0)
        self.assertIn("沉淀闭合", self.core.format_health(payload))


if __name__ == "__main__":
    unittest.main()
