#!/usr/bin/env python3
"""Event-driven deploy watcher: replaces blind sleep polling.

Signals per sample:
  1. apre deploy status + pipeline run identity (stage ids change per run)
  2. machine set / RUNNING count
  3. HTTP liveness of the environment domain

Exit codes: 0 success, 2 definite failure, 3 timeout, 4 cannot observe.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DATA = Path(os.path.expanduser("~/.qoder/task-metrics/data/deploy"))
A1 = os.environ.get("A1_BIN", "a1")


def hhmmss():
    return datetime.now().strftime("%H:%M:%S")


def run_a1(args, timeout=45):
    cmd = [A1, "--no-update-check"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "a1 timeout"
    if p.returncode != 0:
        return None, (p.stderr or p.stdout).strip()[:300]
    try:
        return json.loads(p.stdout), None
    except Exception as exc:
        return None, "json parse: %s" % exc


def run_identity(apre):
    stages = []
    for st in ((apre.get("pipelineInst") or {}).get("pipelineInstVO") or {}).get("stageInstList", []):
        stages.append(str(st.get("id")))
    return ",".join(stages)


def health(url, timeout=5):
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "deploy-watch/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def snapshot(env_id, apre_id):
    lst, err = run_a1(["env", "project", "apre", "list", str(env_id), "--format", "json"])
    if err:
        return None, "apre list: " + err
    apre = next((x for x in lst if str(x.get("id")) == str(apre_id)), None)
    if not apre:
        return None, "apre %s not found in env %s" % (apre_id, env_id)

    deploy = ((apre.get("pipelineInst") or {}).get("deployStatus") or {}).get("value", "UNKNOWN")
    url = None
    try:
        ext = json.loads(apre.get("extParam") or "{}")
        domain = ext.get("bindDomainName")
        if domain:
            url = "https://" + domain + "/"
    except Exception:
        pass

    machines, merr = run_a1(["env", "project", "apre", "machines", str(apre_id), "--format", "json"])
    running, sns = 0, []
    if not merr:
        for m in machines or []:
            if ((m.get("status") or {}).get("value")) == "RUNNING":
                running += 1
            if m.get("sn"):
                sns.append(str(m["sn"]))

    return {
        "deploy": deploy,
        "run": run_identity(apre),
        "machines_running": running,
        "machines_sn": sorted(sns),
        "url": url,
        "error": merr if merr else None,
    }, None


def write_state(path, state):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def log_line(log_path, line):
    DATA.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    ap = argparse.ArgumentParser(description="Watch an apre deployment until success or definite failure.")
    ap.add_argument("--env", required=True, help="group env id")
    ap.add_argument("--apre", required=True, help="apre id")
    ap.add_argument("--trigger", action="store_true", help="run 'apre deploy' before watching")
    ap.add_argument("--attach", action="store_true", help="watch current run without requiring a new one")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--start-grace", type=int, default=180, help="wait this long for a new pipeline run")
    ap.add_argument("--bad-health-grace", type=int, default=180, help="fail if HTTP >=500 persists this long")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    state_path = DATA / ("%s.json" % args.apre)
    log_path = DATA / ("%s.log" % args.apre)
    started = time.time()

    if args.trigger:
        out, err = run_a1(["env", "project", "apre", "deploy", str(args.apre), "--format", "json"], timeout=120)
        if err:
            print("[%s] TRIGGER FAILED %s" % (hhmmss(), err), file=sys.stderr)
            return 2
        log_line(log_path, "[%s] triggered deploy: %s" % (hhmmss(), json.dumps(out, ensure_ascii=False)[:200]))

    first, err = snapshot(args.env, args.apre)
    if err:
        print("[%s] CANNOT OBSERVE %s" % (hhmmss(), err), file=sys.stderr)
        return 4
    if args.once:
        first["health"] = health(first["url"])
        print(json.dumps(first, ensure_ascii=False, indent=2))
        return 0 if first["health"] and first["health"] < 500 else 2

    base_run = first["run"]
    new_run_seen = bool(args.attach)
    deploy_errors = 0
    zero_machines = 0
    bad_since = None
    last_line = None
    state = {
        "apre": args.apre,
        "env": args.env,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "samples": 0,
        "new_run_seen": new_run_seen,
        "transitions": [],
        "url": first["url"],
    }

    while True:
        snap, err = snapshot(args.env, args.apre)
        if err:
            deploy_errors += 1
            if deploy_errors >= 3:
                state.update(exit=4, reason=err, updated_at=datetime.now().isoformat(timespec="seconds"))
                write_state(state_path, state)
                print("[%s] CANNOT OBSERVE %s" % (hhmmss(), err), file=sys.stderr)
                return 4
            time.sleep(args.interval)
            continue
        deploy_errors = 0
        code = health(snap["url"])
        state["samples"] += 1

        if not new_run_seen and snap["run"] != base_run:
            new_run_seen = True
            state["new_run_seen"] = True
            log_line(log_path, "[%s] new pipeline run observed" % hhmmss())

        if snap["deploy"] == "FAILED":
            state.update(exit=2, reason="deploy FAILED", last=snap, updated_at=datetime.now().isoformat(timespec="seconds"))
            write_state(state_path, state)
            log_line(log_path, "[%s] FAILED deploy status" % hhmmss())
            print("[%s] FAILED deploy status" % hhmmss(), file=sys.stderr)
            return 2

        if new_run_seen:
            zero_machines = zero_machines + 1 if snap["machines_running"] == 0 else 0
            if zero_machines >= 3:
                reason = "no RUNNING machines for 3 samples"
                state.update(exit=2, reason=reason, last=snap, updated_at=datetime.now().isoformat(timespec="seconds"))
                write_state(state_path, state)
                log_line(log_path, "[%s] FAILED %s" % (hhmmss(), reason))
                print("[%s] FAILED %s" % (hhmmss(), reason), file=sys.stderr)
                return 2

        if new_run_seen and snap["deploy"] == "SUCCESS":
            if code is not None and code < 500:
                state.update(exit=0, reason="deploy SUCCESS + health %s" % code, last=snap,
                             updated_at=datetime.now().isoformat(timespec="seconds"))
                write_state(state_path, state)
                log_line(log_path, "[%s] SUCCESS health=%s machines=%s" % (hhmmss(), code, snap["machines_running"]))
                print("[%s] SUCCESS health=%s machines=%s" % (hhmmss(), code, snap["machines_running"]))
                return 0
            bad_since = bad_since or time.time()
            if time.time() - bad_since >= args.bad_health_grace:
                reason = "deploy SUCCESS but HTTP unhealthy (%s) for %ss" % (code, args.bad_health_grace)
                state.update(exit=2, reason=reason, last=snap, updated_at=datetime.now().isoformat(timespec="seconds"))
                write_state(state_path, state)
                log_line(log_path, "[%s] FAILED %s" % (hhmmss(), reason))
                print("[%s] FAILED %s" % (hhmmss(), reason), file=sys.stderr)
                return 2
        else:
            bad_since = None

        line = "[%s] %s deploy=%s machines=%s health=%s run=%s" % (
            hhmmss(), "WATCH" if new_run_seen else "WAIT-RUN",
            snap["deploy"], snap["machines_running"], code, snap["run"][:24])
        if line[10:] != (last_line or "")[10:]:
            last_line = line
            log_line(log_path, line)
            print(line)
            state["transitions"].append(line)

        state.update(new_run_seen=new_run_seen, last=snap, last_health=code,
                     updated_at=datetime.now().isoformat(timespec="seconds"))
        write_state(state_path, state)

        if not new_run_seen and time.time() - started >= args.start_grace:
            reason = "no new pipeline run within %ss (deploy not triggered?)" % args.start_grace
            state.update(exit=2, reason=reason, updated_at=datetime.now().isoformat(timespec="seconds"))
            write_state(state_path, state)
            print("[%s] %s" % (hhmmss(), reason), file=sys.stderr)
            return 2
        if time.time() - started >= args.timeout:
            reason = "timeout after %ss" % args.timeout
            state.update(exit=3, reason=reason, updated_at=datetime.now().isoformat(timespec="seconds"))
            write_state(state_path, state)
            print("[%s] %s" % (hhmmss(), reason), file=sys.stderr)
            return 3

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
