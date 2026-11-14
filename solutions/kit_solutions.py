"""方案开关。默认全部关闭；启用列表落在数据目录，不进插件缓存。

启用文件：$DATA_DIR/solutions.json -> {"enabled": ["inline-reminders", ...]}
"""
import json
import os

DATA_DIR = os.path.expanduser(os.environ.get("KIT_DATA_DIR") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics", "data"))
CONFIG = os.path.join(DATA_DIR, "solutions.json")

STATUS_ORDER = ("未验证", "试用中", "已验证", "已停用", "不适用")


def load():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"enabled": []}
    if not isinstance(d, dict):
        return {"enabled": []}
    d.setdefault("enabled", [])
    return d


def enabled():
    return set(load().get("enabled") or [])


def is_on(solution_id):
    return solution_id in enabled()


def save(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def enable(solution_id):
    d = load()
    ids = set(d.get("enabled") or [])
    ids.add(solution_id)
    d["enabled"] = sorted(ids)
    save(d)


def disable(solution_id):
    d = load()
    d["enabled"] = sorted(set(d.get("enabled") or []) - {solution_id})
    save(d)
