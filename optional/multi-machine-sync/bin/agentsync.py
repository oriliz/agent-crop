#!/usr/bin/env python3
"""跨机协同：记忆双向合并、会话摘要、交接台账。

角色（名称由 ~/.agent-sync/config.env 里的 MACHINE / PEER_NAME 指定）：
  hub          被动端 —— 只写本机摘要/交接，等待 orchestrator 同步
  orchestrator 主动端 —— 拉取对端记忆/摘要，合并后推回

命令：
  sync                 发起一次同步（仅 orchestrator 有效）
  digest-scan          扫描本机会话，补齐摘要
  session-start        SessionStart 钩子入口（后台同步 + 打印对端简报）
  session-end          SessionEnd 钩子入口（生成本场摘要 + 后台同步）
  brief                打印对端交接简报
  status               查看配置与同步状态
  conflicts            查看最近一次冲突
  install-hooks        向 Qoder settings.json 追加/移除钩子
  init                 创建目录、配置与交接模板

所有状态都在 ~/.agent-sync/，不上云。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_digest  # noqa: E402

VERSION = "0.1.0"
QUIET = False

HOME = Path(os.environ.get("HOME") or str(Path.home()))
AS_HOME = Path(os.environ.get("AGENTSYNC_HOME") or HOME / ".agent-sync")
SHARED = AS_HOME / "shared"
STATE = AS_HOME / "state"
PEER_ROOT = AS_HOME / "peer"
CONFLICTS = AS_HOME / "conflicts"
LOGS = AS_HOME / "logs"
LOG_PATH = LOGS / "sync.log"
CONFIG_PATH = AS_HOME / "config.env"

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
]

HANDOFF_TEMPLATE = """---
machine: {machine}
updated: {updated}
---
# 交接台账 · {machine}

> 一段工作结束或切换机器前更新这里；对端新会话会自动读取。

## 当前焦点
- （待填写）

## 已完成 / 进行中
- （待填写）

## 下一步
- （待填写）

## 阻塞 / 待拍板
- （待填写）

