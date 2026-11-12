#!/usr/bin/env python3
"""Replay saved builds with full block-state capture and write the site's data files.

usage: export_site.py      -> site/data/index.json, site/data/builds/...
Every valid attempt of every run is replayed (so each version can be viewed in 3D); the best one per run, the
seeds and the baselines are also recorded tick by tick for the animation. Already exported files are kept.
"""
import json
import pathlib

from verify import verify

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "site/data"
FAMILIES = {"door": "Doors", "mechanical": "Piston extenders", "logic": "Logic", "memory": "Memory", "timing": "Timing"}
BLURB = {
    "door_2x2": "One lever opens and closes a 2x2 doorway.", "door_3x3": "One lever opens and closes a 3x3 doorway.",
    "door_4x4": "One lever opens and closes a 4x4 doorway.",
    "mech_extender2": "Push a block two cells up out of the floor, then pull it back.",
    "mech_extender3": "Push a block three cells up out of the floor, then pull it back.",
    "logic_not": "The lamp is on when the lever is off.", "logic_xor": "The lamp is on when exactly one lever is on.",
    "logic_full_adder": "Add three bits: a sum lamp and a carry lamp.",
    "logic_adder2": "Add two 2-bit numbers into three lamps.",
    "logic_seg7": "Four levers pick a hex digit; seven lamps show its segments.",
    "mem_tff": "Each lever press flips the lamp.", "mem_rs_latch": "One lever sets the lamp, another resets it.",
    "mem_counter2": "Two lamps count lever presses from 0 to 3.",
    "time_clock20": "The lamp turns on once every 20 ticks, exactly.",
    "time_clock120": "The lamp turns on once every 120 ticks, exactly.",
    "time_pulse30": "The lamp lights for exactly 30 ticks, once.",
}
MODELS = {"sol": "GPT-6.1 Sol", "terra": "GPT-6.1 Terra", "astra": "GPT-6 Astra", "luna": "GPT-6 Luna", "carbon": "Gemini 4 Carbon", "grok": "Grok 4.7",
          "wonder-full": "Muse Wonder Full 1.0", "wonder": "Muse Wonder 1.0", "ember": "Muse Ember 1.0", "spark13": "Muse Spark 1.3", "spark12": "Muse Spark 1.2"}
EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]


def title(task):
    return task.get("title") or task["id"].split("_")[1] + " piston door"


def ticks(r):
    return r.get("tiebreak", r.get("close_ticks", 0) + r.get("open_ticks", 0))


def pack_frames(frames):
    """Frames as a first frame plus changes, with unchanged stretches run-length encoded."""
    def as_map(f):
        return {",".join(map(str, b["pos"])): b["state"] for b in f["blocks"]}
    prev = as_map(frames[0])
    out = {"first": prev, "steps": []}
    for f in frames[1:]:
        cur = as_map(f)
        ch = {k: v for k, v in cur.items() if prev.get(k) != v}
        ch.update({k: None for k in prev if k not in cur})
        ev = f.get("event")
        if not ch and not ev and out["steps"] and "n" in out["steps"][-1]:
            out["steps"][-1]["n"] += 1
        elif not ch and not ev:
            out["steps"].append({"n": 1})
        else:
            out["steps"].append({"c": ch, **({"e": ev} if ev else {})})
        prev = cur
    return out


def replay(task, sub, path, record):
    """Write one build file. Returns its summary, or None if the build no longer passes."""
    if path.exists():
        d = json.loads(path.read_text())
        if not record or "frames" in d:
            return {k: d[k] for k in ("volume", "dims", "ticks", "blocks", "entities", "states")} | {"states": list(d["states"])}
    r = verify(task, sub, capture=True, record=record)
    if not r.get("valid"):
        return None
    lo, hi = list(r["box_min"]), list(r["box_max"])
    for st in list(r["states"].values()) + [f for f in r.get("frames", [])[:1]]:
        for b in (st["blocks"] if isinstance(st, dict) else st):
            lo = [min(a, c) for a, c in zip(lo, b["pos"])]
            hi = [max(a, c) for a, c in zip(hi, b["pos"])]
    d = {"volume": r["volume"], "dims": r["dims"], "ticks": ticks(r), "blocks": r["block_count"], "entities": r["max_entities"],
         "bounds": [lo, hi], "states": r["states"]}
    if record and r.get("frames"):
        d["frames"] = pack_frames(r["frames"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, separators=(",", ":")))
    return {**{k: d[k] for k in ("volume", "dims", "ticks", "blocks", "entities")}, "states": list(d["states"])}


