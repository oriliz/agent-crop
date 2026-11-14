#!/usr/bin/env python3
"""Scan Aone requirements and cluster similar titles for duplicate checks."""
import argparse
import difflib
import itertools
import json
import re
import subprocess
import sys


def run_a1(project, keyword):
    cmd = [
        "a1", "project", "workitem", "list", "--project", project,
        "--category", "req", "--sort", "gmtCreate:desc",
        "--page-size", "300", "-f", "json",
    ]
    if keyword:
        cmd += ["--filter", f'subject~"{keyword}"']
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        sys.exit(f"a1 failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"a1 returned non-JSON: {proc.stdout[:300]}")


def norm(title):
    t = re.sub(r"[（(].*?[)）]", "", title)
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--keyword", default="")
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    items = run_a1(args.project, args.keyword)
    pairs = []
    for a, b in itertools.combinations(items, 2):
        ratio = difflib.SequenceMatcher(None, norm(a["subject"]), norm(b["subject"])).ratio()
        if ratio >= args.threshold:
            pairs.append({
                "ratio": round(ratio, 2),
                "a": {k: a.get(k) for k in ("identifier", "subject", "status", "assignedTo", "gmtModified")},
                "b": {k: b.get(k) for k in ("identifier", "subject", "status", "assignedTo", "gmtModified")},
            })
    pairs.sort(key=lambda p: p["ratio"], reverse=True)

    if args.json:
        print(json.dumps(pairs, ensure_ascii=False, indent=1))
        return

    print(f"scanned={len(items)} pairs>={args.threshold}: {len(pairs)}")
    for p in pairs:
        a, b = p["a"], p["b"]
        print(f"{p['ratio']:.2f} | {a['identifier']} [{a['status']}] {a['assignedTo']} {a['subject'][:40]}")
        print(f"     | {b['identifier']} [{b['status']}] {b['assignedTo']} {b['subject'][:40]}")
    if not pairs:
        print("no similar titles found")


if __name__ == "__main__":
    main()
