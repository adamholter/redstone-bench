#!/usr/bin/env python3
"""Supervisor for Muse runs on a small subscription: run the batch whenever `muse usage` shows room, sleep until the
window or weekly reset when it does not, and stop when every config has all 16 tasks.

    RSB_CONFIGS=wonder-high python3 bench/muse_loop.py          (under launchd: com.adam.redstone-bench-batch-muse)

One worker by default: a run that hits the quota mid-way is thrown out and redone, so fewer runs in flight waste less.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIGS = os.environ.get("RSB_CONFIGS", "wonder-high").split(",")
BATCH = os.environ.get("RSB_BATCH_DIR", "_batch_muse")
B = ROOT / "runs" / BATCH
TASKS = sorted(p.stem for p in (ROOT / "tasks").glob("*.json"))
MUSE = pathlib.Path.home() / ".local/bin/muse"


def log(msg):
    B.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%S ") + msg
    print(line, flush=True)
    with open(B / "supervisor.log", "a") as f:
        f.write(line + "\n")


def missing():
    return [(c, t) for c in CONFIGS for t in TASKS if not (ROOT / "runs" / c / t / "result.json").exists()]


def secs(text):
    """'3d 5h', '3h 57m', '12m' -> seconds."""
    n = 0
    for v, u in re.findall(r"(\d+)\s*([dhms])", text):
        n += int(v) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
    return n


def usage():
    """(window %, weekly %, seconds until the binding reset) from `muse usage`; None if it cannot be read."""
    with tempfile.TemporaryDirectory() as d:
        try:
            out = subprocess.run([str(MUSE), "usage"], capture_output=True, text=True, timeout=60, cwd=d).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
    w = re.search(r"Window:\s*(\d+)% used, resets in ([^\n]+)", out)
    k = re.search(r"Weekly:\s*(\d+)% used, resets in ([^\n]+)", out)
    if not (w and k):
        return None
    wp, kp = int(w.group(1)), int(k.group(1))
    wait = secs(k.group(2)) if kp >= 98 else secs(w.group(2)) if wp >= 98 else 0
    return wp, kp, wait


def main():
    while True:
        todo = missing()
        status = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "configs": CONFIGS, "missing": len(todo),
                  "done": len(CONFIGS) * len(TASKS) - len(todo)}
        if not todo:
            (B / "done").write_text(status["time"])
            log(f"all {status['done']} runs done")
            (B / "status.json").write_text(json.dumps(status | {"state": "done"}, indent=1))
            return 0
        u = usage()
        if u is None:
            log("could not read muse usage; retry in 10 min")
            (B / "status.json").write_text(json.dumps(status | {"state": "usage unreadable"}, indent=1))
            time.sleep(600)
            continue
        wp, kp, wait = u
        if wait:
            nap = min(wait + 300, 3600)          # recheck at least hourly
            log(f"quota full (window {wp}%, weekly {kp}%), reset in {wait // 60} min; sleeping {nap // 60} min")
            (B / "status.json").write_text(json.dumps(status | {"state": "waiting for quota", "window": wp, "weekly": kp,
                                                                 "reset_in_min": wait // 60}, indent=1))
            time.sleep(nap)
            continue
        log(f"quota open (window {wp}%, weekly {kp}%): batch for {len(todo)} runs")
        (B / "status.json").write_text(json.dumps(status | {"state": "running", "window": wp, "weekly": kp}, indent=1))
        shutil.rmtree(B / "gave_up", ignore_errors=True)
        env = dict(os.environ, RSB_CONFIGS=",".join(CONFIGS), RSB_BATCH_DIR=BATCH, RSB_WORKERS=os.environ.get("RSB_WORKERS", "1"),
                   RSB_RETRIES="1")          # a quota error ends the pass; this loop waits for the reset instead
        with open(B / "batch.out", "a") as out:
            subprocess.run([sys.executable, str(ROOT / "bench/batch.py")], env=env, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
