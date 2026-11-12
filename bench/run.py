#!/usr/bin/env python3
"""Run one model on one task through its native CLI harness (subscription, no API).

usage: run.py --task door_4x4 --model sol --effort xhigh [--budget 40] [--minutes 100]

The model works as an agent in a scratch workspace. It has a shell but no network; it calls
`python3 verify.py submission.json`, which hands the file to this process, which runs it in real
Minecraft and hands the result back. The best valid design across all its verifier calls is its score.
"""
import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import threading
import time

from verify import verify

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODELS = {"sol": "gpt-6.1-sol", "terra": "gpt-6.1-terra", "astra": "gpt-6-astra", "luna": "gpt-6-luna",
          "carbon": "gemini-4-carbon", "grok": "grok-4.7"}
AGY = {"carbon"}                       # these run through the Antigravity CLI (agy) instead of Codex
CURSOR = {"grok"}                      # these run through the Cursor CLI (cursor-agent)
AGY_HOMES = pathlib.Path("/private/tmp/rsb-agy-homes")
AGY_LINKS = ["antigravity-cli/antigravity-oauth-token", "antigravity-cli/installation_id", "oauth_creds.json",
             "google_accounts.json", "installation_id"]
# agy has no network-off switch, so the web, browser, MCP and sub-agent tools are denied and every command is audited
AGY_DENY = ["search_web", "read_url_content", "open_browser_url", "read_browser_page", "browser_subagent", "call_mcp_tool",
            "invoke_subagent", "define_subagent", "manage_subagents", "generate_image", "search_marketplace", "schedule",
            "read_resource", "list_resources", "run_workflow", "send_message", "manage_inbox", "ask_question",
            "ask_permission", "ask_custom_permission", "execute_browser_javascript", "capture_browser_screenshot",
            "capture_browser_console_logs", "list_browser_pages", "click_browser_pixel", "browser_click_element",
            "browser_drag_pixel_to_pixel", "browser_get_dom", "browser_get_network_request", "browser_input",
            "browser_list_network_requests", "browser_mouse_down", "browser_mouse_up", "browser_move_mouse",
            "browser_press_key", "browser_refresh_page", "browser_resize_window", "browser_scroll", "browser_scroll_dom",
            "browser_select_option"]
NETWORK_WORDS = ("curl ", "wget ", "http://", "https://", "pip install", "pip3 install", "git clone", "npm i", "urllib", "requests.")


def agy_setup(tag, ws, links=AGY_LINKS):
    """A fresh HOME (keeps Adam's Gemini skills and memories out; the Library link avoids a Keychain dialog) and a
    macOS sandbox profile that hides his home folder, this repo and the other runs' workspaces from the agent."""
    home = AGY_HOMES / tag
    (home / ".gemini/antigravity-cli").mkdir(parents=True, exist_ok=True)
    real = pathlib.Path.home()
    for rel, src in [("Library", real / "Library")] + [(f".gemini/{f}", real / ".gemini" / f) for f in links]:
        if src.exists() and not (home / rel).is_symlink():
            os.symlink(src, home / rel)
    lit = " ".join(f'(literal "{real}/.gemini/{f}")' for f in links)
    prof = home / "sandbox.sb"
    # /private/tmp and the per-user temp folders hold other agents' scratch files (including other runs' scripts)
    # and private files, so they are hidden too; the agent gets its own TMPDIR inside its home
    shared = '(subpath "/private/tmp") (subpath "/private/var/folders")'
    prof.write_text(f'''(version 1)
(allow default)
(deny file-read* (subpath "{real}") {shared})
(allow file-read-metadata (subpath "{real}") {shared})
(allow file-read* (subpath "{real}/Library") (subpath "{real}/.local") {lit} (subpath "{home}") (subpath "{ws}"))
(deny file-write* (subpath "{real}") {shared})
(allow file-read* file-write* (regex #"^/private/var/folders/[^/]+/[^/]+/T/xcrun_db"))
(allow file-write* (subpath "{home}") (subpath "{ws}") (subpath "{real}/Library/Caches") (subpath "{real}/Library/Logs")
  (subpath "{real}/Library/Application Support") (subpath "{real}/.local/share"))
''')
    (home / "tmp").mkdir(exist_ok=True)
    return home, prof


