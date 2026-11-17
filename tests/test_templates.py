"""Inject templates: loading, rendering and fallbacks (DESIGN-CUSTOM-TEMPLATES)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import crop_templates  # noqa: E402


class TemplatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.addCleanup(crop_templates.clear_cache)
        patcher = mock.patch.dict(os.environ, {"KIT_TEMPLATES_DIR": str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, relative: str, body: str, encoding: str = "utf-8") -> Path:
        path = self.dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding=encoding)
        return path

    def test_missing_directory_means_defaults(self):
        self.assertIsNone(crop_templates.load("protocol"))
        self.assertIsNone(crop_templates.render("protocol", {"persona": "x"}))
        self.assertIsNone(crop_templates.voice_lines("yellow"))

    def test_empty_file_falls_back_to_default(self):
        self.write("protocol.md", "   \n")
        self.assertIsNone(crop_templates.load("protocol"))

    def test_none_switches_the_injection_off(self):
        self.write("binding-hint.md", "none")
        self.assertEqual(crop_templates.render("binding-hint", {}), "")

    def test_variables_are_substituted(self):
        self.write("start-hint.md", "mine={{mine}} others={{others}}")
        self.assertEqual(
            crop_templates.render("start-hint", {"mine": "1", "others": "2"}),
            "mine=1 others=2",
        )

    def test_unknown_variable_becomes_empty(self):
        self.write("end-hint.md", "a{{nope}}b")
        self.assertEqual(crop_templates.render("end-hint", {"work_id": "w"}), "ab")

    def test_long_template_is_truncated(self):
        self.write("protocol.md", "x" * (crop_templates.MAX_CHARS + 10))
        rendered = crop_templates.render("protocol", {})
        self.assertEqual(len(rendered), crop_templates.MAX_CHARS)

    def test_voice_lines_split_on_separator(self):
        self.write("voice/yellow.md", "第一句\n---\n第二句\n---\n\n第三句")
        self.assertEqual(
            crop_templates.voice_lines("yellow"),
            ["第一句", "第二句", "第三句"],
        )

    def test_voice_only_separators_falls_back(self):
        self.write("voice/red.md", "---\n---")
        self.assertIsNone(crop_templates.voice_lines("red"))

    def test_cache_follows_mtime(self):
        path = self.write("binding-hint.md", "old")
        self.assertEqual(crop_templates.load("binding-hint"), "old")
        os.utime(path, (1, 1))
        path.write_text("new", encoding="utf-8")
        self.assertEqual(crop_templates.load("binding-hint"), "new")

    def test_broken_encoding_falls_back(self):
        path = self.dir / "protocol.md"
        path.write_bytes(b"\xff\xfe\x00bad")
        self.assertIsNone(crop_templates.load("protocol"))


if __name__ == "__main__":
    unittest.main()
