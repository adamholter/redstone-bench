#!/usr/bin/env python3
"""Run the whole model x task matrix. Safe to kill and restart: finished runs are skipped.

usage: batch.py            run everything that is not done (4 at a time)
       batch.py --audit    print what really finished, from the files on disk
"""
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
B = ROOT / "runs" / os.environ.get("RSB_BATCH_DIR", "_batch")     # a second batch (another vendor) gets its own state folder
E6 = ("low", "medium", "high", "xhigh", "max", "ultra")
CONFIGS = ([("sol", e) for e in E6] + [("terra", e) for e in E6] + [("astra", e) for e in E6] +
           [("luna", e) for e in E6[:5]])      # Luna has no ultra
if os.environ.get("RSB_CONFIGS"):                                   # e.g. RSB_CONFIGS=carbon-high
    CONFIGS = [tuple(c.rsplit("-", 1)) for c in os.environ["RSB_CONFIGS"].split(",")]
BUDGET, MINUTES, WORKERS, RETRIES = 40, 60, int(os.environ.get("RSB_WORKERS", 12)), 4


def tasks():
    order = ["door_2x2", "door_3x3", "door_4x4", "mech_extender2", "mech_extender3", "logic_not", "logic_xor",
             "logic_full_adder", "logic_adder2", "logic_seg7", "mem_tff", "mem_rs_latch", "mem_counter2",
             "time_clock20", "time_clock120", "time_pulse30"]
    have = {f.stem for f in (ROOT / "tasks").glob("*.json")}
    return [t for t in order if t in have]


def jobs():   # the quick settings first so the table fills in, then the slow ones task by task
    fast = [c for c in CONFIGS if c[1] in ("minimal", "low", "medium", "high")]
    slow = [c for c in CONFIGS if c not in fast]
    return [(m, e, t) for t in tasks() for m, e in fast] + [(m, e, t) for t in tasks() for m, e in slow]


def rdir(m, e, t):
    return ROOT / "runs" / f"{m}-{e}" / t


def audit():
    rows, done, failed = [], 0, 0
    print(f"{'task':18}" + "".join(f"{(m + '-' + e)[:8]:9}" for m, e in CONFIGS))
    for t in tasks():
        line = f"{t:18}"
        for m, e in CONFIGS:
            f = rdir(m, e, t) / "result.json"
            if f.exists():
                r = json.loads(f.read_text())
                done += 1
                line += f"{str((r.get('best') or {}).get('volume') or 'none'):9}"
            elif (B / "gave_up" / f"{m}-{e}__{t}").exists():
                failed += 1
                line += f"{'ERROR':9}"
            else:
                line += f"{'.':9}"
        print(line)
    total = len(jobs())
    print(f"\n{done}/{total} runs finished, {failed} gave up after harness errors, {total - done - failed} to go")
    return done + failed >= total


def kill_strays():
    """A restarted batch must not leave old CLI processes working on runs it is about to redo."""
    mine = [f for m, e in CONFIGS for f in (ROOT / "runs" / f"{m}-{e}").glob("*/codex.pid")]    # never another batch's runs
    for f in list((B / "pids").glob("*.pid")) + mine:
        try:
            pid = int(f.read_text())
            cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
            if ("bench/run.py" in cmd or "codex" in cmd or "agy" in cmd or "cursor-agent" in cmd or "muse exec" in cmd) and not (f.parent / "result.json").exists():
                os.killpg(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass
        if f.parent.name == "pids":
            f.unlink(missing_ok=True)


def ready():
    """The judge must be up and the disk must have room, or a run would be wasted."""
    try:
        socket.create_connection(("127.0.0.1", 25592), timeout=5).close()
    except OSError:
        return False
    return shutil.disk_usage(ROOT).free > 3 << 30


def run_one(m, e, t, lock):
    name = f"{m}-{e}__{t}"
    for attempt in range(1, RETRIES + 1):
        if (rdir(m, e, t) / "result.json").exists():
            return
        while not ready():
            (B / "waiting").write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + " server down or disk full\n")
            time.sleep(30)
        (B / "waiting").unlink(missing_ok=True)
        with open(B / "logs" / f"{name}.log", "a") as log:
            p = subprocess.Popen([sys.executable, str(ROOT / "bench/run.py"), "--task", t, "--model", m, "--effort", e,
                                  "--budget", str(BUDGET), "--minutes", str(MINUTES)],
                                 stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, start_new_session=True)
            (B / "pids" / f"{name}.pid").write_text(str(p.pid))
            code = p.wait()
            (B / "pids" / f"{name}.pid").unlink(missing_ok=True)
        with lock:
            with open(B / "ledger.jsonl", "a") as led:
                led.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "job": name, "try": attempt, "exit": code}) + "\n")
        if (rdir(m, e, t) / "result.json").exists():
            return
        time.sleep(600 if code == 3 else 30)     # quota or auth trouble: wait before trying again
    (B / "gave_up" / name).write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))


def main():
    for d in ("logs", "pids", "gave_up"):
        (B / d).mkdir(parents=True, exist_ok=True)
    if "--audit" in sys.argv:
        audit()
        return
    kill_strays()
    todo = [j for j in jobs() if not (rdir(*j) / "result.json").exists() and not (B / "gave_up" / f"{j[0]}-{j[1]}__{j[2]}").exists()]
    lock, it = threading.Lock(), iter(todo)

    def worker():
        while True:
            with lock:
                j = next(it, None)
            if j is None:
                return
            run_one(*j, lock)
            (B / "status.json").write_text(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "last": "-".join(j)}))

    ws = [threading.Thread(target=worker) for _ in range(WORKERS)]
    [w.start() for w in ws]
    [w.join() for w in ws]
    (B / "finished").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))


if __name__ == "__main__":
    main()