def agy_drop(home):
    for rel in ["Library"] + [f".gemini/{f}" for f in AGY_LINKS]:
        if (home / rel).is_symlink():
            (home / rel).unlink()


def agy_events(d, t):
    """Antigravity's stream as the same (kind, payload) the Codex stream gives."""
    su = d.get("step_update") or {}
    if d.get("event") == "step_update" and su.get("state") == "DONE" and su.get("step_type") == "tool":
        info = su.get("tool_info") or {}
        par = info.get("parameters") or {}
        cmd = par.get("CommandLine") or f"[{su.get('tool_name')}] " + json.dumps(par)[:200]
        return "command", {"command": str(cmd), "tool": su.get("tool_name"), "output": str(info.get("output", ""))[:300]}
    if d.get("event") == "result":
        r = d.get("result") or {}
        u = r.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "cached_input_tokens": u.get("cache_read_tokens"),
                 "output_tokens": (u.get("output_tokens") or 0) + (u.get("thinking_tokens") or 0),
                 "reasoning_output_tokens": u.get("thinking_tokens")}
        if r.get("status") == "SUCCESS":
            return "done", {"usage": usage, "final": r.get("response") or ""}
        return "error", {"text": json.dumps(r)[:600]}
    return None, None
def cursor_events(d, t):
    """Cursor's stream-json as the same (kind, payload) the other runners give."""
    if d.get("type") == "tool_call" and d.get("subtype") == "completed":
        tc = d.get("tool_call") or {}
        name = next(iter(tc), "tool")
        v = tc.get(name) or {}
        args = v.get("args") or {}
        cmd = args.get("command") or f"[{name}] " + json.dumps(args)[:200]
        return "command", {"command": str(cmd), "tool": name, "output": json.dumps(v.get("result", ""))[:300]}
    if d.get("type") == "result":
        u = d.get("usage") or {}
        usage = {"input_tokens": u.get("inputTokens"), "cached_input_tokens": u.get("cacheReadTokens"),
                 "output_tokens": u.get("outputTokens")}
        if d.get("is_error") or d.get("subtype") != "success":
            return "error", {"text": json.dumps(d)[:600]}
        return "done", {"usage": usage, "final": d.get("result") or ""}
    return None, None


PRIVATE = pathlib.Path.home() / ".cache/redstone-bench"   # 0700, not a shared temp dir
CODEX_HOME, AGENT_HOME = PRIVATE / "codex-home", PRIVATE / "home"
WORK = pathlib.Path("/private/tmp/rsb-work")
WORK = WORK.resolve() if WORK.exists() else WORK

CLIENT = '''#!/usr/bin/env python3
"""usage: python3 verify.py submission.json   -> prints the verifier's JSON result"""
import pathlib, shutil, sys, time, uuid
q = pathlib.Path(__file__).resolve().parent / ".queue"
rid = uuid.uuid4().hex[:8]
shutil.copy(sys.argv[1], q / f"{rid}.tmp")
(q / f"{rid}.tmp").rename(q / f"{rid}.req")
res = q / f"{rid}.res"
t0 = time.time()
while not res.exists():
    if time.time() - t0 > 600:
        sys.exit("verifier did not answer")
    time.sleep(0.2)
time.sleep(0.1)
print(res.read_text())
'''


COMMON_SUBMISSION = """`state` is a block state exactly as `/setblock` takes it.
Blocks are placed one by one in list order with `/setblock` while the game is frozen. Order matters in two ways.
Blocks that need support (torches, redstone dust, repeaters, levers) must come after the block that holds them.
And a block placed this way tells its neighbours it arrived, but some blocks do not look at their own surroundings
until a neighbour changes: a redstone lamp placed next to a torch that is already lit stays dark. Put such blocks
before the things that power them.
Optional key `"placement": "raw"` places every block exactly as written with no block updates at all
(`/setblock ... strict`), so you can describe a machine mid-state: extended pistons with their heads, powered
components, exact redstone wire shapes.
No command blocks, structure blocks, barriers, bedrock, portals or moving pistons. A piston head needs its extended piston behind it."""

