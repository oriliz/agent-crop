#!/usr/bin/env python3
"""ACP replay: build a long-ish session, fork a work, restart the process, keep asserting.

Run with an isolated home + data dir:
    DSH_HOME=/tmp/dsh-acp-replay KIT_DATA_DIR=/tmp/crop-acp-data \
        python3 tests/acp_replay.py
"""

import json
import os
import subprocess
import sys

CWD = "/Users/lizhi/AgentProjects/agent-crop"
PROMPTS = [
    ("warmup", "用 bash 数一下 README 有多少行，一句话回答。"),
    ("work", "用 bash 数一下 core 目录里有多少个 py 文件，一句话回答。"),
    ("fork", "这是一件新的事：请调 crop_report(phase=\"start\", task_id=\"replay-fork\", summary=\"回放分叉测试\")，"
             "然后用 bash 看一眼 bin 目录有几个文件。"),
    ("continue", "继续做：把 README 的前三行内容用一句话概括。"),
    ("continue2", "再看看 docs 目录有几个 md 文件，一句话回答。"),
]


class Acp:
    def __init__(self, home, data):
        env = dict(os.environ, DSH_HOME=home, KIT_DATA_DIR=data)
        self.proc = subprocess.Popen(
            ["dsh", "--profile", "acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=CWD, env=env, text=True, bufsize=1,
        )
        self.seq = 0
        self.sid = None
        self.used = None

    def send(self, method, params):
        self.seq += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return self.seq

    def wait(self, target, timeout=240):
        import time
        start = time.time()
        while time.time() - start < timeout:
            line = self.proc.stdout.readline()
            if not line:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("method") == "session/update":
                upd = msg.get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "usage_update":
                    self.used = upd.get("used")
            if msg.get("id") == target:
                return msg
        return None

    def boot(self, resume=False):
        self.wait(self.send("initialize", {"protocolVersion": 1, "clientCapabilities": {}}))
        if resume and self.sid:
            reply = self.wait(self.send("session/resume", {"sessionId": self.sid, "cwd": CWD, "mcpServers": []}))
        else:
            reply = self.wait(self.send("session/new", {"cwd": CWD, "mcpServers": []}))
            self.sid = reply["result"]["sessionId"]
        assert "result" in reply, reply

    def prompt(self, text):
        return self.wait(self.send("session/prompt", {"sessionId": self.sid, "prompt": [{"type": "text", "text": text}]}))

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def events(data):
    path = os.path.join(data, "events.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def main(home, data):
    client = Acp(home, data)
    client.boot()
    print(f"session {client.sid}")
    for name, text in PROMPTS:
        reply = client.prompt(text)
        error = (reply or {}).get("error")
        print(f"[{name}] {'error: ' + error['message'] if error else 'ok'} | context {client.used}")
    client.close()

    # restart: a fresh process must resume the same session and keep the binding
    client2 = Acp(home, data)
    client2.sid = client.sid
    client2.boot(resume=True)
    reply = client2.prompt("只回复 OK")
    print(f"[restart] {'error: ' + reply['error']['message'] if (reply or {}).get('error') else 'ok'} | context {client2.used}")
    client2.close()

    rows = events(data)
    sid = client.sid
    kinds = [e.get("ev") for e in rows if e.get("sid") == sid]
    print("\n-- invariants --")
    bound = [e for e in rows if e.get("ev") in ("work_open", "work_claim") and e.get("sid") == sid]
    print(f"[{'PASS' if bound else 'FAIL'}] 会话产生了工作绑定 — {len(bound)} 条")
    stops = [e for e in rows if e.get("ev") == "stop" and e.get("sid") == sid]
    owned = [e for e in stops if e.get("work_id")]
    print(f"[{'PASS' if owned else 'FAIL'}] 块带 work_id — {len(owned)}/{len(stops)}")
    forks = [e for e in rows if e.get("work_id") == "replay-fork" or e.get("task_id") == "replay-fork"]
    print(f"[{'PASS' if forks else 'FAIL'}] 分叉工作被记录 — {len(forks)} 条")
    print(f"[INFO] 该会话事件: {kinds}")

    # reuse the data-layer checker for global invariants
    subprocess.run([sys.executable, "tests/replay_check.py", data], cwd="/Users/lizhi/AgentProjects/agent-crop")


if __name__ == "__main__":
    main(os.environ.get("DSH_HOME", "/tmp/dsh-acp-replay"), os.environ.get("KIT_DATA_DIR", "/tmp/crop-acp-data"))
