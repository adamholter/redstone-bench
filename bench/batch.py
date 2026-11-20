#!/usr/bin/env python3
"""Run the whole model x task matrix. Safe to kill and restart: finished runs are skipped.

usage: batch.py            run everything that is not done (4 at a time)
       batch.py --audit    print what really finished, from the files on disk
"""
import json
import os
import pathlib
import re
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
BUDGET, MINUTES, WORKERS, RETRIES = 40, 60, int(os.environ.get("RSB_WORKERS", 12)), int(os.environ.get("RSB_RETRIES", 4))


def tasks():
    order = ["door_2x2", "door_3x3", "door_4x4", "mech_extender2", "mech_extender3", "logic_not", "logic_xor",
             "logic_full_adder", "logic_adder2", "logic_seg7", "mem_tff", "mem_rs_latch", "mem_counter2",
             "time_clock20", "time_clock120", "time_pulse30"]
    have = {f.stem for f in (ROOT / "tasks").glob("*.json")}
    return [t for t in order if t in have]


def jobs():   # the quick settings first so the table fills in, then the slow ones task by task
    if os.environ.get("RSB_ORDER") == "by_config":
        return [(m, e, t) for m, e in CONFIGS for t in tasks()]
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
            if ("bench/run.py" in cmd or "codex" in cmd or "agy" in cmd or "claude" in cmd or "cursor-agent" in cmd or "muse exec" in cmd or "opencode" in cmd) and not (f.parent / "result.json").exists():
                os.killpg(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass
        if f.parent.name == "pids":
            f.unlink(missing_ok=True)


def codex_week_used():
    """Percent of the weekly Codex limit used, from the newest session logs; None if there is no current reading."""
    days = [time.strftime("%Y/%m/%d", time.localtime(time.time() - d * 86400)) for d in (0, 1)]
    homes = [pathlib.Path.home() / ".codex", pathlib.Path.home() / ".cache/redstone-bench/codex-home"]
    logs = [f for h in homes for d in days for f in (h / "sessions" / d).glob("rollout-*.jsonl")]
    for f in sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
        with open(f, "rb") as h:
            h.seek(max(0, f.stat().st_size - 300_000))
            hits = re.findall(r'"primary":\{"used_percent":([\d.]+),"window_minutes":10080,"resets_at":(\d+)', h.read().decode("utf8", "ignore"))
        if hits:
            used, resets = hits[-1]
            return float(used) if int(resets) > time.time() else None
    return None


_CLAIM_LOCK = threading.Lock()
_LAST_LIMIT_CHECK = 0.0
_LAST_LIMIT_RESULT = None


def _codex_app_server_call(calls):
    """Run one or more JSON-RPC calls against `codex app-server` stdio and return their responses."""
    p = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        def rpc(req_id, method, params=None):
            req = {"id": req_id, "method": method}
            if params is not None:
                req["params"] = params
            p.stdin.write(json.dumps(req) + "\n")
            p.stdin.flush()
            for line in p.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == req_id:
                    return msg
            raise RuntimeError("codex app-server closed before reply")

        rpc(1, "initialize", {"clientInfo": {"name": "redstone-bench", "version": "1"}})
        p.stdin.write('{"method":"initialized"}\n')
        p.stdin.flush()
        out = []
        for idx, (method, params) in enumerate(calls, start=2):
            out.append(rpc(idx, method, params))
        return out
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def codex_maybe_claim_reset(threshold=99.0):
    """Read live Codex limits; if >= threshold% used (i.e. <1% left) or blocked, claim the earliest-expiring banked reset."""
    global _LAST_LIMIT_CHECK, _LAST_LIMIT_RESULT
    with _CLAIM_LOCK:
        now = time.time()
        if now - _LAST_LIMIT_CHECK < 20 and _LAST_LIMIT_RESULT is not None:
            return _LAST_LIMIT_RESULT
        _LAST_LIMIT_CHECK = now
        try:
            [res] = _codex_app_server_call([("account/rateLimits/read", {})])
            data = res.get("result") or {}
            primary = (data.get("rateLimits") or {}).get("primary") or {}
            used = primary.get("usedPercent")
            allowed = data.get("ordinaryUsageAllowed", True)
            reached = (data.get("rateLimits") or {}).get("rateLimitReachedType")
            credits = [
                c for c in ((data.get("rateLimitResetCredits") or {}).get("credits") or [])
                if c.get("status") == "available" and c.get("id")
            ]
            depleted = (used is not None and float(used) >= threshold) or (not allowed) or bool(reached)
            if depleted:
                if not credits:
                    _LAST_LIMIT_RESULT = {"used": used, "allowed": allowed, "depleted": True, "claimed": False, "reason": "no_credits"}
                    return _LAST_LIMIT_RESULT
                credits.sort(key=lambda c: c.get("expiresAt") or float("inf"))
                chosen = credits[0]
                cid = chosen["id"]
                [claim] = _codex_app_server_call([(
                    "account/rateLimitResetCredit/consume",
                    {"creditId": cid, "idempotencyKey": f"rsb-auto-{cid}"},
                )])
                ok = (claim.get("result") or {}).get("outcome") == "reset"
                _LAST_LIMIT_RESULT = {"used": 0 if ok else used, "allowed": True if ok else allowed, "depleted": not ok, "claimed": ok, "creditId": cid, "result": claim.get("result"), "error": claim.get("error")}
                return _LAST_LIMIT_RESULT
            _LAST_LIMIT_RESULT = {"used": used, "allowed": allowed, "depleted": False, "claimed": False}
            return _LAST_LIMIT_RESULT
        except Exception as exc:
            _LAST_LIMIT_RESULT = {"error": str(exc), "depleted": False}
            return _LAST_LIMIT_RESULT


_QUOTA_LOCK = threading.Lock()
_LAST_AGY_CHECK = 0.0
_LAST_AGY_OK = True


def agy_quota_ok(min_frac=0.02):
    """Check Gemini Models 5h & weekly remaining_fraction via `agy -p /quota`; cached 60s."""
    global _LAST_AGY_CHECK, _LAST_AGY_OK
    with _QUOTA_LOCK:
        now = time.time()
        if now - _LAST_AGY_CHECK < 60:
            return _LAST_AGY_OK
        _LAST_AGY_CHECK = now
        try:
            p = subprocess.run(
                [str(pathlib.Path.home() / ".local/bin/agy"), "-p", "/quota", "--output-format", "json"],
                capture_output=True, text=True, timeout=25, stdin=subprocess.DEVNULL,
            )
            data = json.loads(p.stdout)
            groups = ((data.get("command") or {}).get("data") or {}).get("groups") or []
            for g in groups:
                if "gemini" in (g.get("name") or "").lower():
                    for b in g.get("buckets") or []:
                        frac = b.get("remaining_fraction")
                        if frac is not None and float(frac) <= min_frac:
                            _LAST_AGY_OK = False
                            return False
            _LAST_AGY_OK = True
            return True
        except Exception:
            return _LAST_AGY_OK


_LAST_OC_CHECK = 0.0
_LAST_OC_OK = True


def opencode_quota_ok():
    """OpenCode Go's rolling, weekly and monthly usage from the CLI's own limits endpoint; cached 60s. A new run only
    starts below RSB_OPENCODE_STOP percent (default 80) in every window, so runs already going can finish inside the cap."""
    global _LAST_OC_CHECK, _LAST_OC_OK
    with _QUOTA_LOCK:
        if time.time() - _LAST_OC_CHECK < 60:
            return _LAST_OC_OK
        _LAST_OC_CHECK = time.time()
        try:
            p = subprocess.run(["opencode", "api", "GET", "/api/experimental/usage/limits"],
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            go = (json.loads(p.stdout).get("data") or {}).get("go") or {}
            stop = float(os.environ.get("RSB_OPENCODE_STOP", "80"))
            _LAST_OC_OK = bool(go) and not any(w.get("limited") or float(w.get("percent") or 0) >= stop for w in go.values())
            (B / "opencode_usage.json").write_text(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "go": go}))
        except Exception:
            pass
        return _LAST_OC_OK


