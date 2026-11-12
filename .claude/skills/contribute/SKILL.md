---
name: contribute
description: Add a model, a harness or a task to Redstone Bench and submit results others can re-verify. Use when someone wants to benchmark a new model or CLI, add a task, or open a pull request with runs.
---

# Contributing to Redstone Bench

Every score here is checked by a real vanilla Minecraft server, never by a model. A contribution is only useful if
the maintainer can replay it and get the same numbers, so follow these steps in order.

## 1. Set up the judge

1. Java 21+ and Python 3.11+. No pip packages are needed for the bench itself.
2. Download the vanilla Minecraft Java **26.3** server jar from minecraft.net into `server/server.jar`
   (it is not in the repo; Mojang's EULA forbids redistributing it). Accept the EULA in `server/eula.txt`.
3. `cp server/server.properties.example server/server.properties`, pick an RCON password, and write the same
   value to `.rcon_password` at the repo root. Both files are gitignored. Never commit them.
4. Copy `datapack/rsb` into `server/world/datapacks/` (create the world first by starting the server once).
5. Start it: `sh serve.sh` (edit the java path in it if yours is not Homebrew's). It must listen on RCON
   127.0.0.1:25592.
6. Sanity check: `python3 bench/verify.py tasks/door_2x2.json examples/door_2x2_baseline.json` must pass.

## 2. Add your model or harness

Runs go through the vendor's own agent CLI, signed in on a normal subscription, so the model works the way users
actually use it. `bench/run.py` has four runners to copy from:

- **Codex** (`codex exec`): network off, workspace-write sandbox, plugins/apps/memories off.
- **Cursor** (`cursor-agent`): same sandbox recipe as Antigravity, Cursor's own sandbox off (it cannot nest),
  web and MCP tools denied in a fresh `cli-config.json`.
- **Antigravity** (`agy`): a fresh HOME per run, a `sandbox-exec` profile hiding the rest of the disk, a tool deny
  list for web/browser/MCP/sub-agents, and an audit of every shell command for network words.
- **Muse** (`muse exec`): a fresh HOME per run (sign-in copied in, `Library/Keychains` linked for the Keychain
  secret, home wiped per attempt), Muse's own sandbox with network restricted including loopback (a
  `sandbox-exec` wrapper cannot nest), web tools and reminder agents off. Weaker than the others in two stated
  ways: its tools can read outside the workspace (writes are confined), and bundled skills and sub-agents cannot
  be switched off; every tool call is therefore audited into `commands_touching_outside` (arguments where the
  stream carries them, output where it does not).

A new runner must:

- add the model id to `MODELS` (short key -> CLI model id) and the display name to `bench/export_site.py`;
- start the agent in the scratch workspace with the single prompt `Read TASK.md in this folder and do the task.`;
- give it **no network and no files outside the workspace** (sandbox it, deny the tools, audit the commands into
  `commands_touching_outside`), no memories, skills, plugins or MCP servers. That includes `/tmp` and
  `/private/var/folders`: other agents (including other runs of this bench) leave scripts there, so hide them and give
  the agent its own `TMPDIR`. Before submitting, grep every transcript for paths in `/tmp` that are not the run's own;
  a run that read another run's files is void;
- record token usage, the CLI version and the final message; map the CLI's event stream into the same
  `command` / `done` / `error` events `agy_events` produces, so the timeline and step counts work;
- treat provider failures (quota, auth, safety filters, crashes) as harness errors: write `error.json`, exit 3,
  so the batch retries them. These are never scored as model failures.
- kill the CLI's whole process group when the run ends.

Do not change `TASK.md` text, the budget (40 verifier calls), the time cap (60 minutes in the batch) or the
verifier for your model. If you think one of those is wrong, open an issue; a change reruns every model.

## 3. Run the full matrix for your settings

    RSB_CONFIGS=mymodel-high,mymodel-low RSB_BATCH_DIR=_batch_mymodel RSB_WORKERS=4 python3 bench/batch.py
    RSB_BATCH_DIR=_batch_mymodel python3 bench/batch.py --audit

- One config = one model at one reasoning effort. Run **all 16 tasks** for each config; the site only shows
  complete configs (`site-host/sync.sh` drops the rest).
- The batch is safe to kill and restart. Do not hand-edit anything under `runs/`.
- Runs thrown out for harness problems go to `runs/_void_<reason>/`; say why in the PR.

## 4. Export and check

    python3 bench/export_site.py          # replays every best build in your server into site/data
    (cd site && node render_all.mjs)      # thumbnails
    python3 -m http.server 7461 -d site   # look at your model in the leaderboard and explorer

`export_site.py` re-verifies each best build; any replay mismatch is a bug to fix before submitting.

## 5. Open the pull request

Include:

- the runner code and the `MODELS` / `export_site.py` entries;
- the exported `site/data` and `site/renders` changes for your configs only;
- a `.tar.gz` of `runs/<config>/` for each config (attach it to the PR or link it; it is too big for git). The
  maintainer replays every attempt with `bench/rescore.py` before merging;
- the CLI name and version, plan type, how the agent was sandboxed, and anything that went wrong.

Before pushing, check for secrets: `git diff --cached | grep -iE "password|token|api.?key|sk-|BEGIN .*KEY"` must
come back empty, and transcripts must not contain your home path's private files or credentials.

## Adding a task

Tasks are JSON in `tasks/`; circuits are generated by `bench/make_tasks.py`. A new task needs a hand-made build in
`baselines/` that passes `bench/verify.py`, so it is proven solvable, plus its size in `baselines/scores.json`.
Adding a task changes every model's score, so open an issue first.
