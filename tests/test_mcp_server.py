"""MCP 服务名与工具名的契约。

Claude Code 侧的工具 id 由「插件 + 服务名 + 工具名」拼出来，所以这两层名字一旦
漂移，权限白名单和旧会话都会静默失效。这里把契约钉在进程边界上：起真的 stdio
服务，发 JSON-RPC，看它自己怎么报名字。

另外锁两件事：
- 工具名应该是 `crop_update`（它做的是工作状态更新，后面还要承载工作生命周期、改进记录等，不再只做 report）；
- 旧名 `crop` / `probe_report` 作为调用别名保留，避免升级后旧会话直接报 unknown tool。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "core", "probe_mcp_server.py")


class McpServerContractTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ)
        env["KIT_DATA_DIR"] = self._tmp.name
        env["KIT_HOST"] = "mcp-contract"
        env.pop("KIT_SHARED_HOME", None)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )

    def tearDown(self):
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except Exception:
                pass
        self.proc.kill()
        self.proc.wait(timeout=5)
        self._tmp.cleanup()

    def rpc(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "MCP server closed the stream")
        return json.loads(line)

    def initialize(self):
        return self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "contract-test", "version": "1"},
                    "capabilities": {},
                },
            }
        )

    def test_server_reports_crop_as_its_name(self):
        reply = self.initialize()
        self.assertEqual(reply["result"]["serverInfo"]["name"], "crop")

    def test_tools_list_exposes_crop_update(self):
        self.initialize()
        reply = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in reply["result"]["tools"]]
        self.assertEqual(names, ["crop_update", "crop_health", "crop_adjust"])

    def test_crop_tool_records_a_report(self):
        self.initialize()
        reply = self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "crop_update",
                    "arguments": {
                        "phase": "start",
                        "task_id": "mcp-contract",
                        "summary": "验证 MCP 工具名",
                    },
                },
            }
        )
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("recorded start", reply["result"]["content"][0]["text"])

    def test_legacy_crop_tool_name_still_works(self):
        self.initialize()
        reply = self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "crop",
                    "arguments": {
                        "phase": "progress",
                        "task_id": "mcp-contract",
                        "summary": "crop 旧名兼容",
                        "expect_min": 5,
                    },
                },
            }
        )
        self.assertFalse(reply["result"]["isError"])

    def test_legacy_tool_name_still_works(self):
        self.initialize()
        reply = self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "probe_report",
                    "arguments": {
                        "phase": "progress",
                        "task_id": "mcp-contract",
                        "summary": "旧名兼容",
                        "expect_min": 5,
                    },
                },
            }
        )
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("recorded progress", reply["result"]["content"][0]["text"])

    def test_rich_report_fields_are_accepted(self):
        self.initialize()
        self.rpc({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "crop_update", "arguments": {
                "phase": "start", "task_id": "mcp-rich", "summary": "开始",
                "expect_min": 20, "verdict": "justified", "type": "implement",
            }},
        })
        reply = self.rpc({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "crop_update", "arguments": {
                "phase": "end", "task_id": "mcp-rich", "summary": "收尾",
                "what": "做完了", "cost": "查资料", "next_time": "先画图",
                "artifact": "docs/plan.md",
            }},
        })
        self.assertFalse(reply["result"]["isError"])
        path = os.path.join(self._tmp.name, "events.jsonl")
        events = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        summaries = [event for event in events if event.get("ev") == "work_summary"]
        self.assertEqual(summaries[-1]["artifact"], "docs/plan.md")

    def test_health_tool_returns_a_report(self):
        self.initialize()
        reply = self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "crop_health", "arguments": {"days": 7}},
            }
        )
        self.assertFalse(reply["result"]["isError"])
        text = reply["result"]["content"][0]["text"]
        self.assertIn("Crop 健康度", text)

    def test_crop_adjust_records_and_reads_the_ledger(self):
        self.initialize()
        record = self.rpc({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {"name": "crop_adjust", "arguments": {
                "action": "record", "metric": "work_minutes",
                "evidence": "PR #29", "note": "少一轮提醒应该更快",
            }},
        })
        self.assertFalse(record["result"]["isError"])
        self.assertIn("recorded adjustment", record["result"]["content"][0]["text"])
        ledger = self.rpc({
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {"name": "crop_adjust", "arguments": {"action": "ledger"}},
        })
        self.assertFalse(ledger["result"]["isError"])
        self.assertIn("改进台账", ledger["result"]["content"][0]["text"])

    def test_crop_adjust_requires_evidence(self):
        self.initialize()
        reply = self.rpc({
            "jsonrpc": "2.0", "id": 23, "method": "tools/call",
            "params": {"name": "crop_adjust", "arguments": {
                "action": "record", "metric": "work_minutes",
                "note": "少一轮提醒应该更快",
            }},
        })
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("evidence", reply["result"]["content"][0]["text"])

    def test_unknown_tool_is_rejected(self):
        self.initialize()
        reply = self.rpc(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "not_a_tool", "arguments": {}},
            }
        )
        self.assertEqual(reply["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