## 关键分支 / 路径
- （待填写）
"""


@dataclass
class Config:
    machine: str
    role: str
    peer_name: str
    peer_ssh: str
    peer_ip: str
    peer_local_root: str
    project_map: dict[str, str]
    peer_ssh_fallbacks: tuple[str, ...] = ()
    peer_link_if: str = ""

    @property
    def is_orchestrator(self) -> bool:
        return self.role == "orchestrator"

    @property
    def local_ns(self) -> Path:
        return SHARED / self.machine

    @property
    def peer_ns(self) -> Path:
        return SHARED / self.peer_name

    @property
    def peer_mirror(self) -> Path:
        return PEER_ROOT / self.peer_name


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_short() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str, error: bool = False) -> None:
    line = f"[{now_short()}] {msg}"
    if not QUIET or error:
        print(line, file=sys.stderr if error else sys.stdout, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5 * 1024 * 1024:
            shutil.copyfile(LOG_PATH, LOG_PATH.with_name("sync.log.1"))
            LOG_PATH.write_text("", encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def load_config(require: bool = True) -> Config:
    data: dict[str, str] = {}
    if CONFIG_PATH.exists():
        try:
            for raw in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
        except Exception as exc:
            raise SystemExit(f"无法读取 {CONFIG_PATH}: {exc}")
    if require and not CONFIG_PATH.exists():
        raise SystemExit(f"尚未初始化：{CONFIG_PATH}（先跑 install.sh 或 init）")

    machine = os.environ.get("AGENTSYNC_MACHINE") or data.get("MACHINE") or socket.gethostname().split(".")[0]
    role = os.environ.get("AGENTSYNC_ROLE") or data.get("ROLE") or "hub"
    peer_name = os.environ.get("AGENTSYNC_PEER_NAME") or data.get("PEER_NAME") or "peer"
    peer_ssh = os.environ.get("AGENTSYNC_PEER_SSH") or data.get("PEER_SSH") or ""
    peer_ip = os.environ.get("AGENTSYNC_PEER_IP") or data.get("PEER_IP") or ""
    peer_local = os.environ.get("AGENTSYNC_PEER_LOCAL_ROOT") or ""
    raw_fallbacks = os.environ.get("AGENTSYNC_PEER_SSH_FALLBACKS") or data.get("PEER_SSH_FALLBACKS") or ""
    peer_ssh_fallbacks = tuple(
        item.strip() for item in raw_fallbacks.split(",") if item.strip()
    )
    peer_link_if = os.environ.get("AGENTSYNC_PEER_LINK_IF") or data.get("PEER_LINK_IF") or ""
    raw_map = os.environ.get("AGENTSYNC_PROJECT_MAP") or data.get("PROJECT_MAP") or ""
    project_map: dict[str, str] = {}
    for pair in raw_map.split(","):
        if ":" not in pair:
            continue
        local_slug, peer_slug = pair.split(":", 1)
        if local_slug.strip() and peer_slug.strip():
            project_map[local_slug.strip()] = peer_slug.strip()
    return Config(
        machine=machine,
        role=role,
        peer_name=peer_name,
        peer_ssh=peer_ssh,
        peer_ip=peer_ip,
        peer_local_root=peer_local,
        project_map=project_map,
        peer_ssh_fallbacks=peer_ssh_fallbacks,
        peer_link_if=peer_link_if,
    )


def write_config(cfg: Config) -> None:
    AS_HOME.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 跨机协同配置（由 install.sh 生成）",
        f"MACHINE={cfg.machine}",
        f"ROLE={cfg.role}",
        f"PEER_NAME={cfg.peer_name}",
        f"PEER_SSH={cfg.peer_ssh}",
        f"PEER_IP={cfg.peer_ip}",
        f"PEER_SSH_FALLBACKS={','.join(cfg.peer_ssh_fallbacks)}",
        f"PEER_LINK_IF={cfg.peer_link_if}",
        "",
    ]
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def ensure_dirs(cfg: Config) -> None:
    for path in (
        AS_HOME / "bin",
        SHARED,
        cfg.local_ns / "sessions",
        cfg.peer_ns,
        STATE,
        PEER_ROOT,
        CONFLICTS,
        LOGS,
    ):
        path.mkdir(parents=True, exist_ok=True)


PEER_ACTIVE_PATH = STATE / "peer-active.txt"


def link_is_up(ifname: str) -> bool:
    """直连网卡是否插着且拿到了 IPv4（macOS 断开时 status: inactive）。"""
    if not ifname:
        return False
    try:
        result = subprocess.run(
            ["ifconfig", ifname],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=3,
        )
    except Exception:
        return False
    if result.returncode != 0 or "status: active" not in result.stdout:
        return False
    return any(line.strip().startswith("inet ") for line in result.stdout.splitlines())


def peer_candidates(cfg: Config) -> list[str]:
    """返回可尝试的对端 SSH 目标。

    配置了 PEER_LINK_IF 时按网卡实际状态排序：网桥接着就优先直连，
    拔掉了就直接走内网 fallback，不先等主地址超时。
    """
    try:
        cached = PEER_ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        cached = ""
    if cfg.peer_link_if:
        if link_is_up(cfg.peer_link_if):
            raw = [cfg.peer_ssh, cached, *cfg.peer_ssh_fallbacks]
        else:
            cached_alt = cached if cached != cfg.peer_ssh else ""
            raw = [cached_alt, *cfg.peer_ssh_fallbacks, cfg.peer_ssh]
    else:
        raw = [cached, cfg.peer_ssh, *cfg.peer_ssh_fallbacks]
    ordered: list[str] = []
    for item in raw:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def mark_peer_active(target: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        PEER_ACTIVE_PATH.write_text(target + "\n", encoding="utf-8")
    except Exception:
        pass


def ssh_run(cfg: Config, remote: str, **kwargs) -> subprocess.CompletedProcess:
    if not cfg.peer_ssh:
        raise RuntimeError("未配置 PEER_SSH")
    last: subprocess.CompletedProcess | None = None
    for target in peer_candidates(cfg):
        result = subprocess.run(["ssh", *SSH_OPTS, target, remote], **kwargs)
        last = result
        if result.returncode != 255:
            mark_peer_active(target)
            return result
        log(f"SSH {target} 连接失败，尝试下一个地址", error=True)
    return last


def rsync_run(cfg: Config, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """执行含 {target} 占位的 rsync 命令；连接类失败时换候选地址重试。"""
    last: subprocess.CompletedProcess | None = None
    for target in peer_candidates(cfg):
        cmd = [part.replace("{target}", target) for part in argv]
        result = subprocess.run(cmd, **kwargs)
        last = result
        if result.returncode not in (255, 12):
            mark_peer_active(target)
            return result
        log(f"rsync 连接 {target} 失败，尝试下一个地址", error=True)
    return last


PEER_EXPORT_REL = ".agent-sync/export/qoder"


def refresh_peer_export(cfg: Config) -> None:
    """让对端把 Qoder 记忆汇总为 rsync 可读的导出镜像。

    企业安全策略会拦截 sshd 会话里 tar/cat/cp/shasum 对项目记忆的读取，
    但 rsync 不受影响，所以同步统一走 ~/.agent-sync/export/qoder。
    """
    if cfg.peer_local_root:
        return
    remote = (
        'script="$HOME/.agent-sync/bin/export_memory.sh"; '
        'if [ -f "$script" ]; then bash "$script"; else echo "缺少 $script" >&2; exit 3; fi'
    )
    result = ssh_run(cfg, remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        if not detail:
            detail = result.stdout.decode("utf-8", "replace").strip()
        raise RuntimeError(f"刷新对端记忆导出失败：{detail}")


def fetch_peer_memory(cfg: Config) -> None:
    mirror = cfg.peer_mirror
    tmp = mirror.with_name(mirror.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    if cfg.peer_local_root:
        root = Path(cfg.peer_local_root)
        for rel in memory_dir_rels(root):
            src = root / rel
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, copy_function=shutil.copy2)
    else:
        refresh_peer_export(cfg)
        result = rsync_run(
            cfg,
            ["rsync", "-a", "--checksum", "--delete",
             "{target}:" + PEER_EXPORT_REL + "/", f"{tmp}/"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode not in (0, 24):
            raise RuntimeError(f"拉取对端记忆失败：{result.stderr.strip()}")

    shutil.rmtree(mirror, ignore_errors=True)
    tmp.rename(mirror)
    log(f"已拉取对端记忆：{count_md(mirror)} 个 md")


def memory_dir_rels(root: Path) -> list[Path]:
    rels: list[Path] = []
    if (root / ".qoder/memory").is_dir():
        rels.append(Path(".qoder/memory"))
    projects = root / ".qoder/projects"
    if projects.is_dir():
        for item in sorted(projects.iterdir()):
            if (item / "memory").is_dir():
                rels.append(Path(".qoder/projects") / item.name / "memory")
    return rels


def memory_files_rel(root: Path) -> list[str]:
    out: list[str] = []
    for rel in memory_dir_rels(root):
        base = root / rel
        for path in base.rglob("*.md"):
            if path.is_file():
                out.append(str(path.relative_to(root)))
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_md(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for _ in root.rglob("*.md"))


def peer_hashes(cfg: Config) -> dict[str, str]:
    """返回对端所有记忆 md 的 {相对 $HOME 路径: sha256}。

    远程模式先刷新导出镜像再拉取，哈希在本地计算：对端的安全策略会
    拦截 shasum 等二进制读取记忆，导出镜像则不受限。
    """
    if cfg.peer_local_root:
        root = Path(cfg.peer_local_root)
        return {rel: sha256_file(root / rel) for rel in memory_files_rel(root)}

    fetch_peer_memory(cfg)
    mirror = cfg.peer_mirror
    return {rel: sha256_file(mirror / rel) for rel in memory_files_rel(mirror)}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def union_memory_index(local_text: str, peer_text: str) -> str:
    def parse(text: str):
        header: list[str] = []
        entries: list[str] = []
        others: list[str] = []
        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                header.append(s)
            elif s.startswith(("- ", "* ")):
                entries.append(s)
            else:
                others.append(s)
        return header, entries, others

    local_header, local_entries, local_others = parse(local_text)
    peer_header, peer_entries, peer_others = parse(peer_text)
    header = local_header or peer_header
    seen: set[str] = set()
    entries: list[str] = []
    for entry in local_entries + peer_entries:
        match = re.search(r"\]\(([^)]+)\)", entry)
        key = match.group(1) if match else re.sub(r"\s+", " ", entry)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    others: list[str] = []
    for item in local_others + peer_others:
        if item not in others:
            others.append(item)
    out = list(header)
    if header and (others or entries):
        out.append("")
    out.extend(others)
    if others and entries:
        out.append("")
    out.extend(entries)
    return "\n".join(out).rstrip() + "\n"


def _remote_rel(category: str, key: str, peer_slug: str = "") -> str:
    if category == "user":
        return f".qoder/memory/{key}"
    slug = peer_slug or category.split("/", 1)[1]
    return f".qoder/projects/{slug}/memory/{key}"


def merge_memory(cfg: Config) -> dict:
    run_dir = CONFLICTS / datetime.now().strftime("%Y%m%d-%H%M%S")
    conflict_entries: list[str] = []
    push_files: dict[str, dict] = {}
    counts = {"copied_in": 0, "local_only": 0, "index_merged": 0, "conflicts": 0}

    categories: list[tuple[str, Path, Path, str]] = [
        ("user", HOME / ".qoder/memory", cfg.peer_mirror / ".qoder/memory", ""),
    ]
    local_projects = HOME / ".qoder/projects"
    peer_projects = cfg.peer_mirror / ".qoder/projects"
    local_slugs: set[str] = set()
    peer_slugs: set[str] = set()
    for base, bucket in ((local_projects, local_slugs), (peer_projects, peer_slugs)):
        if base.is_dir():
            for item in base.iterdir():
                if (item / "memory").is_dir():
                    bucket.add(item.name)
    # 两端项目全量同步；别名把同一仓库的不同 checkout 路径合并。
    reverse_map = {peer: local for local, peer in cfg.project_map.items()}
    pairs: dict[str, str] = {}
    for slug in local_slugs:
        pairs[slug] = cfg.project_map.get(slug, slug)
    for peer_slug in peer_slugs:
        pairs.setdefault(reverse_map.get(peer_slug, peer_slug), peer_slug)
    for slug in sorted(pairs):
        peer_slug = pairs[slug]
        categories.append((
            f"projects/{slug}",
            local_projects / slug / "memory",
            peer_projects / peer_slug / "memory",
            peer_slug,
        ))

    def save_conflict(category: str, key: str, src: Path, kept: str, other: str) -> Path:
        nonlocal conflict_entries
        dst = run_dir / category / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        conflict_entries.append(
            f"- `{category}/{key}`：保留 **{kept}** 版本，**{other}** 版本见 `{dst}`"
        )
        return dst

    for category, live_dir, peer_dir, peer_slug in categories:
        if not live_dir.is_dir() and not peer_dir.is_dir():
            continue
        live_dir.mkdir(parents=True, exist_ok=True)

        def file_map(base: Path) -> dict[str, Path]:
            if not base.is_dir():
                return {}
            return {
                str(path.relative_to(base)): path
                for path in base.rglob("*.md")
                if path.is_file()
            }

        live_map = file_map(live_dir)
        peer_map = file_map(peer_dir)

        for key in sorted(set(live_map) | set(peer_map)):
            live = live_map.get(key)
            peer = peer_map.get(key)
            remote_rel = _remote_rel(category, key, peer_slug)

            if live is None and peer is not None:
                dst = live_dir / key
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(peer, dst)
                counts["copied_in"] += 1
                continue
            if live is not None and peer is None:
                push_files[remote_rel] = {"rel": remote_rel, "local": str(live), "category": category, "key": key}
                counts["local_only"] += 1
                continue
            if live is None or peer is None:
                continue

            local_bytes = live.read_bytes()
            peer_bytes = peer.read_bytes()
            if local_bytes == peer_bytes:
                continue

            if Path(key).name == "MEMORY.md":
                local_text = local_bytes.decode("utf-8", "replace")
                peer_text = peer_bytes.decode("utf-8", "replace")
                merged = union_memory_index(local_text, peer_text)
                local_changed = merged != local_text
                peer_changed = merged != peer_text
                if local_changed:
                    live.write_text(merged, encoding="utf-8")
                if peer_changed:
                    push_files[remote_rel] = {"rel": remote_rel, "local": str(live), "category": category, "key": key}
                if local_changed or peer_changed:
                    counts["index_merged"] += 1
                continue

            local_mtime = live.stat().st_mtime
            peer_mtime = peer.stat().st_mtime
            if local_mtime >= peer_mtime:
                save_conflict(category, key, peer, kept="local", other=cfg.peer_name)
                push_files[remote_rel] = {"rel": remote_rel, "local": str(live), "category": category, "key": key}
                counts["conflicts"] += 1
            else:
                save_conflict(category, key, live, kept=cfg.peer_name, other="local")
                shutil.copy2(peer, live)
                counts["copied_in"] += 1
                counts["conflicts"] += 1

    if conflict_entries:
        run_dir.mkdir(parents=True, exist_ok=True)
        index = [
            f"# 记忆冲突 · {now_short()}",
            "",
            f"对端：{cfg.peer_name}。同名文件内容不同，已保留一边，另一边存档如下。",
            "",
            *conflict_entries,
            "",
        ]
        (run_dir / "index.md").write_text("\n".join(index), encoding="utf-8")
        log(f"发现 {counts['conflicts']} 个冲突，存档：{run_dir}", error=True)

    return {"push_files": list(push_files.values()), "counts": counts, "run_dir": str(run_dir) if conflict_entries else ""}


def push_memory(cfg: Config, push_files: list[dict]) -> dict:
    if not push_files:
        return {"pushed": 0, "skipped": 0, "mismatch": 0}

    fetched: dict[str, str | None] = {}
    for item in push_files:
        peer_copy = cfg.peer_mirror / item["rel"]
        fetched[item["rel"]] = sha256_file(peer_copy) if peer_copy.exists() else None
    remote_now = peer_hashes(cfg)

    safe: list[dict] = []
    skipped = 0
    for item in push_files:
        rel = item["rel"]
        if fetched.get(rel) != remote_now.get(rel):
            log(f"跳过 {rel}：对端在同步窗口内有变化", error=True)
            skipped += 1
            continue
        safe.append(item)

    if not safe:
        return {"pushed": 0, "skipped": skipped, "mismatch": 0}

    if cfg.peer_local_root:
        root = Path(cfg.peer_local_root)
        for item in safe:
            dst = root / item["rel"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(item["local"]), dst)
    else:
        stage = STATE / "push-stage"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        rels: list[str] = []
        for item in safe:
            src = Path(item["local"])
            dst = stage / item["rel"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            rels.append(item["rel"])
        files_list = STATE / "push-files.list"
        files_list.write_bytes("\0".join(rels).encode("utf-8") + b"\0")
        result = rsync_run(
            cfg,
            ["rsync", "-a", "--ignore-times", "--from0",
             "--files-from", str(files_list), f"{stage}/", "{target}:"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode not in (0, 24):
            raise RuntimeError(f"推送记忆失败：{result.stderr.strip()}")

    after = peer_hashes(cfg)
    mismatch = 0
    for item in safe:
        expected = sha256_file(Path(item["local"]))
        if after.get(item["rel"]) != expected:
            mismatch += 1
            log(f"推送校验不一致：{item['rel']}", error=True)

    log(f"已推送记忆：{len(safe)} 个（跳过 {skipped}，校验不一致 {mismatch}）")
    return {"pushed": len(safe), "skipped": skipped, "mismatch": mismatch}


def sync_shared(cfg: Config) -> None:
    cfg.local_ns.mkdir(parents=True, exist_ok=True)
    cfg.peer_ns.mkdir(parents=True, exist_ok=True)

    if cfg.peer_local_root:
        peer_shared = Path(cfg.peer_local_root) / ".agent-sync" / "shared"
        peer_src = peer_shared / cfg.peer_name
        if peer_src.is_dir():
            shutil.rmtree(cfg.peer_ns, ignore_errors=True)
            shutil.copytree(peer_src, cfg.peer_ns, copy_function=shutil.copy2)
        peer_dst = peer_shared / cfg.machine
        shutil.rmtree(peer_dst, ignore_errors=True)
        shutil.copytree(cfg.local_ns, peer_dst, copy_function=shutil.copy2)
        return

    ssh_run(cfg, 'mkdir -p "$HOME/.agent-sync/shared"', check=True)

    pull = rsync_run(
        cfg,
        ["rsync", "-a", "--delete",
         "{target}:.agent-sync/shared/" + cfg.peer_name + "/",
         f"{cfg.peer_ns}/"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if pull.returncode != 0:
        log(f"拉取对端摘要失败：{pull.stderr.strip()}", error=True)

    push = rsync_run(
        cfg,
        ["rsync", "-a", "--delete",
         f"{cfg.local_ns}/",
         "{target}:.agent-sync/shared/" + cfg.machine + "/"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if push.returncode != 0:
        log(f"推送本机摘要失败：{push.stderr.strip()}", error=True)
    else:
        log("已同步 shared/<machine> 与 shared/<peer>")


def _state_file() -> Path:
    return STATE / "digests.json"


def load_digest_state() -> dict:
    path = _state_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_digest_state(state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def write_index(cfg: Config, state: dict) -> None:
    sessions_dir = cfg.local_ns / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for sid, item in state.items():
        entries.append({
            "session_id": sid,
            "title": item.get("title", ""),
            "start": item.get("start", ""),
            "end": item.get("end", ""),
            "cwd": item.get("cwd", ""),
            "file": item.get("digest", ""),
        })
    entries.sort(key=lambda x: (x.get("end", ""), x.get("session_id", "")), reverse=True)
    payload = {
        "machine": cfg.machine,
        "updated": now_iso(),
        "sessions": entries[:200],
    }
    (sessions_dir / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _digest_one(cfg: Config, path: Path, state: dict) -> bool:
    markdown, meta = session_digest.build_with_machine(str(path), cfg.machine)
    sessions_dir = cfg.local_ns / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out = sessions_dir / f"{meta.session_id}.md"
    out.write_text(markdown, encoding="utf-8")
    state[meta.session_id] = {
        "source_mtime": int(path.stat().st_mtime),
        "digest": str(out),
        "title": meta.title,
        "start": meta.start,
        "end": meta.end,
        "cwd": meta.cwd,
    }
    return True


def digest_scan(cfg: Config, min_age_sec: int = 120, include_active: bool = False, limit: int = 0) -> int:
    lock_path = STATE / "digest.lock"
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 0
    try:
        state = load_digest_state()
        projects = HOME / ".qoder/projects"
        files: list[Path] = []
        if projects.is_dir():
            files = [
                p for p in projects.glob("*/*.jsonl")
                if p.parent.name != "transcript" and p.stat().st_size > 0
            ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        count = 0
        for path in files:
            sid = path.stem
            mtime = int(path.stat().st_mtime)
            if not include_active and time.time() - mtime < min_age_sec:
                continue
            previous = state.get(sid)
            if previous and mtime <= int(previous.get("source_mtime", 0)):
                continue
            try:
                _digest_one(cfg, path, state)
                count += 1
            except Exception as exc:
                log(f"摘要失败 {path}: {exc}", error=True)
            if limit and count >= limit:
                break
        save_digest_state(state)
        write_index(cfg, state)
        if count:
            log(f"已生成 {count} 条会话摘要")
        return count
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


def read_hook_payload() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_hook_transcript(payload: dict) -> Path | None:
    for key in ("transcript_path", "transcriptPath", "session_file", "sessionFile", "file_path"):
        value = payload.get(key)
        if isinstance(value, str) and Path(value).exists():
            return Path(value)
    sid = payload.get("session_id") or payload.get("sessionId")
    projects = HOME / ".qoder/projects"
    if sid:
        hits = list(projects.glob(f"*/{sid}.jsonl"))
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        project = projects / cwd.replace("/", "-")
        if project.is_dir():
            hits = list(project.glob("*.jsonl"))
            if hits:
                return max(hits, key=lambda p: p.stat().st_mtime)
    recent = [
        p for p in projects.glob("*/*.jsonl")
        if p.parent.name != "transcript" and time.time() - p.stat().st_mtime < 600
    ]
    return max(recent, key=lambda p: p.stat().st_mtime) if recent else None


def spawn_background(*argv: str) -> None:
    script = str(Path(__file__).resolve())
    env = os.environ.copy()
    try:
        subprocess.Popen(
            [sys.executable, script, *argv, "--quiet"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    except Exception as exc:
        log(f"后台任务启动失败：{exc}", error=True)


def _strip_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n"), parts[1]
    return text, ""


def _frontmatter_field(frontmatter: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", frontmatter, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in body.splitlines():
        if raw.startswith("## "):
            current = raw[3:].strip()
            sections.setdefault(current, [])
        elif current is not None and raw.strip():
            sections[current].append(raw.rstrip())
    return sections


def print_brief(cfg: Config, syncing: bool = False) -> None:
    lines = [f"[跨机协同] 对端 {cfg.peer_name}" + (" · 后台同步中，以下为上次结果" if syncing else "")]
    handoff = cfg.peer_ns / "handoff.md"
    if handoff.exists():
        text = read_text(handoff)
        body, frontmatter = _strip_frontmatter(text)
        updated = _frontmatter_field(frontmatter, "updated")
        if not updated:
            updated = datetime.fromtimestamp(handoff.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"交接更新：{updated}")
        try:
            updated_dt = datetime.fromisoformat(updated)
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.astimezone()
            if datetime.now().astimezone() - updated_dt > timedelta(hours=24):
                lines.append("⚠️ 交接已超过 24 小时未更新")
        except Exception:
            pass

        sections = _sections(body)
        printed = False
        for name in ("当前焦点", "下一步", "进行中 / 下一步", "阻塞 / 待拍板", "阻塞"):
            items = [x for x in sections.get(name, []) if "待填写" not in x]
            if not items:
                continue
            lines.append(f"## {name}")
            lines.extend(items[:4])
            printed = True
            if len(lines) > 24:
                break
        if not printed:
            if "（待填写）" in body:
                lines.append(f"对端交接尚未填写（{cfg.peer_name}）")
            else:
                fallback = [x for x in body.splitlines() if x.strip()][:8]
                lines.extend(fallback)
    else:
        lines.append(f"对端暂无交接数据（{handoff}）")

    index = cfg.peer_ns / "sessions" / "index.json"
    if index.exists():
        try:
            data = json.loads(read_text(index))
            recent = data.get("sessions", [])[:2]
            if recent:
                lines.append("最近会话：")
                for item in recent:
                    title = (item.get("title") or "")[:60]
                    lines.append(f"- {item.get('end', '')} {title}")
        except Exception:
            pass

    conflict_count = 0
    cutoff = time.time() - 7 * 86400
    if CONFLICTS.is_dir():
        conflict_count = sum(
            1 for p in CONFLICTS.rglob("*.md")
            if p.name != "index.md" and p.stat().st_mtime >= cutoff
        )
    if conflict_count:
        lines.append(f"待处理冲突：{conflict_count} 个（{CONFLICTS}）")
    lines.append(f"完整交接：{handoff}")
    print("\n".join(lines), flush=True)


def cmd_init(cfg: Config, force_handoff: bool = False) -> None:
    ensure_dirs(cfg)
    handoff = cfg.local_ns / "handoff.md"
    if force_handoff or not handoff.exists():
        handoff.write_text(
            HANDOFF_TEMPLATE.format(machine=cfg.machine, updated=now_iso()),
            encoding="utf-8",
        )
    log(f"初始化完成：{AS_HOME}")


def cmd_sync(cfg: Config) -> int:
    ensure_dirs(cfg)
    if not cfg.is_orchestrator:
        digest_scan(cfg)
        log(f"hub 模式：仅扫描本机会话；等待 {cfg.peer_name} 发起同步")
        return 0

    lock_path = STATE / "sync.lock"
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("已有同步在运行，跳过")
            return 0

        try:
            digest_scan(cfg)
            fetch_peer_memory(cfg)
            merged = merge_memory(cfg)
            pushed = push_memory(cfg, merged["push_files"])
            sync_shared(cfg)
            counts = merged["counts"]
            summary = (
                f"拉取 {counts['copied_in']}，推送 {pushed['pushed']}，"
                f"索引合并 {counts['index_merged']}，冲突 {counts['conflicts']}，"
                f"跳过 {pushed['skipped']}"
            )
            (STATE / "last-sync.txt").write_text(f"{now_iso()}\n{summary}\n", encoding="utf-8")
            log(f"同步完成：{summary}")
            return 0
        except Exception as exc:
            log(f"同步失败：{exc}", error=True)
            (STATE / "last-sync.txt").write_text(f"{now_iso()}\n失败：{exc}\n", encoding="utf-8")
            return 1


def cmd_session_start(cfg: Config) -> int:
    ensure_dirs(cfg)
    spawn_background("digest-scan", "--min-age-sec", "120")
    if cfg.is_orchestrator:
        spawn_background("sync")
        print_brief(cfg, syncing=True)
    else:
        print_brief(cfg, syncing=False)
    return 0


def cmd_session_end(cfg: Config) -> int:
    ensure_dirs(cfg)
    payload = read_hook_payload()
    path = resolve_hook_transcript(payload)
    if path is not None:
        try:
            state = load_digest_state()
            _digest_one(cfg, path, state)
            save_digest_state(state)
            write_index(cfg, state)
            if not QUIET:
                log(f"已生成会话摘要：{path.stem}")
        except Exception as exc:
            log(f"会话摘要失败：{exc}", error=True)
    else:
        log("SessionEnd 未找到对应会话文件，跳过摘要", error=True)

    if cfg.is_orchestrator:
        spawn_background("sync")
    return 0


def cmd_status(cfg: Config) -> int:
    print(f"agentsync {VERSION}")
    print(f"machine : {cfg.machine}")
    print(f"role    : {cfg.role}")
    print(f"peer    : {cfg.peer_name} ({cfg.peer_ssh or '未配置'})")
    if cfg.peer_ssh_fallbacks:
        print(f"fallback: {', '.join(cfg.peer_ssh_fallbacks)}")
    if cfg.peer_link_if:
        state = "up" if link_is_up(cfg.peer_link_if) else "down"
        print(f"link    : {cfg.peer_link_if} {state}")
    try:
        active = PEER_ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        active = ""
    if active:
        print(f"active  : {active}")
    print(f"home    : {AS_HOME}")
    last = STATE / "last-sync.txt"
    if last.exists():
        print("last    : " + " | ".join(x.strip() for x in read_text(last).splitlines() if x.strip()))
    else:
        print("last    : 尚未同步")
    local_index = cfg.local_ns / "sessions" / "index.json"
    if local_index.exists():
        try:
            print(f"digests : {len(json.loads(read_text(local_index)).get('sessions', []))}")
        except Exception:
            pass
    conflict_count = sum(1 for p in CONFLICTS.rglob("*.md") if p.name != "index.md") if CONFLICTS.is_dir() else 0
    print(f"conflicts: {conflict_count}")
    if cfg.is_orchestrator:
        if cfg.peer_local_root:
            print("peer io : local test root " + cfg.peer_local_root)
        else:
            try:
                result = ssh_run(cfg, "echo ok", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
                print("ssh     : " + ("ok" if result.returncode == 0 else "失败"))
            except Exception as exc:
                print(f"ssh     : 失败（{exc}）")
    else:
        print("ssh     : hub 模式不需要反向连接")
    return 0


def cmd_conflicts(cfg: Config) -> int:
    dirs = sorted([p for p in CONFLICTS.iterdir() if p.is_dir()], reverse=True) if CONFLICTS.is_dir() else []
    if not dirs:
        print("没有冲突记录")
        return 0
    latest = dirs[0]
    index = latest / "index.md"
    print(read_text(index) if index.exists() else f"冲突目录：{latest}")
    return 0


def install_hooks(uninstall: bool = False) -> int:
    settings_path = HOME / ".qoder" / "settings.json"
    if not settings_path.exists():
        raise SystemExit(f"未找到 Qoder 配置：{settings_path}")
    try:
        data = json.loads(read_text(settings_path))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"settings.json 不是合法 JSON：{exc}")
    if not isinstance(data, dict):
        raise SystemExit("settings.json 顶层不是对象")

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("settings.json 的 hooks 不是对象")

    start_cmd = f"bash {AS_HOME}/bin/hook_session_start.sh"
    end_cmd = f"bash {AS_HOME}/bin/hook_session_end.sh"

    def group_commands(group) -> list[str]:
        out = []
        if isinstance(group, dict):
            for hook in group.get("hooks", []) or []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    out.append(hook["command"])
        return out

    if uninstall:
        removed = 0
        for event in ("SessionStart", "SessionEnd"):
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                commands = group_commands(group)
                if any("hook_session_start.sh" in c or "hook_session_end.sh" in c for c in commands):
                    removed += 1
                    continue
                kept.append(group)
            hooks[event] = kept
    else:
        backup = settings_path.with_name(
            f"settings.json.agentsync-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
        )
        shutil.copy2(settings_path, backup)

        def already(event: str, needle: str) -> bool:
            groups = hooks.get(event) or []
            return any(needle in c for group in groups for c in group_commands(group))

        added = []
        if not already("SessionStart", "hook_session_start.sh"):
            hooks.setdefault("SessionStart", []).append({
                "matcher": "startup|resume|compact",
                "hooks": [{"type": "command", "command": start_cmd, "timeout": 8}],
            })
            added.append("SessionStart")
        if not already("SessionEnd", "hook_session_end.sh"):
            hooks.setdefault("SessionEnd", []).append({
                "matcher": "*",
                "hooks": [{"type": "command", "command": end_cmd, "timeout": 12}],
            })
            added.append("SessionEnd")
        if not added:
            print("钩子已存在，无需重复安装")
            return 0

    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    if uninstall:
        print(f"已移除 {removed} 组跨机协同钩子")
    else:
        print(f"已安装钩子：{', '.join(added)}（备份 {backup}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--quiet", action="store_true", help="只输出错误")

    parser = argparse.ArgumentParser(description="跨机协同同步工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", parents=[common], help="发起一次同步")
    p.set_defaults(func=lambda cfg, args: cmd_sync(cfg))

    p = sub.add_parser("digest-scan", parents=[common], help="扫描本机会话并补摘要")
    p.add_argument("--min-age-sec", type=int, default=120)
    p.add_argument("--include-active", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=lambda cfg, args: (digest_scan(cfg, args.min_age_sec, args.include_active, args.limit) and 0) or 0)

    p = sub.add_parser("session-start", parents=[common], help="SessionStart 钩子入口")
    p.set_defaults(func=lambda cfg, args: cmd_session_start(cfg))

    p = sub.add_parser("session-end", parents=[common], help="SessionEnd 钩子入口")
    p.set_defaults(func=lambda cfg, args: cmd_session_end(cfg))

    p = sub.add_parser("brief", parents=[common], help="打印对端简报")
    p.set_defaults(func=lambda cfg, args: (print_brief(cfg), 0)[1])

    p = sub.add_parser("status", parents=[common], help="查看状态")
    p.set_defaults(func=lambda cfg, args: cmd_status(cfg))

    p = sub.add_parser("conflicts", parents=[common], help="查看最近冲突")
    p.set_defaults(func=lambda cfg, args: cmd_conflicts(cfg))

    p = sub.add_parser("install-hooks", parents=[common], help="安装/移除 Qoder 钩子")
    p.add_argument("--uninstall", action="store_true")
    p.set_defaults(func=lambda cfg, args: install_hooks(args.uninstall))

    p = sub.add_parser("init", parents=[common], help="创建目录与交接模板")
    p.add_argument("--force-handoff", action="store_true")
    p.set_defaults(func=lambda cfg, args: (cmd_init(cfg, args.force_handoff), 0)[1])

    return parser


def main() -> int:
    global QUIET
    parser = build_parser()
    args = parser.parse_args()
    QUIET = bool(getattr(args, "quiet", False))
    cfg = load_config(require=args.command not in ("init", "install-hooks"))
    try:
        return int(args.func(cfg, args) or 0)
    except SystemExit:
        raise
    except Exception as exc:
        log(f"{args.command} 失败：{exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
