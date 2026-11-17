"""User-editable injection templates.

The five AI-facing injection points (protocol, first-turn candidates, binding
hint, end hint, nudge voice) can be overridden by files under
``$KIT_TEMPLATES_DIR`` (default ``~/.agentcrop/templates``).  This module is
the only loader/renderer: core and the DSH host both go through it so the
behaviour cannot drift between hosts.

Contract (see docs/DESIGN-CUSTOM-TEMPLATES.md):

- missing file        -> ``None`` (caller uses the built-in default)
- empty file          -> ``None`` (same as missing)
- body ``none``       -> ``""`` (the injection is switched off)
- ``{{name}}``        -> whitelisted variable value; unknown -> ``""`` + warning
- more than ``MAX_CHARS`` rendered -> truncated + warning
- files are cached by ``(path, mtime_ns, size)`` so edits apply on next read
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MAX_CHARS = 4000

VOICE_TIERS = ("warm", "yellow", "red", "overdue", "miss", "miss_streak")

# name -> relative file, accepts both "protocol" and "voice:yellow"
_FILES = {
    "protocol": "protocol.md",
    "start-hint": "start-hint.md",
    "binding-hint": "binding-hint.md",
    "end-hint": "end-hint.md",
}
for _tier in VOICE_TIERS:
    _FILES[f"voice:{_tier}"] = f"voice/{_tier}.md"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_VOICE_SEPARATOR = re.compile(r"(?m)^---\s*$")

_cache: dict[tuple[str, int, int], str | None] = {}


def templates_dir() -> Path:
    """Directory holding user templates; absent means "all defaults"."""
    explicit = os.environ.get("KIT_TEMPLATES_DIR")
    if explicit:
        return Path(explicit).expanduser()
    shared = os.environ.get("KIT_SHARED_HOME")
    base = Path(shared).expanduser() if shared else Path.home() / ".agentcrop"
    return base / "templates"


def _warn(message: str) -> None:
    # stdout belongs to the CLI JSON answer; warnings go to stderr.
    print(f"[crop] template warning: {message}", file=sys.stderr)


def _path_for(name: str) -> Path | None:
    relative = _FILES.get(name)
    return templates_dir() / relative if relative else None


def clear_cache() -> None:
    """Tests and the "generate defaults" action call this after writing files."""
    _cache.clear()


def load(name: str) -> str | None:
    """Raw template body: None = no override, "" = switched off."""
    path = _path_for(name)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _cache:
        return _cache[key]

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _warn(f"cannot read {path}: {error}")
        _cache[key] = None
        return None

    body = raw.strip()
    if not body:
        result = None
    elif body.lower() == "none":
        result = ""
    else:
        result = body
    _cache[key] = result
    return result


def render(name: str, variables: dict[str, str]) -> str | None:
    """Render one template; None keeps the caller's built-in default."""
    body = load(name)
    if body is None or body == "":
        return body

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            _warn(f"{name}: unknown variable {{{{{key}}}}} replaced with empty text")
            return ""
        return str(variables[key])

    rendered = _PLACEHOLDER.sub(substitute, body)
    if len(rendered) > MAX_CHARS:
        _warn(f"{name}: truncated to {MAX_CHARS} chars")
        rendered = rendered[:MAX_CHARS]
    return rendered


def voice_lines(tier: str) -> list[str] | None:
    """Candidate lines for one nudge tier, split on ``---`` lines."""
    body = load(f"voice:{tier}")
    if body is None or body == "":
        return None
    lines = [segment.strip() for segment in _VOICE_SEPARATOR.split(body) if segment.strip()]
    return lines or None