def main():
    (OUT / "builds").mkdir(parents=True, exist_ok=True)
    tasks = {f.stem: json.loads(f.read_text()) for f in sorted((ROOT / "tasks").glob("*.json"))}
    runs, others = [], []
    for f in sorted((ROOT / "runs").glob("[!_]*/*/result.json")):
        r = json.loads(f.read_text())
        cfg, name = f.parent.parent.name, f.parent.name
        tid = name.split("+")[0]
        if "+" in name or tid not in tasks:
            continue
        model, effort = cfg.rsplit("-", 1)
        u, best = r.get("usage") or {}, r.get("best") or {}
        rid = f"{cfg}__{tid}"
        row = {"id": rid, "task": tid, "config": cfg, "model": MODELS.get(model, model), "effort": effort,
               "seconds": r["seconds"], "calls": r["verifier_calls"], "budget": r["budget"], "ended": r["ended"],
               "commands": r.get("shell_commands"), "tokens_in": u.get("input_tokens"), "tokens_out": u.get("output_tokens"),
               "tokens_reasoning": u.get("reasoning_output_tokens"), "started": r.get("started"),
               "message": (r.get("final_message") or "")[:700], "volume": None, "best": None, "log": [], "versions": {}}
        for x in r["attempt_log"]:
            n, vol = x["attempt"], None
            if x.get("valid"):
                sub = json.loads((f.parent / "attempts" / f"{n:03d}.json").read_text())
                s = replay(tasks[tid], sub, OUT / "builds" / rid / f"{n}.json", record=(n == best.get("attempt")))
                if s:
                    vol = s["volume"]
                    row["versions"][n] = {"volume": s["volume"], "dims": s["dims"], "ticks": s["ticks"], "states": s["states"]}
                else:
                    print("no longer valid on replay:", rid, n)
            row["log"].append([x["t"], x.get("step"), vol, "" if vol else (x.get("error") or "")[:140]])
        if row["versions"]:
            n = min(row["versions"], key=lambda k: (row["versions"][k]["volume"], row["versions"][k]["ticks"], k))
            row.update(volume=row["versions"][n]["volume"], dims=row["versions"][n]["dims"], ticks=row["versions"][n]["ticks"], best=n)
            if n != best.get("attempt"):   # make sure the best version has its animation
                sub = json.loads((f.parent / "attempts" / f"{n:03d}.json").read_text())
                replay(tasks[tid], sub, OUT / "builds" / rid / f"{n}.json", record=True)
        row["valid_calls"] = len(row["versions"])
        runs.append(row)
        print(rid, row["volume"], f"{row['valid_calls']}/{row['calls']}")

    def other(bid, tid, path, source, kind):
        s = replay(tasks[tid], json.loads(path.read_text()), OUT / "builds" / f"{bid}.json", record=True)
        if s:
            others.append({"id": bid, "task": tid, "source": source, "kind": kind, **s})

    for f in sorted((ROOT / "baselines").glob("*.json")):
        if f.stem in tasks:
            other(f"baseline__{f.stem}", f.stem, f, "Hand-made baseline", "baseline")
    other("baseline__door_2x2", "door_2x2", ROOT / "examples/door_2x2_baseline.json", "Hand-made baseline", "baseline")
    other("baseline__logic_not", "logic_not", ROOT / "examples/logic_not.json", "Hand-made baseline", "baseline")
    for f in sorted((ROOT / "seeds").glob("*.json")):
        other(f"seed__{f.stem}", f.stem, f, "Redstonepedia (found by agents)", "seed")

    tl, ids = [], list(BLURB)
    for tid in ids:
        if tid not in tasks:
            continue
        t = tasks[tid]
        cands = [(o["volume"], o["source"]) for o in others if o["task"] == tid]
        if t.get("reference"):
            cands.append((t["reference"], "human record"))
        tl.append({"id": tid, "title": title(t), "family": t.get("family", "door"), "blurb": BLURB[tid],
                   "known": min(cands) if cands else None})
    present = {r["config"] for r in runs}
    configs = [{"id": f"{m}-{e}", "model": MODELS[m], "effort": e} for m in MODELS for e in reversed(EFFORTS)
               if f"{m}-{e}" in present]
    (OUT / "index.json").write_text(json.dumps({"families": FAMILIES, "tasks": tl, "configs": configs, "runs": runs, "others": others}))
    print(len(runs), "runs,", len(others), "other builds,", sum(len(r["versions"]) for r in runs), "versions")


if __name__ == "__main__":
    main()
