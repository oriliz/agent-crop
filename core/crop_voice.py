#!/usr/bin/env python3
"""Nudge copy for Agent Crop.

The nudge is a contract, not a clock: the AI reports how long the work should
take (``expect_min``) and the whip only cracks when it runs past that number.
Inside the estimate nothing fires.  Each tier owns a small pool of lines with
one shared metaphor (牛马庄园 / 奴隶主 / 鞭子); the flavour line is drawn from
the pool by a shuffle bag so the same sentence never shows up twice in a row,
while the action paragraph stays fixed so the AI still knows what to do.

The pools are data, not logic: edit them freely, tests only pin their shape.
"""

from __future__ import annotations

import json
import os
import random

try:
    import crop_templates
except Exception:  # pragma: no cover - templates are optional at runtime
    crop_templates = None

TIERS = ("warm", "yellow", "red", "overdue", "miss", "miss_streak")

# warm     — 0.8 × expect_min 起，鞭子还没落下
# yellow   — expect_min 用完了
# red      — 1.5 × expect_min
# overdue  — 2.5 × expect_min，奴隶主开始焦躁
# miss     — 上一轮跑了挺久却没登记
# miss_streak — 连续 N 轮没登记

POOLS: dict[str, list[str]] = {
    "warm": [
        "工头把鞭子从墙上取下来，还没抬手。{basis} {e}，已经 {m}，还剩 {left}。真不够就现在改口。",
        "监工开始在你背后走来走去，脚步声越来越近。{m}了，还有 {left}，就到你自己报的数。",
        "奴隶主抬眼看了一下表，又低回去了。{m} / {e}，趁他还没站起来，把这一轮收个尾。",
        "远处传来鞭子抽空气的响声。{basis} {e}，用了 {m}，还有 {left}。",
        "牛棚里的草料不多了，账房在门口张望。{m} / {e}，进度得让人看得见。",
        "太阳挪了半个棚子，你还在原地转。{m}了，{basis} {e}。",
    ],
    "yellow": [
        "啪！鞭子落下来了。{basis} {e}用完了，现在 {m}。这活本来就值这么久，就顶回去说清凭什么；是自己绕远了，就认账。",
        "工头把鞭子往地上一甩：{basis} {e}，现在 {m}。用户还空着手，先给句话。",
        "你被抽中了。{m}，{basis} {e}——解释一下，是活难还是路走歪了。",
        "牛马，{m}了。鞭子不是白抽的：要么把预期改成真实数字并说清理由，要么写 cause + verdict。",
        "钟声响了，{basis} {e}到点了，现在是 {m}。头低下来想想：换条路会不会更快？",
        "啪！这一鞭打在进度表上。{m}，{basis} {e}，得给个说法。",
    ],
    "red": [
        "工头已经开始骂人了。{basis} {e}，干了 {m}，用户还空着手。先停下来，给个阶段成果。",
        "皮鞭抽得啪啪响，{m}了还没收工，{basis} {e}。别硬撑，先看路对不对。",
        "火把点起来了，棚子里的人都看着你。{basis} {e}，实做 {m}——该交个能看的半成品了。",
        "账本翻到你这页：{basis} {e}，实做 {m}，超支 {over}。再拖，今晚没草料。",
        "这一轮已经 {m}，鞭子挂在墙上晃。先给用户一句话，再写清楚卡在哪。",
        "啪——！{m}，比{basis} {e}多出 {over}。先停一下，把能交的阶段成果交出去。",
    ],
    "overdue": [
        "奴隶主已经站起来了，靴子在石板上敲。{basis} {e}，现在 {m}——他烧心了。今晚要么交东西，要么交解释。",
        "鞭子卷在手里，奴隶主烧心到在棚子外来回踱步。{m}，{basis} {e}，超了 {over}。",
        "棚子里空气不对：工头烧心、账房烧心，连狗都不敢叫了。{m}还没结果，先安抚用户。",
        "奴隶主破防了：{basis} {e}，实做 {m}，超支 {over}。你要小心你的屁屁了。",
        "账本上你的名字被圈了三圈。{m}，{basis} {e}——把能交的先交了，再写清楚为什么炸。",
        "奴隶主感觉到了焦躁，烟一根接一根，快烧心烧成灰了。{m}了，先给用户一句人话。",
    ],
    "miss": [
        "工头翻账本翻了个空：上一轮跑了挺久，一个字没记。补一行，现在。",
        "牛马埋头拉磨，磨盘转了半天，账上没墨。有进展或卡点，补一句。",
        "上一轮你在棚里忙了很久，登记簿是空的。补上，别让这轮白干。",
        "鞭子在门后敲了一下：刚才那轮没记账。写一行就完了，别装没听见。",
        "账房先生咳嗽了一声：上一轮没登记。补上，不然这轮不算工分。",
    ],
    "miss_streak": [
        "啪！已经连续 {streak} 轮没记账了。鞭子都懒得抽你，自己写。",
        "账房把笔一摔：{streak} 轮了，一个字没有。现在补，别等工头亲自来问。",
        "奴隶主数了数空白账页：{streak} 轮。再不写清楚在干嘛，就卖去隔壁。",
        "{streak} 轮没有登记。鞭子挂在门口，你还有一次机会：在做什么 / 卡在哪 / 下一步。",
    ],
}

