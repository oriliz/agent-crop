#!/usr/bin/env python3
"""Qoder 会话 JSONL -> 紧凑摘要 Markdown。

摘要用于跨机交接：只保留标题、最近请求、助手结论、涉及文件与命令，
不复制全文。可由 agentsync.py 导入，也可单独运行：

  python3 session_digest.py --file <session.jsonl> --machine mini [--stdout]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime

MAX_TITLE = 120
MAX_USER = 500
MAX_ASSISTANT = 1100
MAX_FILES = 15
MAX_COMMANDS = 10

NOISE_PREFIXES = (
    "<system-reminder>",
    "<command-name>",
    "<local-command",
    "[Request interrupted",
)


@dataclass
class DigestMeta:
    session_id: str
    title: str
    start: str
    end: str
    cwd: str
    branch: str
    machine: str
    source: str
    message_count: int


def _collapse(text: str, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _is_noise(text: str) -> bool:
    t = (text or "").lstrip()
    return any(t.startswith(p) for p in NOISE_PREFIXES)


def _iso_to_local(ts) -> str:
    if ts is None:
        return ""
    try:
        if isinstance(ts, (int, float)):
            seconds = ts / 1000 if ts > 1e11 else ts
            dt = datetime.fromtimestamp(seconds)
        else:
            s = str(ts)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:19]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("text", "input_text") and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _has_tool_result(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def parse_session(path: str) -> tuple[str, DigestMeta]:
    """解析一个会话文件，返回 (markdown, meta)。"""
    session_id = os.path.splitext(os.path.basename(path))[0]
    machine = ""
    first_human = None
    human_texts: list[str] = []
    assistant_texts: list[str] = []
    files: list[str] = []
    commands: list[str] = []
    start_ts = None
    end_ts = None
    cwd = ""
    branch = ""
    workspace_dirs: list[str] = []
    message_count = 0

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            typ = obj.get("type")
            ts = obj.get("timestamp")
            if ts is not None:
                start_ts = start_ts if start_ts is not None else ts
                end_ts = ts
            if obj.get("cwd"):
                cwd = obj["cwd"]
            gb = obj.get("gitBranch")
            if gb and gb != "HEAD":
                branch = gb

            if typ == "workspace-directories":
                workspace_dirs = obj.get("directories") or []
                continue
            if typ not in ("user", "assistant"):
                continue
            if obj.get("isSidechain"):
                continue
            message_count += 1

            if typ == "user":
                hi = obj.get("humanInput")
                text = hi.get("text") if isinstance(hi, dict) else None
                if not text:
                    content = (obj.get("message") or {}).get("content")
                    if _has_tool_result(content) and not _content_text(content).strip():
                        continue
                    text = _content_text(content)
                if not text or not text.strip() or _is_noise(text):
                    continue
                if first_human is None:
                    first_human = text
                human_texts.append(text)
            else:
                content = (obj.get("message") or {}).get("content")
                if isinstance(content, str):
                    if content.strip():
                        assistant_texts.append(content)
                    continue
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and isinstance(block.get("text"), str):
                        if block["text"].strip():
                            assistant_texts.append(block["text"])
                    elif btype == "tool_use":
                        inp = block.get("input") or {}
                        if not isinstance(inp, dict):
                            continue
                        for key in ("file_path", "path", "notebook_path", "target_file", "output_path", "output_file"):
                            val = inp.get(key)
                            if isinstance(val, str) and val.strip():
                                files.append(val.strip())
                        cmd = inp.get("command")
                        if isinstance(cmd, str) and cmd.strip():
                            commands.append(cmd.strip().splitlines()[0])

    if cwd == "" and workspace_dirs:
        cwd = workspace_dirs[0]

    title = _collapse(first_human, MAX_TITLE) if first_human else "未命名会话"
    start = _iso_to_local(start_ts)
    end = _iso_to_local(end_ts)

    def dedupe(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    files = dedupe(files)
    commands = dedupe(commands)

    lines: list[str] = [
        "---",
        f"session_id: {json.dumps(session_id, ensure_ascii=False)}",
        f"machine: {json.dumps(machine, ensure_ascii=False)}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"start: {json.dumps(start, ensure_ascii=False)}",
        f"end: {json.dumps(end, ensure_ascii=False)}",
        f"cwd: {json.dumps(cwd, ensure_ascii=False)}",
        f"branch: {json.dumps(branch, ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    recent_users = human_texts[-4:]
    if recent_users:
        lines.append("## 最近请求")
        if len(human_texts) > len(recent_users):
            lines.append(f"- （共 {len(human_texts)} 条，以下为最后 {len(recent_users)} 条）")
        for text in recent_users:
            lines.append(f"- {_collapse(text, MAX_USER)}")
        lines.append("")

    recent_assistant = assistant_texts[-2:]
    if recent_assistant:
        lines.append("## 助手结论")
        for text in recent_assistant:
            lines.append(f"- {_collapse(text, MAX_ASSISTANT)}")
        lines.append("")

    if files:
        lines.append("## 涉及文件")
        for path_item in files[-MAX_FILES:]:
            lines.append(f"- {_collapse(path_item, 220)}")
        lines.append("")

    if commands:
        lines.append("## 执行命令")
        for cmd in commands[-MAX_COMMANDS:]:
            lines.append(f"- `{_collapse(cmd, 180)}`")
        lines.append("")

    lines.append("## 元信息")
    lines.append(f"- 会话文件：{path}")
    lines.append(f"- 消息数：{message_count}")
    if workspace_dirs:
        lines.append(f"- 工作区：{_collapse(', '.join(workspace_dirs), 300)}")
    lines.append("")

    meta = DigestMeta(
        session_id=session_id,
        title=title,
        start=start,
        end=end,
        cwd=cwd,
        branch=branch,
        machine=machine,
        source=path,
        message_count=message_count,
    )
    return "\n".join(lines), meta


def build_with_machine(path: str, machine: str) -> tuple[str, DigestMeta]:
    markdown, meta = parse_session(path)
    meta.machine = machine
    markdown = markdown.replace(
        f"machine: {json.dumps('', ensure_ascii=False)}",
        f"machine: {json.dumps(machine, ensure_ascii=False)}",
        1,
    )
    return markdown, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Qoder 会话 JSONL 生成紧凑摘要")
    parser.add_argument("--file", required=True, help="会话 .jsonl 路径")
    parser.add_argument("--machine", default="", help="机器标识，如 mini / laptop")
    parser.add_argument("--out", default="", help="输出 md 路径；缺省打印到 stdout")
    parser.add_argument("--json", action="store_true", help="输出 meta JSON")
    args = parser.parse_args()

    markdown, meta = build_with_machine(args.file, args.machine)
    if args.json:
        print(json.dumps(asdict(meta), ensure_ascii=False, indent=2))
        return 0
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