def wait_until_ok():
    f = B / "wait_until"
    if not f.exists():
        return True
    try:
        until = float(f.read_text().strip())
    except Exception:
        return True
    if time.time() < until:
        return False
    f.unlink(missing_ok=True)
    return True


def ready():
    """The judge must be up and the disk must have room, or a run would be wasted."""
    try:
        socket.create_connection(("127.0.0.1", 25592), timeout=5).close()
    except OSError:
        return False
    if not wait_until_ok():
        return False
    if os.environ.get("RSB_AGY_QUOTA") == "1" and not agy_quota_ok():
        return False
    if os.environ.get("RSB_OPENCODE_QUOTA") == "1" and not opencode_quota_ok():
        return False
    if os.environ.get("RSB_CODEX_AUTO_CLAIM") == "1":
        st = codex_maybe_claim_reset(float(os.environ.get("RSB_CODEX_CLAIM_AT", "99")))
        if st and st.get("depleted") and not st.get("claimed"):
            return False
    stop = os.environ.get("RSB_CODEX_STOP")        # optional reserve for other Codex work
    if stop and (codex_week_used() or 0) >= float(stop):
        return False
    return shutil.disk_usage(ROOT).free > 3 << 30


def _is_quota_error(err_path):
    """Return (is_quota, resets_at_epoch_or_None) from a run's error.json."""
    if not err_path.exists():
        return False, None
    try:
        d = json.loads(err_path.read_text())
    except Exception:
        return False, None
    resets = d.get("rate_limit_resets_at")
    txt = " ".join(str(x) for x in (d.get("errors") or [])).lower()
    if resets or any(w in txt for w in ("rate_limit", "rate limit", "hit your limit", "quota", "resource_exhausted", "429", "usage limit")):
        return True, (float(resets) if resets else None)
    return False, None


def run_one(m, e, t, lock):
    name = f"{m}-{e}__{t}"
    attempt = 0
    while attempt < RETRIES:
        if (rdir(m, e, t) / "result.json").exists():
            return
        while not ready():
            (B / "waiting").write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + " server down, disk full or subscription quota paused\n")
            time.sleep(60)
        (B / "waiting").unlink(missing_ok=True)
        attempt += 1
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
        if code == 3 and any(os.environ.get(f"RSB_{v}_QUOTA") == "1" for v in ("CLAUDE", "AGY", "OPENCODE")):
            is_q, resets = _is_quota_error(rdir(m, e, t) / "error.json")
            if is_q:
                until = max((resets + 90) if resets else 0, time.time() + 600)
                with lock:
                    cur = float((B / "wait_until").read_text().strip()) if (B / "wait_until").exists() else 0
                    if until > cur:
                        (B / "wait_until").write_text(f"{int(until)}\n")
                attempt -= 1    # quota pause never burns a retry or triggers gave_up
                continue
        if attempt < RETRIES:
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