VERIFIER = """## Verifier

    python3 verify.py submission.json

It runs the test above in the real game and prints JSON: `valid`, and if valid {fields}.
If it fails you get the reason{extra} and a list of every block in the world at the moment it failed.

You have {budget} verifier calls. Your score is your best valid result across all of them, so there is no
final submit step: when you are done, just stop. A working build of any size beats no build, so get something
valid first, then use the calls you have left to make it smaller. There is no internet. Work only inside this folder.
"""


def box_txt(task):
    b = task["max_box"]
    return (f"Build box: x 0..{b[0]-1}, y 0..{b[1]-1}, z 0..{b[2]-1}. y is up. Nothing may ever be outside it. "
            "It floats in an empty void: there is no ground, and nothing but your blocks.")


def seed_txt(seed, what):
    if not seed:
        return ""
    ticks = (f"closes in {seed['close_ticks']} ticks, opens in {seed['open_ticks']}" if "close_ticks" in seed
             else f"delay {seed['delay_ticks']} ticks")
    return (f"## Starting point\n\n`seed.json` is the smallest known working {what}: volume "
            f"{seed['volume']} ({'x'.join(map(str, seed['dims']))}), {ticks}. It passes this verifier as is. Your job is to "
            f"beat it: a valid build with a smaller volume, or the same volume and fewer ticks. Anything else scores as no improvement.\n\n")


def circuit_md(task, budget, seed=None):
    t = task["test"]
    ins, outs = task["inputs"], task["outputs"]
    if t["type"] == "script":
        how = f"""2. The game runs. With every lever off, the lamps must show their starting values.
3. The levers are then switched through the {len(t['script'])} steps listed in `test_script.json`, in order. After each step every
   lamp must reach the value given there and hold it for the step's `hold` ticks (20 game ticks unless stated),
   within {task.get('timeout_ticks', 100)} ticks. Flicker before it settles is fine. Levers named in one step are switched on the same tick."""
    elif t["type"] == "clock":
        P = t["period"]
        how = f"""2. The game runs with the lever off. The lamp must be off and stay off for {2*P} game ticks.
3. The lever is switched on and the lamp is watched for {8*P} ticks. It must first turn on within {2*P} ticks, and
   from then on turn on once every {P} ticks exactly (measured from one off-to-on moment to the next).
4. The lever is switched off. The lamp must go off within {3*P} ticks and stay off for {3*P} ticks.
5. Steps 3 and 4 run {t.get('cycles', 2)} times in a row."""
    else:
        L, d, q = t["length"], t.get("max_delay", 40), t.get("quiet", 60)
        how = f"""2. The game runs with the lever off. The lamp must be off and stay off for 40 game ticks.
3. The lever is switched on and left on, and the lamp is watched for {d+L+q} ticks. It must light within {d} ticks,
   stay lit for exactly {L} ticks in a row, then stay off.
4. The lever is switched off and the lamp is watched for {q} ticks. It must stay off the whole time.
5. Steps 3 and 4 run {t.get('cycles', 3)} times in a row."""
    return f"""# {task['title']}: build the smallest one that works

You are designing a redstone circuit for Minecraft Java Edition 26.3 (vanilla, no mods).
Your design is built and run in a real Minecraft server, so it has to work under real game rules.

## The circuit

- {box_txt(task)}
- Inputs: {len(ins)} lever{'s' if len(ins) > 1 else ''}, named {', '.join(ins)}. Outputs: {len(outs)} redstone lamp{'s' if len(outs) > 1 else ''}, named {', '.join(outs)}.
  You choose where they go and say so in your submission.
- A lamp is "on" when the block at its position is a lit redstone lamp. Note that a real redstone lamp turns on at once but takes 4 game ticks to turn off.
- All times are game ticks (a repeater set to 1 delays by 2 game ticks).

{task['desc']}

## How it is tested

1. Your blocks are placed while the game is frozen, every lever off.
{how}

## Score

Lower volume wins. Volume is the bounding box (width x height x depth, in blocks) around everything your
build ever occupies at any tick, levers and lamps included.
Ties are broken by fewer ticks of delay between a lever change and the lamps being right. The number of entities seen is reported too.

## Submission

Write a JSON file:

    {{"blocks": [{{"pos": [x, y, z], "state": "minecraft:redstone_lamp"}}, ...],
     "inputs": {{{', '.join(f'"{n}": [x, y, z]' for n in ins)}}},
     "outputs": {{{', '.join(f'"{n}": [x, y, z]' for n in outs)}}}}}

Each input is the position of a lever in your list, each output the position of a redstone lamp in your list.
{COMMON_SUBMISSION}

{seed_txt(seed, 'build for this task')}`example_not.json` is a working NOT gate (one lever A, one lamp Q, volume 6), to show the format.

{VERIFIER.format(fields='`volume`, `dims`, `delay_ticks`, `max_entities`', extra='', budget=budget)}"""


