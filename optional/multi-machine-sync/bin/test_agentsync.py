import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agentsync


class MergeMemoryTest(unittest.TestCase):
    def test_schedules_index_push_when_local_is_peer_superset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            peer_root = root / "peer"
            conflicts = root / "conflicts"
            local_memory = home / ".qoder/memory"
            peer_memory = peer_root / "mini/.qoder/memory"
            local_memory.mkdir(parents=True)
            peer_memory.mkdir(parents=True)

            shared = "# User Memory Index\n\n- [Shared](shared.md)\n"
            extra = "- [Local only](local-only.md)\n"
            (local_memory / "MEMORY.md").write_text(shared + extra, encoding="utf-8")
            (peer_memory / "MEMORY.md").write_text(shared, encoding="utf-8")

            cfg = agentsync.Config(
                machine="laptop",
                role="orchestrator",
                peer_name="mini",
                peer_ssh="",
                peer_ip="",
                peer_local_root="",
                project_map={},
            )
            with (
                patch.object(agentsync, "HOME", home),
                patch.object(agentsync, "PEER_ROOT", peer_root),
                patch.object(agentsync, "CONFLICTS", conflicts),
            ):
                result = agentsync.merge_memory(cfg)

            pushed = {item["rel"] for item in result["push_files"]}
            self.assertIn(".qoder/memory/MEMORY.md", pushed)


if __name__ == "__main__":
    unittest.main()
