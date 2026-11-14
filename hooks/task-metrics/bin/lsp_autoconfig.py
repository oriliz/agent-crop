#!/usr/bin/env python3
"""按仓库主语言把 LSP 注册成 MCP 服务，写到 <repo>/.mcp.json。

用法：lsp_autoconfig.py [--workspace DIR] [--dry-run]
Qoder 读工作区根的 .mcp.json（已实测），所以按仓自动挂、不污染全局配置。
"""
import argparse
import json
import os
import shutil
import sys

MCP_LS = os.path.expanduser("~/.local/share/mcp-language-server-patched/mcp-language-server")

# 先认强标志（.mod/tsconfig/pom），再退回弱标志（只有 package.json 的多半是工具目录）
STRONG = [
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts"), "jdtls"),
    ("go", ("go.mod",), "gopls"),
    ("ts", ("tsconfig.json",), "typescript-language-server"),
    ("py", ("pyproject.toml", "setup.py"), "pyright-langserver"),
]
WEAK = [("ts", ("package.json",), "typescript-language-server")]
ARGS = {"ts": ["--stdio"], "py": ["--stdio"]}


def detect(root):
    for table in (STRONG, WEAK):
        for lang, markers, cmd in table:
            if any(os.path.exists(os.path.join(root, m)) for m in markers):
                extra = list(ARGS.get(lang, []))
                if lang == "java":
                    tag = "".join(c if c.isalnum() else "-" for c in root).strip("-")
                    extra = ["-data", os.path.expanduser(f"~/.local/share/qoder-lsp/{tag}")]
                return lang, cmd, extra
    return None, None, None


def entry(root, cmd_bin, extra):
    args = ["-workspace", root, "-lsp", cmd_bin]
    if extra:
        args += ["--"] + extra
    return {"type": "stdio", "command": MCP_LS, "args": args}


def already_global(root):
    """全局 settings.json 里已有绑到同一 workspace 的 LSP 条目就不再重复挂。"""
    cfg = os.path.join(os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"),
                       "settings.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            servers = json.load(f).get("mcpServers") or {}
    except Exception:
        return False
    for v in servers.values():
        args = v.get("args") or [] if isinstance(v, dict) else []
        for i, a in enumerate(args[:-1]):
            if a == "-workspace" and os.path.abspath(args[i + 1]) == root:
                return True
    return False


def exclude_from_git(root):
    path = os.path.join(root, ".git", "info", "exclude")
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        lines = [x.strip() for x in f]
    if ".mcp.json" in lines:
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n# lsp_autoconfig\n.mcp.json\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.workspace)

    if not os.path.isdir(root) or not os.path.exists(MCP_LS) or already_global(root):
        return 0
    lang, cmd, extra = detect(root)
    if not lang:
        return 0
    cmd_bin = shutil.which(cmd)
    if not cmd_bin:
        return 0

    key = "lsp-" + lang
    want = entry(root, cmd_bin, extra)
    path = os.path.join(root, ".mcp.json")
    try:
        with open(path, encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        conf = {}
    servers = conf.setdefault("mcpServers", {})
    if servers.get(key) == want:
        return 0

    servers[key] = want
    if a.dry_run:
        print(json.dumps({key: want}, ensure_ascii=False, indent=2))
        return 0

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    exclude_from_git(root)
    print(f"[lsp] 本仓（{lang}）已注册 {key}（.mcp.json）；"
          f"若本会话工具列表暂无，下一会话生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