def extender_md(task, budget, seed=None):
    col, n, fy = task["opening"], task["reach"], task["floor_y"]
    x, z = col[0][0], col[0][2]
    return f"""# {task['title']}: build the smallest one that works

You are designing a redstone piston extender for Minecraft Java Edition 26.3 (vanilla, no mods).
Your design is built and run in a real Minecraft server, so it has to work under real game rules.

## The extender

A block has to be pushed {n} cells up out of a floor, and pulled all the way back down.

- {box_txt(task)}
- The floor line is y {fy}. Your whole machine sits below it (y {fy-1} and lower).
- The column: the {n} cells x {x}, z {z}, y {fy}..{fy+n-1}. It is the only place above the floor line your blocks may ever be.
  Every other cell with y {fy} or higher must be air at every tick, and so must the column above y {fy+n-1}.
- Input: exactly one lever, anywhere below the floor line.
- Lever off: retracted. Every cell of the column is air.
- Lever on: extended. The top cell of the column (x {x}, y {fy+n-1}, z {z}) holds a full solid block from `door_blocks.txt`, at rest
  (not a piston head, not a block in motion). The cells below it in the column may hold anything.

## How it is tested

1. Your blocks are placed while the game is frozen, lever off. Do not put blocks in the column.
2. The game runs. The extender must be retracted and stay retracted for 40 game ticks.
3. The lever is switched on. It must become extended and stay extended for 40 ticks, within {task['timeout_ticks']} ticks.
4. The lever is switched off. It must become retracted and stay retracted for 40 ticks, within {task['timeout_ticks']} ticks.
5. Steps 3 and 4 run {task['cycles']} times in a row. Every cycle must work.

## Score

Lower volume wins. Volume is the bounding box (width x height x depth, in blocks) around everything your
build ever occupies at any tick, including the column. The lever alone is not counted.
Ties are broken by fewer ticks to extend plus retract. The number of entities seen (falling blocks, items) is reported too.

## Submission

Write a JSON file:

    {{"blocks": [{{"pos": [x, y, z], "state": "minecraft:sticky_piston[facing=up]"}}, ...],
     "input": [x, y, z]}}

`input` is the position of your lever.
{COMMON_SUBMISSION}

{seed_txt(seed, 'extender for this task')}`example_2x2.json` is a working 2x2 piston door from a different task (lever off: four cells air; lever on: four cells filled), to show the format.

{VERIFIER.format(fields='`volume`, `dims`, `close_ticks` (ticks to extend), `open_ticks` (ticks to retract), `max_entities`', extra=', the state of each column cell', budget=budget)}"""