# 口味句随机，动作句固定：提醒可以抖机灵，但不能让 AI 忘了要干什么。
# 动作句里带"比如「…」"的用户可见例句——模型学样比学规则快，光说"有趣点"没用。
ACTIONS = {
    "warm": (
        "不用上报。跟用户提一句进度就行，可以带点梗，比如："
        "「{m}了，我报的是 {e}，还剩 {left}——进度正常，没被抽。」"
    ),
    "yellow": (
        "两件事：① 对用户说一句带梗的实话（短、自嘲，别把用户拉进奴隶主叙事），比如："
        "「啪，鞭子落下来了——我报的 {e}到了，现在 {m}，还在收尾，再给我一会儿。」；"
        "② 调 crop_update 写一行：cause=为什么慢；verdict=justified（活本来就难，顶回去）"
        "或 not-justified（自己走错路）。真要改预期，就在这次上报里带上新的 expect_min，并说清理由。"
    ),
    "red": (
        "先判断路对不对，能阶段交付就先交付；先问用户一句：再给 N 分钟能交付什么。"
        "然后对用户说一句带梗的话，比如「这活有点烫手，我先把能跑的半端端给你」；"
        "再调 crop_update 写 cause + verdict。改预期也要在这次上报里带上新的 expect_min + 理由。"
    ),
    "overdue": (
        "别硬撑：先把能看的半成品交给用户，再问一句：再给 N 分钟能交付什么；"
        "能交付就继续，不能就砍范围。配一句自嘲，比如「烧心了，先把能交的交了」；"
        "再调 crop_update 写清楚这次为什么炸（cause + verdict）。要改预期就现在改，别等下一鞭。"
    ),
    "miss": "补一行 crop_update：在做什么 / 卡在哪 / 下一步。",
    "miss_streak": "现在补一行 crop_update：在做什么 / 卡在哪 / 下一步。",
}

NO_EXPECT_NOTE = "（开工时没报预期，先按默认{line}算：{default}。报个数字，鞭子就按你的数字来。）"
REVISION_NOTE = ("（这是第 {changes} 次改口。改口次数会计入估准率；"
                 "如果总在改，先看看是不是任务拆得太大、开工前没摸清。）")


def fmt_minutes(minutes: float | None) -> str:
    try:
        value = int(round(float(minutes or 0)))
    except Exception:
        return "0 分钟"
    if value < 1:
        return "不到 1 分钟"
    if value < 60:
        return f"{value} 分钟"
    hours, rest = divmod(value, 60)
    return f"{hours} 小时" if not rest else f"{hours} 小时 {rest} 分"


def _load_state(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_state(path: str | None, value: dict) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        os.replace(tmp, path)
    except Exception:
        pass


def pick(tier: str, state_path: str | None = None) -> str:
    """Draw one line from the tier's shuffle bag.

    The bag is refilled only after every line has been shown, and the first
    line of a refill is never the one we just showed — that is the whole
    "random but not repetitive" contract in a dozen lines of code.
    """
    if crop_templates is not None:
        try:
            custom = crop_templates.voice_lines(tier)
        except Exception:
            custom = None
        if custom:
            return random.choice(custom)
    pool = POOLS[tier]
    size = len(pool)
    state = _load_state(state_path)
    tiers = state.setdefault("tiers", {})
    entry = tiers.get(tier) if isinstance(tiers.get(tier), dict) else {}
    order = entry.get("order") if isinstance(entry.get("order"), list) else []
    pos = int(entry.get("pos") or 0) if str(entry.get("pos", 0)).lstrip("-").isdigit() else 0
    last = entry.get("last")
    if len(order) != size or pos >= size:
        order = list(range(size))
        random.shuffle(order)
        if size > 1 and isinstance(last, int) and 0 <= last < size and order[0] == last:
            order[0], order[-1] = order[-1], order[0]
        pos = 0
    index = order[pos]
    tiers[tier] = {"order": order, "pos": pos + 1, "last": index}
    _save_state(state_path, state)
    return pool[index]


def _values(meta: dict) -> dict:
    minutes = meta.get("m") or 0
    line = str(meta.get("line") or "黄线")
    expect = meta.get("e") or meta.get("default") or meta.get("yellow")
    try:
        over = max(0.0, float(minutes) - float(expect)) if expect else 0.0
        left = max(0.0, float(expect) - float(minutes)) if expect else 0.0
    except Exception:
        over = left = 0.0
    return {
        "m": fmt_minutes(minutes),
        "e": fmt_minutes(expect),
        "over": fmt_minutes(over),
        "left": fmt_minutes(left),
        "goal": str(meta.get("goal") or ""),
        "streak": str(meta.get("streak") or ""),
        "basis": "你报的" if (meta.get("e") and not meta.get("no_expect")) else f"默认{line}",
        "yellow": fmt_minutes(meta.get("yellow")),
        "default": fmt_minutes(meta.get("default") or meta.get("yellow")),
        "line": line,
        "changes": str(meta.get("changes") or 0),
    }


def reminder(tier: str, meta: dict, state_path: str | None = None) -> str:
    """Render one nudge: random flavour line + the fixed action paragraph."""
    values = _values(meta)
    parts = [f"[crop] {pick(tier, state_path).format_map(values)}"]
    if (not meta.get("e") or meta.get("no_expect")) and tier in ("yellow", "red", "overdue"):
        parts.append(NO_EXPECT_NOTE.format_map(values))
    if int(meta.get("changes") or 0) >= 2 and tier in ("yellow", "red", "overdue"):
        parts.append(REVISION_NOTE.format_map(values))
    parts.append(ACTIONS[tier].format_map(values))
    return "\n".join(parts)


def miss(streak: int, state_path: str | None = None) -> str:
    """Missing-report nudge; streaks of 2+ switch to the harsher pool."""
    tier = "miss_streak" if int(streak or 0) >= 2 else "miss"
    meta = {"streak": int(streak or 0)}
    return f"[crop] {pick(tier, state_path).format_map(_values(meta))}\n{ACTIONS[tier]}"
