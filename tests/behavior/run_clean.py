#!/usr/bin/env python3
"""干净环境行为测试。

规则：任何一次行为测试都必须跑在独立的 DSH_HOME 里，且跑完先验证三件事，
不通过就判 INVALID，不进结果。

用法：
    python3 tests/behavior/run_clean.py --scenario s1 --prompt "继续吧" \
        --plugin ~/AgentProjects/agent-crop-e2e

三个校验：
1. 会话里第一条用户消息 == 我发的 prompt
2. 注入里出现当前版本的标记文本（默认「绑定只用 probe_report」）
3. 没有第三方 agent/inbox/spliced（source.kind == "user" 且文本 != prompt）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
DEFAULT_HOME = HOME / ".dsh-e2e"
DEFAULT_PROFILE = "lab"
MARKER = "绑定后先报预期"


def read_key(dsh_home: Path) -> str:
    cred = dsh_home / ".credentials.yaml"
    for line in cred.read_text().splitlines():
        if line.strip().startswith("DEEPSEEK_API_KEY:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit(f"找不到 DEEPSEEK_API_KEY：{cred}")


def seed(data_dir: Path, workspace: str) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
    os.environ["KIT_DATA_DIR"] = str(data_dir)
    import probe_core as core  # noqa: PLC0415

    core.record_report(
        {"phase": "start", "task_id": "alpha-doc-rework", "summary": "重写接入文档", "goal": "重写接入文档"},
        {"sid": "seed-a", "workspace": workspace},
    )
    time.sleep(0.05)
    core.record_report(
        {"phase": "start", "task_id": "beta-panel-spec", "summary": "定面板功能点", "goal": "定面板功能点"},
        {"sid": "seed-b", "workspace": workspace},
    )


def latest_session(dsh_home: Path, after: float) -> Path | None:
    best = None
    for path in (dsh_home / "sessions").glob("*/session-*"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= after and (best is None or mtime > best[0]):
            best = (mtime, path)
    return best[1] if best else None


def load_rows(session_dir: Path) -> list[dict]:
    blob = subprocess.run(
        ["zstd", "-dc", str(session_dir / "session.v4.jsonl.zstd")],
        capture_output=True, check=True,
    ).stdout.decode()
    return [json.loads(line) for line in blob.splitlines() if line.strip()]


def text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--plugin", default=str(HOME / ".agentcrop" / "current"))
    ap.add_argument("--workspace", default=str(HOME / "AgentProjects"))
    ap.add_argument("--dsh-home", default=str(DEFAULT_HOME))
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--marker", default=MARKER)
    ap.add_argument("--expect-tools", default="",
                    help="逗号分隔；这些工具都必须出现过，否则 INVALID")
    args = ap.parse_args()

    dsh_home = Path(args.dsh_home).expanduser()
    data_dir = Path("/tmp") / f"crop-clean-{args.scenario}" / "data"
    if data_dir.parent.exists():
        shutil.rmtree(data_dir.parent)
    data_dir.mkdir(parents=True)

    seed(data_dir, args.workspace)

    env = dict(os.environ)
    env.update({
        "DSH_HOME": str(dsh_home),
        "KIT_DATA_DIR": str(data_dir),
        "DEEPSEEK_API_KEY": read_key(dsh_home),
    })

    started = time.time()
    proc = subprocess.run(
        ["dsh", "--profile", args.profile, args.prompt],
        cwd=args.workspace, env=env, capture_output=True, text=True, timeout=900,
    )

    session = latest_session(dsh_home, started - 5)
    result = {
        "scenario": args.scenario,
        "prompt": args.prompt,
        "exit": proc.returncode,
        "session": str(session) if session else None,
        "checks": {},
        "verdict": "INVALID",
    }

    if session is None:
        result["checks"]["session_found"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    rows = load_rows(session)
    user_texts = [text_of(r.get("data", {})) for r in rows if r.get("type") == "user/message"]
    spliced = [
        text_of(item) for r in rows if r.get("type") == "agent/inbox/spliced"
        for item in r.get("data", {}).get("inserted", [])
        if item.get("source", {}).get("kind") == "user"
    ]
    injected = "\n".join(user_texts)

    result["checks"]["user_message_matches"] = args.prompt in injected
    result["checks"]["injection_has_marker"] = args.marker in injected
    result["checks"]["no_foreign_spliced"] = all(t.strip() == args.prompt for t in spliced) if spliced else True
    result["spliced_user_messages"] = [t[:80] for t in spliced]

    tool_calls = [
        {"name": r.get("data", {}).get("name"), "args": r.get("data", {}).get("arguments")}
        for r in rows if r.get("type") == "tool/call"
    ]
    result["tool_calls"] = len(tool_calls)
    result["first_tools"] = tool_calls[:5]
    names = [call["name"] for call in tool_calls]
    result["all_tool_names"] = list(dict.fromkeys(names))

    def is_bind(call):
        if call["name"] in ("probe_report", "work_claim"):
            return True
        if call["name"] == "crop_update":
            return "start" in str(call.get("args") or "")
        return False

    result["first_bind_tool_index"] = next((i for i, call in enumerate(tool_calls)
                                            if is_bind(call)), None)

    reply_parts = [
        block.get("text", "")
        for row in rows if row.get("type") == "assistant/message"
        for block in row.get("data", {}).get("message", {}).get("content", [])
        if block.get("type") == "text" and block.get("text", "").strip()
    ]
    result["final_text"] = reply_parts[-1][:2000] if reply_parts else ""

    expected = [name.strip() for name in args.expect_tools.split(",") if name.strip()]
    if expected:
        result["checks"]["expect_tools"] = all(name in names for name in expected)

    events = []
    events_file = data_dir / "events.jsonl"
    if events_file.exists():
        for line in events_file.read_text().splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("ev") in ("probe_report", "work_claim", "side_issue", "session_start"):
                events.append({k: ev.get(k) for k in ("ev", "task_id", "work_id", "sid", "summary", "note")})
    result["events"] = events

    # 绑定是否发生在第一批工具调用之前：事件里第一次 probe_report/work_claim 的序号
    first_bind = next((i for i, e in enumerate(events) if e["ev"] in ("probe_report", "work_claim")), None)
    result["first_bind_event_index"] = first_bind

    result["verdict"] = "VALID" if all(result["checks"].values()) else "INVALID"
    out = Path(__file__).parent / "traces"
    out.mkdir(exist_ok=True)
    (out / f"{args.scenario}-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