def task_md(task, budget, seed=None):
    kind = task.get("kind", "door")
    if kind == "circuit":
        return circuit_md(task, budget, seed)
    if kind == "extender":
        return extender_md(task, budget, seed)
    n = task["id"].split("_")[1]
    op = task["opening"]
    xs, ys, z = sorted({p[0] for p in op}), sorted({p[1] for p in op}), op[0][2]
    ref = task.get("reference")
    return f"""# Build the smallest working {n} piston door

You are designing a redstone piston door for Minecraft Java Edition 26.3 (vanilla, no mods).
Your design is built and run in a real Minecraft server, so it has to work under real game rules.

## The door

- {box_txt(task)}
- Doorway: the {len(op)} cells with x {xs[0]}..{xs[-1]}, y {ys[0]}..{ys[-1]}, z {z}.
- Walkway: a player must be able to walk straight through. The cells in front of and behind the doorway (same x and y, every other z in the box) must be air at every tick. Door blocks may only move within the doorway's own z layer, or come from the sides, top or bottom.
- Input: exactly one lever, anywhere in the box.
- Lever off: the door is open. Every doorway cell is air.
- Lever on: the door is closed. Every doorway cell holds a full solid block from `door_blocks.txt`, at rest (not a piston head, not a block in motion).

## How it is tested

1. Your blocks are placed while the game is frozen, lever off. Do not put blocks in the doorway.
2. The game runs. The door must be open and stay open for 40 game ticks.
   (Or set `"start": "closed"`: the build is placed closed, door blocks in the doorway, lever on, and must first hold closed for 40 ticks and then open when the lever is switched off.)
3. The lever is switched on. The door must become closed and stay closed for 40 ticks, within {task['timeout_ticks']} ticks.
4. The lever is switched off. The door must become open and stay open for 40 ticks, within {task['timeout_ticks']} ticks.
5. Steps 3 and 4 run {task['cycles']} times in a row. Every cycle must work.

## Score

Lower volume wins. Volume is the bounding box (width x height x depth, in blocks) around everything your
build ever occupies at any tick, including extended pistons, moving blocks and the doorway itself.
The lever alone is not counted (the piston door community's convention).
Ties are broken by fewer ticks to close plus open. The number of entities seen (falling blocks, items) is reported too.
{f"For reference, the human record for this door is a volume of {ref}." if ref else ""}

## Submission

Write a JSON file:

    {{"blocks": [{{"pos": [x, y, z], "state": "minecraft:sticky_piston[facing=east]"}}, ...],
     "input": [x, y, z]}}

`input` is the position of your lever. Optional key `"start": "closed"` (see above).
{COMMON_SUBMISSION}

{seed_txt(seed, 'door for this doorway')}`example_2x2.json` is a working door for a smaller 2x2 doorway at x 3..4, y 0..1, z 0 (volume 28), to show the format.

{VERIFIER.format(fields='`volume`, `dims`, `close_ticks`, `open_ticks`, `max_entities`', extra=', the state of each doorway cell', budget=budget)}"""


def reap(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def key(r):
    return (r["volume"], r.get("tiebreak", r.get("close_ticks", 0) + r.get("open_ticks", 0)))


def sh(*argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20, cwd=ROOT).stdout.strip()
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default="sol")
    ap.add_argument("--effort", default="xhigh")
    ap.add_argument("--budget", type=int, default=40)
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--seed", action="store_true", help="variant: start from seeds/<task>.json, the best known door")
    a = ap.parse_args()

    task = json.loads((ROOT / f"tasks/{a.task}.json").read_text())
    cfg = f"{a.model}-{a.effort}"
    name = a.task + ("+seed" if a.seed else "")
    out = ROOT / "runs" / cfg / name
    if (out / "result.json").exists():
        print("already done:", out)
        return
    shutil.rmtree(out, ignore_errors=True)
    (out / "attempts").mkdir(parents=True)
    ws = WORK / f"{cfg}__{name}"
    shutil.rmtree(ws, ignore_errors=True)
    (ws / ".queue").mkdir(parents=True)
    seed = None
    if a.seed:
        seed_sub = json.loads((ROOT / f"seeds/{a.task}.json").read_text())
        seed = verify(task, seed_sub)
        if not seed["valid"]:
            raise SystemExit(f"seed does not verify: {seed['errors']}")
        shutil.copy(ROOT / f"seeds/{a.task}.json", ws / "seed.json")
    (ws / "TASK.md").write_text(task_md(task, a.budget, seed))
    (ws / "verify.py").write_text(CLIENT)
    if task.get("kind", "door") == "circuit":
        shutil.copy(ROOT / "examples/logic_not.json", ws / "example_not.json")
        if task["test"]["type"] == "script":
            (ws / "test_script.json").write_text(json.dumps(task["test"]["script"], indent=1))
    else:
        shutil.copy(ROOT / "examples/door_2x2_baseline.json", ws / "example_2x2.json")
        tags = json.loads((ROOT / "datapack/rsb/data/rsb/tags/block/door.json").read_text())["values"]
        (ws / "door_blocks.txt").write_text("\n".join(tags) + "\n")
    shutil.copy(ws / "TASK.md", out / "TASK.md")           # exactly what the model was given
    (out / "task.json").write_text(json.dumps(task, indent=1))
    given = {f.name for f in ws.iterdir()}

    state = {"n": 0, "best": None, "stop": False, "log": [], "commands": 0, "timeline": [], "errors": []}

    def serve():
        # The queue lives in the agent-writable workspace. Hold the directory by fd and do every
        # open/unlink relative to it, never following links, so a swapped dir or planted link is inert.
        try:
            serve_loop()
        except Exception as e:      # disk full, queue gone: the run cannot be trusted, main() stops it
            state["fault"] = repr(e)

    def checked(sub):
        """One verifier call. If the Minecraft server is away (restart), wait for it; give up after ~4 minutes."""
        for i in range(12):
            try:
                return verify(task, sub)
            except OSError as e:
                err = e
                time.sleep(20)
            except Exception as e:      # a submission the verifier chokes on: tell the model, do not count it
                state["n"] -= 1
                return {"valid": False, "errors": [f"verifier crashed, call not counted: {e!r}"], "harness_fault": True}
        state["fault"] = f"verifier down: {err!r}"
        raise err

    def serve_loop():
        qfd = os.open(ws / ".queue", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        while not state["stop"]:
            names = sorted(e.name for e in os.scandir(qfd) if e.name.endswith(".req") and "/" not in e.name)
            for name in names:
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=qfd)
                    with os.fdopen(fd, "r", errors="replace") as f:
                        raw = f.read(2_000_000)
                except OSError:
                    try:
                        os.unlink(name, dir_fd=qfd)
                    except OSError:
                        pass
                    continue
                state["n"] += 1
                n = state["n"]
                if n > a.budget:
                    res = {"valid": False, "errors": ["verifier budget used up"]}
                else:
                    try:
                        sub = json.loads(raw)
                    except json.JSONDecodeError as e:
                        sub, res = None, {"valid": False, "errors": [f"submission is not valid JSON: {e}"]}
                    if sub is not None:
                        res = checked(sub)
                    res["verifier_calls_left"] = max(0, a.budget - state["n"])
                    (out / "attempts" / f"{n:03d}.json").write_text(raw)
                    (out / "attempts" / f"{n:03d}.result.json").write_text(json.dumps(res, indent=1))
                    if res.get("valid") and (state["best"] is None or key(res) < key(state["best"])):
                        state["best"] = dict(res, attempt=n)
                    state["log"].append({"attempt": n, "t": round(time.time() - t0), "step": state["commands"] + 1,
                                         "valid": res.get("valid"),
                                         "volume": res.get("volume"), "tiebreak": res.get("tiebreak"),
                                         "verify_seconds": res.get("verify_seconds"),
                                         "error": (res.get("errors") or [""])[0][:100]})
                    print(json.dumps(state["log"][-1]), flush=True)
                dst = name[:-4] + ".res"
                try:
                    os.unlink(dst, dir_fd=qfd)
                except OSError:
                    pass
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=qfd)
                with os.fdopen(fd, "w") as f:
                    f.write(json.dumps(res, indent=1))
                os.unlink(name, dir_fd=qfd)
            time.sleep(0.2)

    PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE.chmod(0o700)
    CODEX_HOME.mkdir(exist_ok=True)
    AGENT_HOME.mkdir(exist_ok=True)
    if not (CODEX_HOME / "auth.json").exists():
        os.symlink(pathlib.Path.home() / ".codex/auth.json", CODEX_HOME / "auth.json")
    env = {"PATH": os.environ["PATH"], "HOME": str(AGENT_HOME), "CODEX_HOME": str(CODEX_HOME), "LANG": "en_US.UTF-8"}
    agy = a.model in AGY
    cur = a.model in CURSOR
    sx = agy or cur                      # sandboxed runners with a translated event stream
    events = agy_events if agy else cursor_events
    argv = ["codex", "exec", "-m", MODELS[a.model], "-c", f'model_reasoning_effort="{a.effort}"',
            "-c", 'web_search="disabled"', "-c", 'approval_policy="never"',
            *[x for f in ("plugins", "apps", "memories", "image_generation") for x in ("--disable", f)],
            "--skip-git-repo-check", "-s", "workspace-write", "--json", "--",
            "Read TASK.md in this folder and do the task."]
    if agy:
        home, prof = agy_setup(f"{cfg}__{name}", ws)
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "LANG": "en_US.UTF-8"}
        argv = ["sandbox-exec", "-f", str(prof), str(pathlib.Path.home() / ".local/bin/agy"), "-p",
                "Read TASK.md in this folder and do the task.", "--model", f"{MODELS[a.model]}-{a.effort}", "--effort", a.effort,
                "--dangerously-skip-permissions", "--disable-slash-commands", "--output-format", "stream-json",
                "--print-timeout", f"{int(a.minutes * 60) + 120}s", "--settings", json.dumps({"permissions": {"deny": AGY_DENY}})]
    if cur:
        # a fresh HOME (no Cursor rules, skills or MCP servers; the Library link keeps the keychain sign-in), the same
        # macOS sandbox profile as agy, Cursor's own sandbox off (it cannot nest), web tools denied, commands audited
        home, prof = agy_setup(f"{cfg}__{name}", ws, links=[])
        (home / ".cursor").mkdir(exist_ok=True)
        (home / ".cursor/cli-config.json").write_text(json.dumps({"version": 1, "permissions": {
            "allow": ["Shell(*)", "Read(**)", "Write(**)"], "deny": ["WebFetch(*)", "WebSearch(*)", "Mcp(*:*)"]}}))
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(home / "tmp"), "TMPPREFIX": str(home / "tmp/zsh"), "LANG": "en_US.UTF-8"}
        argv = ["sandbox-exec", "-f", str(prof), str(pathlib.Path.home() / ".local/bin/cursor-agent"), "-p",
                "--model", f"{MODELS[a.model]}-{a.effort}", "--output-format", "stream-json", "--force", "--trust",
                "--sandbox", "disabled", "--workspace", str(ws), "Read TASK.md in this folder and do the task."]
    t0, started = time.time(), time.strftime("%Y-%m-%dT%H:%M:%S%z")
    threading.Thread(target=serve, daemon=True).start()
    def pump(proc):
        # copy the CLI's event stream to disk, stamping each event with the time it arrived
        with open(out / "transcript.jsonl", "wb") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                t = round(time.time() - t0, 1)
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if sx:
                    k, x = events(d, t)
                    if k == "command":
                        state["commands"] += 1
                        state["timeline"].append({"t": t, "kind": "command_execution", "step": state["commands"], "command": x["command"][:240], "tool": x["tool"]})
                    elif k == "error":
                        state["errors"].append(x["text"])
                    continue
                item = d.get("item") or {}
                kind = d.get("type", "")
                if kind in ("error", "turn.failed"):
                    state["errors"].append(json.dumps(d)[:600])
                if kind == "item.completed":
                    ev = {"t": t, "kind": item.get("type")}
                    if item.get("type") == "command_execution":
                        state["commands"] += 1
                        ev.update(step=state["commands"], command=str(item.get("command", ""))[:240], exit=item.get("exit_code"))
                    elif item.get("type") == "agent_message":
                        ev["text"] = str(item.get("text", ""))[:400]
                    state["timeline"].append(ev)

    proc = subprocess.Popen(argv, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    (out / "codex.pid").write_text(str(proc.pid))
    pumper = threading.Thread(target=pump, args=(proc,), daemon=True)
    pumper.start()
    ended = "timeout"
    while time.time() - t0 < a.minutes * 60 and not state.get("fault"):
        try:
            proc.wait(2)
            ended = "finished"
            break
        except subprocess.TimeoutExpired:
            pass
    reap(proc)
    pumper.join(10)
    time.sleep(1)
    state["stop"] = True

    usage, final, commands, outside = {}, "", 0, []
    for line in (out / "transcript.jsonl").read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if sx:
            k, x = events(d, 0)
            if k == "done":
                usage, final = x["usage"], x["final"]
            elif k == "command":
                commands += 1
                c = x["command"].replace(str(ws), ".").replace(str(home), "$HOME")
                if any(w in c for w in ("/Users/", "~/", "redstone-bench", "redstonepedia", "rsb-work", "rsb-agy", "/tmp/", "/var/folders") + NETWORK_WORDS):
                    outside.append(c[:300])
            continue
        item = d.get("item") or {}
        if d.get("type") == "turn.completed":
            usage = d.get("usage") or {}
        if d.get("type") == "item.completed" and item.get("type") == "agent_message":
            final = item.get("text", "")
        if d.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands += 1
            c = str(item.get("command", "")).replace(str(ws), ".")
            if any(x in c for x in ("/Users/", "~/", ".codex", "redstone-bench", "redstonepedia", "/tmp/", "/var/folders")):
                outside.append(c[:300])
    (out / "timeline.json").write_text(json.dumps(state["timeline"]))
    if sx:
        agy_drop(home)
    if agy and "blocked by Gemini's filters" in final:     # the provider cut the run off; not a model result
        state["fault"] = "provider safety filter ended the run"
    if shutil.disk_usage(out).free < 1 << 30:
        state["fault"] = state.get("fault") or "disk nearly full during the run"
    if state.get("fault"):
        state["errors"].append("harness fault: " + state["fault"])
    if state.get("fault") or (ended == "finished" and not usage):
        # the CLI stopped without completing a turn: quota, auth or a crash. Not a model result; the batch retries it.
        (out / "error.json").write_text(json.dumps({"returncode": proc.returncode, "errors": state["errors"][-5:],
                                                    "seconds": round(time.time() - t0), "verifier_calls": state["n"]}, indent=1))
        print("HARNESS ERROR:", state["errors"][-1:] or proc.returncode)
        raise SystemExit(3)
    # keep what the agent wrote (its notes and scripts), not the files it was handed
    kept = out / "workspace"
    kept.mkdir(exist_ok=True)
    for f in sorted(ws.iterdir()):
        if f.name in given or f.name.startswith(".") or not f.is_file() or f.is_symlink() or f.stat().st_size > 2_000_000:
            continue
        shutil.copy(f, kept / f.name)
    log = state["log"]
    valid = [x for x in log if x["valid"]]
    best = state["best"]
    result = {"task": name, "family": task.get("family", "door"), "model": MODELS[a.model], "effort": a.effort, "ended": ended,
              "started": started, "seconds": round(time.time() - t0), "minutes_cap": a.minutes,
              "verifier_calls": min(state["n"], a.budget), "valid_calls": len(valid),
              "budget": a.budget, "best": best, "reference": task.get("reference"),
              "seconds_to_first_valid": valid[0]["t"] if valid else None,
              "seconds_to_best": next((x["t"] for x in log if best and x["attempt"] == best["attempt"]), None),
              "seed": seed and {k: seed[k] for k in ("volume", "dims", "tiebreak")},
              "beat_seed": bool(seed and best and key(best) < key(seed)),
              "attempt_log": log, "usage": usage, "shell_commands": commands, "cli_errors": state["errors"][-5:], "commands_touching_outside": outside,
              "final_message": final[-2000:],
              "harness": {"cli": "agy " + sh(str(pathlib.Path.home() / ".local/bin/agy"), "--version") if agy else
                          "cursor-agent " + sh(str(pathlib.Path.home() / ".local/bin/cursor-agent"), "--version") if cur else sh("codex", "--version"), "git": sh("git", "rev-parse", "--short", "HEAD"),
                          "git_dirty": bool(sh("git", "status", "--porcelain", "bench", "tasks")), "minecraft": "26.3 vanilla"}}
    if best:
        shutil.copy(out / "attempts" / f"{best['attempt']:03d}.json", out / "best.json")
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("task", "ended", "seconds", "verifier_calls", "valid_calls", "best")}, indent=1))


if __name__ == "__main__":
    main()
