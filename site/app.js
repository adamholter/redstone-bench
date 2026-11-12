// Redstone Bench site: leaderboard, task gallery, and the run explorer.
(async function () {
  const STATE_ORDER = ["closed", "extended", "rest", "open", "retracted"];
  const STATE_LABEL = { open: "Open", closed: "Closed", extended: "Extended", retracted: "Retracted", rest: "At rest" };
  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) { if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v); }
    kids.flat().forEach((c) => c !== null && c !== undefined && c !== false && n.append(c.nodeType ? c : document.createTextNode(c)));
    return n;
  };
  const svg = (tag, attrs = {}, ...kids) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) { if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v); }
    kids.flat().forEach((c) => c && n.append(c.nodeType ? c : document.createTextNode(c)));
    return n;
  };
  const mins = (s) => (s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);
  const big = (n) => (n == null ? "" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${n}`);
  const cache = {};
  const load = (path) => (cache[path] ||= fetch(`data/builds/${path}.json`).then((r) => r.json()));
  const sortStates = (ss) => [...ss].sort((x, y) => STATE_ORDER.indexOf(x) - STATE_ORDER.indexOf(y));

  const d = await fetch("data/index.json").then((r) => r.json());
  const task = Object.fromEntries(d.tasks.map((t) => [t.id, t]));
  const cfgName = (c) => `${c.model.replace("GPT-", "")} ${c.effort}`;
  const cfg = Object.fromEntries(d.configs.map((c) => [c.id, c]));
  const mkey = (c) => c.id.split("-")[0];
  const short = (c) => { const w = c.model.split(" "); return /^[\d.]+$/.test(w.at(-1)) ? w[0] : w.at(-1); };   // "GPT-6 Astra" -> Astra, "Grok 4.7" -> Grok
  const models = [...new Set(d.configs.map(mkey))];
  const modelName = Object.fromEntries(d.configs.map((c) => [mkey(c), short(c)]));
  // one colour and one shape per model, so colour is never the only cue
  const MK = { sol: ["#2a78d6", "circle"], terra: ["#1baf7a", "square"], astra: ["#e34948", "tri"], luna: ["#eda100", "diamond"], carbon: ["#b0308f", "pent"], grok: ["#3a3ab8", "hex"] };
  const glyph = (k, x, y, r, extra = {}) => {
    const [fill, sh] = MK[k] || ["#8f9da8", "circle"], a = { fill, stroke: "#fff", "stroke-width": 1.5, ...extra };
    if (sh === "circle") return svg("circle", { cx: x, cy: y, r, ...a });
    if (sh === "square") return svg("rect", { x: x - r * 0.9, y: y - r * 0.9, width: r * 1.8, height: r * 1.8, rx: 1.5, ...a });
    if (sh === "hex") return svg("path", { d: [0, 1, 2, 3, 4, 5].map((i) => `${i ? "L" : "M"}${x + r * 1.15 * Math.cos((i * Math.PI) / 3)},${y + r * 1.15 * Math.sin((i * Math.PI) / 3)}`).join("") + "Z", ...a });
    if (sh === "pent") return svg("path", { d: [0, 1, 2, 3, 4].map((i) => `${i ? "L" : "M"}${x + r * 1.2 * Math.sin((i * 2 * Math.PI) / 5)},${y - r * 1.2 * Math.cos((i * 2 * Math.PI) / 5)}`).join("") + "Z", ...a });
    if (sh === "tri") return svg("path", { d: `M${x},${y - r * 1.15}L${x + r * 1.1},${y + r * 0.85}H${x - r * 1.1}Z`, ...a });
    return svg("path", { d: `M${x},${y - r * 1.25}L${x + r * 1.25},${y}L${x},${y + r * 1.25}L${x - r * 1.25},${y}Z`, ...a });
  };
  const sw = (k) => { const g = svg("svg", { viewBox: "0 0 14 14", width: 14, height: 14, class: "sw", "aria-hidden": "true" }); g.append(glyph(k, 7, 7, 5, { stroke: "none" })); return g; };
  const segmented = (box, items, on, pick) => { box.replaceChildren(...items.map(([k, label]) => el("button", { "aria-pressed": k === on, onclick: () => pick(k) }, label))); };
  const runOf = (tid, cid) => d.runs.find((r) => r.task === tid && r.config === cid);
  const runsFor = (tid) => d.configs.map((c) => runOf(tid, c.id)).filter(Boolean);
  const othersFor = (tid) => d.others.filter((o) => o.task === tid);
  // smallest build anyone has made, per task
  const floor = {};
  d.tasks.forEach((t) => { const v = [t.known?.[0], ...runsFor(t.id).map((r) => r.volume)].filter(Boolean); floor[t.id] = v.length ? Math.min(...v) : null; });
  const bestRun = (tid) => runsFor(tid).filter((r) => r.volume).sort((a, b) => a.volume - b.volume || a.ticks - b.ticks || a.seconds - b.seconds)[0];
  const thumbOf = (tid) => { const r = bestRun(tid); if (r) return `renders/${r.id}.png`; const o = othersFor(tid)[0]; return o ? `renders/${o.id}.png` : null; };

  // ---------- hero
  const solved = d.tasks.filter((t) => bestRun(t.id)).length;
  $("lede").prepend(`${d.tasks.length} building tasks, ${solved} solved by a model so far. `);
  const heroB = d.others.find((o) => o.id === "seed__door_3x3") || d.others[0];
  if (heroB) {
    load(heroB.id).then((b) => RSB.viewer($("hero"), b.states.closed || Object.values(b.states)[0], { spin: true, pitch: 0.35, bounds: b.bounds }));
    $("herocap").append(el("b", {}, task[heroB.task].title), `, volume ${heroB.volume}. Drag to turn it.`);
  }

  // ---------- leaderboard
  const rows = d.configs.map((c) => {
    const rs = d.tasks.map((t) => runOf(t.id, c.id));
    const ran = rs.filter(Boolean), ok = ran.filter((r) => r.volume);
    const score = d.tasks.reduce((s, t, i) => s + (rs[i]?.volume ? floor[t.id] / rs[i].volume : 0), 0) / d.tasks.length;
    return { c, ran: ran.length, solved: ok.length, score, seconds: ran.reduce((s, r) => s + r.seconds, 0), tries: ran.reduce((s, r) => s + r.calls, 0), out: ran.reduce((s, r) => s + (r.tokens_out || 0), 0), cut: ran.filter((r) => r.tokens_out == null).length };
  }).sort((a, b) => b.score - a.score);
  const full = (r) => r.ran === d.tasks.length;
  function drawLB(all) {
    segmented($("lbseg"), [[false, "Best effort per model"], [true, "All efforts"]], all, drawLB);
    const shown = all ? rows : models.map((k) => { const mine = rows.filter((r) => mkey(r.c) === k); return mine.find(full) || mine[0]; }).sort((a, b) => b.score - a.score);
    $("lb").replaceChildren(el("tr", {}, ["", "Model", "Size score", "Solved", "Time per task", "Tries per task", "Output tokens"].map((h) => el("th", {}, h))),
      ...shown.map((r, i) => el("tr", { class: full(r) ? "" : "partial" },
        el("td", {}, i + 1),
        el("td", { class: "who" }, el("b", {}, sw(mkey(r.c)), r.c.model, full(r) ? null : el("span", { class: "tag" }, `${r.ran} of ${d.tasks.length} run`)), el("small", {}, `${r.c.effort} effort`)),
        el("td", {}, el("div", { class: "score" }, el("b", {}, Math.round(r.score * 100)), el("div", { class: "bar" }, el("i", { style: `width:${r.score * 100}%` })))),
        el("td", {}, `${r.solved} / ${d.tasks.length}`),
        el("td", {}, r.ran ? mins(r.seconds / r.ran) : ""), el("td", {}, r.ran ? (r.tries / r.ran).toFixed(1) : ""), el("td", { title: r.cut ? `${r.cut} runs hit the time cap and reported no token count` : "" }, big(r.out) + (r.cut ? "+" : "")))));
  }
  drawLB(false);

  // ---------- score against time: every finished setting, and the line nothing beats
  function pareto() {
    const box = $("pareto"), pts = rows.filter(full).map((r) => ({ r, x: r.seconds / r.ran / 60, y: r.score * 100 }));
    box.replaceChildren();
    if (pts.length < 2) { $("paretowrap").hidden = true; return; }
    const W = Math.max(320, box.clientWidth), H = Math.min(420, Math.max(280, W * 0.42)), m = { l: 40, r: 24, t: 24, b: 50 };
    const x0 = Math.min(...pts.map((p) => p.x)) / 1.25, x1 = Math.max(...pts.map((p) => p.x)) * 1.15, y1 = Math.min(100, Math.ceil((Math.max(...pts.map((p) => p.y)) + 6) / 10) * 10);
    const sx = (x) => m.l + (Math.log(x / x0) / Math.log(x1 / x0)) * (W - m.l - m.r), sy = (y) => m.t + (1 - y / y1) * (H - m.t - m.b);
    const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Size score against minutes per task for every model and effort" });
    for (let t = 0; t <= y1; t += y1 > 60 ? 20 : 10) g.append(svg("line", { x1: m.l, x2: W - m.r, y1: sy(t), y2: sy(t), stroke: "#e3ebf0" }), svg("text", { x: m.l - 8, y: sy(t) + 4, "text-anchor": "end" }, t));
    [0.5, 1, 2, 5, 10, 20, 50].filter((t) => t >= x0 && t <= x1).forEach((t) => g.append(svg("text", { x: sx(t), y: H - 30, "text-anchor": "middle" }, t)));
    g.append(svg("text", { x: (m.l + W - m.r) / 2, y: H - 8, "text-anchor": "middle" }, "minutes per task (log scale)"), svg("text", { x: 4, y: 12 }, "size score"));
    let bestY = -1; const front = [...pts].sort((a, b) => a.x - b.x || b.y - a.y).filter((p) => (p.y > bestY ? ((bestY = p.y), true) : false));
    front.forEach((p) => (p.front = true));
    g.append(svg("path", { d: front.map((p, i) => (i ? `H${sx(p.x)}V${sy(p.y)}` : `M${sx(p.x)},${sy(p.y)}`)).join("") + `H${W - m.r}`, fill: "none", stroke: "#16222c", "stroke-width": 2, "stroke-linejoin": "round" }));
    const tip = el("div", { class: "tip" });
    const placed = [];
    pts.forEach((p) => placed.push([sx(p.x) - 8, sy(p.y) - 8, sx(p.x) + 8, sy(p.y) + 8]));
    pts.sort((a, b) => !!b.front - !!a.front || b.y - a.y).forEach((p) => {
      const k = mkey(p.r.c), X = sx(p.x), Y = sy(p.y);
      g.append(glyph(k, X, Y, p.front ? 7 : 5, p.front ? {} : { opacity: 0.55 }));
      {   // effort label beside the point (the shape names the model); if every spot is taken the hover has it
        const text = p.r.c.effort, w = text.length * 7.2 + 4;
        const spots = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([dx, dy]) => ({ x: X + dx * 9, y: dy < 0 ? Y - 9 : Y + 19, a: dx < 0 ? "end" : "start",
          box: [dx < 0 ? X - 9 - w : X + 9, dy < 0 ? Y - 22 : Y + 6, dx < 0 ? X - 9 : X + 9 + w, dy < 0 ? Y - 5 : Y + 23] }));
        const free = spots.find((q) => q.box[0] > 2 && q.box[2] < W - 2 && q.box[3] < H - m.b + 4 && !placed.some((o) => q.box[0] < o[2] && q.box[2] > o[0] && q.box[1] < o[3] && q.box[3] > o[1]));
        if (free) { placed.push(free.box); g.append(svg("text", { class: p.front ? "lab" : "", x: free.x, y: free.y, "text-anchor": free.a }, text)); }
      }
      g.append(svg("circle", { cx: X, cy: Y, r: 13, fill: "transparent",
        onmouseenter: () => { tip.textContent = `${short(p.r.c)} ${p.r.c.effort} · score ${Math.round(p.y)} · ${p.x.toFixed(1)} min per task · ${p.r.solved} of ${d.tasks.length} solved`; tip.style.left = `${Math.min(Math.max((X / W) * 100, 18), 82)}%`; tip.style.top = `${Y}px`; tip.style.display = "block"; },
        onmouseleave: () => (tip.style.display = "none") }));
    });
    box.append(g, tip);
    $("plegend").replaceChildren(...models.filter((k) => pts.some((p) => mkey(p.r.c) === k)).map((k) => el("span", {}, sw(k), modelName[k])), el("span", { class: "key" }, el("i", {}), "nothing scores higher in less time"), el("span", { class: "faded" }, el("i", {}), "beaten by a faster setting"));
  }
  pareto();
  let pw = 0; new ResizeObserver(() => { const w = $("pareto").clientWidth; if (w !== pw) { pw = w; pareto(); } }).observe($("pareto"));

  // ---------- task tiles
  for (const [fid, fname] of Object.entries(d.families)) {
    const ts = d.tasks.filter((t) => t.family === fid);
    $("fams").append(el("div", { class: "fam" }, el("h3", {}, fname), el("div", { class: "grid" }, ts.map((t) => {
      const r = bestRun(t.id), th = thumbOf(t.id);
      return el("button", { class: "tile", onclick: () => go(t.id) },
        el("div", { class: "pic" }, th ? el("img", { src: th, alt: "", loading: "lazy" }) : el("span", { class: "none" }, "No working build yet")),
        el("div", { class: "txt" }, el("b", {}, t.title), el("small", {}, t.blurb),
          el("div", { class: "score" }, r ? [el("strong", {}, r.volume), `blocks · ${cfgName(cfg[r.config])}`] : "No model has solved it")));
    }))));
  }

  // ---------- score matrix: the best of each model, or one model's efforts side by side
  function drawBoard(view) {
    segmented($("bseg"), [["best", "Best of each model"], ...models.map((k) => [k, modelName[k]])], view, drawBoard);
    const cols = view === "best" ? models.map((k) => ({ head: modelName[k], pick: (t) => runsFor(t.id).filter((r) => mkey(cfg[r.config]) === k) }))
      : d.configs.filter((c) => mkey(c) === view).map((c) => ({ head: c.effort, pick: (t) => [runOf(t.id, c.id)].filter(Boolean) }));
    $("board").replaceChildren(el("tr", {}, el("th", {}, "Task"), cols.map((c) => el("th", {}, c.head)), el("th", {}, "Best other")));
    d.tasks.forEach((t) => {
      const cells = cols.map((c) => { const rs = c.pick(t); return { rs, r: rs.filter((r) => r.volume).sort((a, b) => a.volume - b.volume || a.ticks - b.ticks)[0] }; });
      const min = Math.min(...cells.map((c) => c.r?.volume).filter(Boolean));
      $("board").append(el("tr", {}, el("td", {}, el("a", { href: `#${t.id}` }, t.title)),
        cells.map(({ rs, r }) => {
          if (!rs.length) return el("td", { class: "none" }, "");
          if (!r) return el("td", { class: "none" }, rs.some((x) => x.calls) ? "failed" : "gave up");
          return el("td", { class: r.volume === min ? "win" : "" }, el("button", { title: cfgName(cfg[r.config]), onclick: () => go(t.id, r.config) }, r.volume)); }),
        el("td", { title: t.known ? t.known[1] : "" }, t.known ? t.known[0] : "")));
    });
  }
  drawBoard("best");

  // ---------- explorer
  const dlg = $("dlg");
  let v = null, cur = { tid: null, sel: null, version: null, mode: "time", model: null }, anim = null, token = 0;
  dlg.querySelector(".x").onclick = () => dlg.close();
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener("close", () => { stop(); if (location.hash.length > 1) history.replaceState(null, "", location.pathname + location.search + "#tasks"); });
  $("xmode").querySelectorAll("button").forEach((b) => (b.onclick = () => { cur.mode = b.dataset.m; $("xmode").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b)); chart(); }));
  function go(tid, cid) { location.hash = cid ? `${tid}/${cid}` : tid; }
  addEventListener("hashchange", route);
  function route() {
    const [tid, cid] = location.hash.slice(1).split("/");
    if (!task[tid]) { if (dlg.open) dlg.close(); return; }
    openTask(tid, cid);
  }

  function openTask(tid, cid) {
    const t = task[tid];
    cur.tid = tid;
    $("xt").textContent = t.title; $("xb").textContent = t.blurb;
    if (!dlg.open) dlg.showModal();
    const r = (cid && runOf(tid, cid)) || bestRun(tid) || runsFor(tid)[0];
    const o = !r?.volume && !cid ? othersFor(tid)[0] : null;
    if (r) cur.model = mkey(cfg[r.config]);
    select(o ? { other: o } : r ? { run: r } : null);
  }

  function select(sel, version) {
    cur.sel = sel;
    const tid = cur.tid, t = task[tid];
    // model picker, then that model's runs
    const mine = (k) => runsFor(tid).filter((r) => mkey(cfg[r.config]) === k);
    const have = models.filter((k) => mine(k).length);
    cur.model = have.includes(cur.model) ? cur.model : have[0];
    $("xmodel").hidden = have.length < 2;
    segmented($("xmodel"), have.map((k) => [k, modelName[k]]), cur.model, (k) => {
      const rs = mine(k), b = rs.filter((r) => r.volume).sort((a, b) => a.volume - b.volume || a.ticks - b.ticks)[0] || rs[0];
      go(tid, b.config);
    });
    $("runs").replaceChildren(
      ...mine(cur.model).map((r) => el("li", {}, el("button", { "aria-pressed": sel?.run === r, onclick: () => go(tid, r.config) },
        el("span", { class: "dot" }), el("span", { class: "n" }, `${modelName[cur.model]} ${r.effort}`),
        el("span", { class: "m" }, r.calls ? `${r.valid_calls} of ${r.calls} tries worked · ${mins(r.seconds)}` : `gave up after ${mins(r.seconds)}`),
        el("span", { class: "v" }, r.volume ?? "—")))),
      ...othersFor(tid).map((o) => el("li", { class: "other" }, el("button", { "aria-pressed": sel?.other === o, onclick: () => select({ other: o }) },
        el("span", { class: "dot" }), el("span", { class: "n" }, o.source), el("span", { class: "m" }, "not a model run"), el("span", { class: "v" }, o.volume)))),
      ...(t.known && t.known[1] === "human record" ? [el("li", { class: "other" }, el("button", { disabled: true, style: "cursor:default" },
        el("span", { class: "dot" }), el("span", { class: "n" }, "Human record"), el("span", { class: "m" }, "no file to show"), el("span", { class: "v" }, t.known[0])))] : []));
    // facts
    const r = sel?.run;
    $("facts").replaceChildren(...(r ? [
      el("div", { class: "facts" },
        el("div", {}, el("b", {}, mins(r.seconds)), el("span", {}, r.ended === "timeout" ? "hit the time cap" : "time spent")),
        el("div", {}, el("b", {}, `${r.calls}`), el("span", {}, `of ${r.budget} tries used`)),
        el("div", {}, el("b", {}, `${r.commands ?? ""}`), el("span", {}, "shell steps")),
        el("div", {}, el("b", {}, r.tokens_out == null ? "n/a" : big(r.tokens_out)), el("span", {}, r.tokens_out == null ? "tokens not reported when cut off" : "output tokens"))),
      r.message ? el("details", {}, el("summary", {}, "The model's last message"), el("p", {}, r.message)) : null] : []));
    chart();
    if (sel?.run) show(sel.run, version ?? sel.run.best); else if (sel?.other) show(null, null, sel.other); else show(null, null, null);
  }

  // ---------- 3D view and animation
  function expand(fr) {   // cumulative frames; long still stretches are shortened so the action stays watchable
    const out = [{ map: fr.first, tick: 0 }]; let map = fr.first, tick = 0;
    for (const s of fr.steps) {
      if (s.n) { const keep = Math.min(s.n, 8); for (let i = 1; i <= keep; i++) out.push({ map, tick: tick + i }); tick += s.n; continue; }
      map = { ...map };
      for (const [k, st] of Object.entries(s.c)) { if (st === null) delete map[k]; else map[k] = st; }
      tick += 1; out.push({ map, tick, event: s.e });
    }
    return out;
  }
  function stop() { if (anim) { clearTimeout(anim.timer); anim.playing = false; } setPlay(); }
  function setPlay() { $("play").textContent = anim?.playing ? "Pause" : anim && anim.i > 0 && anim.i >= anim.end ? "Replay" : "Play"; }
  function frame(i) {
    const f = anim.frames[i]; anim.i = i;
    v.set(f.map, anim.bounds);
    $("scrub").value = i; $("tick").textContent = `tick ${f.tick}`;
    if (f.event) { $("toast").textContent = f.event; $("toast").classList.add("on"); clearTimeout(anim.tt); anim.tt = setTimeout(() => $("toast").classList.remove("on"), 1200); }
  }
  function play(from, to) {
    if (!anim) return;
    clearTimeout(anim.timer);
    anim.end = to ?? anim.frames.length - 1; anim.playing = true; frame(from ?? 0); setPlay();
    const step = () => {
      if (!anim.playing) return;
      if (anim.i >= anim.end) { anim.playing = false; setPlay(); return; }
      frame(anim.i + 1);
      anim.timer = setTimeout(step, 50 / +$("speed").value);
    };
    anim.timer = setTimeout(step, 50 / +$("speed").value);
  }
  $("play").onclick = () => { if (!anim) return; if (anim.playing) stop(); else play(anim.i >= anim.frames.length - 1 || anim.i >= anim.end ? 0 : anim.i); };
  $("scrub").oninput = () => { if (anim) { stop(); frame(+$("scrub").value); } };

  async function show(run, n, other) {
    const my = ++token;
    stop(); anim = null;
    cur.version = n;
    const has = (run && n) || other;
    $("empty").style.display = has ? "none" : "grid"; $("dv").style.visibility = has ? "" : "hidden";
    $("seg").replaceChildren(); $("player").hidden = true; $("cap").replaceChildren();
    if (!has) { if (run) $("cap").append(el("b", {}, cfgName(cfg[run.config])), run.calls ? " never got a working build." : " stopped without trying a build."); return; }
    const b = await load(other ? other.id : `${run.id}/${n}`);
    if (my !== token) return;
    const ss = sortStates(Object.keys(b.states));
    const draw = (blocks) => (v ? v.set(blocks, b.bounds) : (v = RSB.viewer($("dv"), blocks, { wheel: true, bounds: b.bounds })));
    $("cap").append(el("b", {}, other ? other.source : cfgName(cfg[run.config])),
      other ? `, volume ${b.volume}` : `, try ${n}${n === run.best ? " (its best)" : ""}: volume ${b.volume}, ${b.dims.join(" × ")}, ${b.ticks} ticks`);
    if (b.frames) {
      anim = { frames: expand(b.frames), bounds: b.bounds, i: 0, end: 0, playing: false };
      $("player").hidden = false; $("scrub").max = anim.frames.length - 1;
      const ev = (name) => anim.frames.findIndex((f) => f.event === name);
      const on = ev("lever on"), off = ev("lever off");
      if (ss.length > 1 && on >= 0 && off > on) {   // a door or extender: the two buttons play the real move
        const seg = { [ss[0]]: [Math.max(0, on - 1), off - 1], [ss[1]]: [off - 1, anim.frames.length - 1] };
        ss.forEach((s) => $("seg").append(el("button", { "data-s": s, onclick: () => { mark(s); play(...seg[s]); } }, STATE_LABEL[s])));
        const mark = (s) => $("seg").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x.dataset.s === s));
        mark(ss[1]); await draw(anim.frames[0].map); frame(0);
      } else { await draw(anim.frames[0].map); frame(0); }
      setPlay();
    } else {
      const pick = (s) => { $("seg").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x.dataset.s === s)); draw(b.states[s]); };
      if (ss.length > 1) ss.forEach((s) => $("seg").append(el("button", { "data-s": s, onclick: () => pick(s) }, STATE_LABEL[s])));
      pick(ss[0]);
    }
    chart();
  }

  // ---------- chart: smallest working build so far, for every run of this task
  function chart() {
    const box = $("chart"), tid = cur.tid;
    if (!tid) return;
    const W = Math.max(320, box.clientWidth), H = 230, m = { l: 46, r: 92, t: 26, b: 30 };
    const X = { time: (a, r) => (a ? a[0] : r.seconds) / 60, tries: (a, r, i) => (a ? i + 1 : r.calls), steps: (a, r) => (a ? a[1] ?? 0 : r.commands ?? 0) }[cur.mode];
    const series = runsFor(tid).filter((r) => mkey(cfg[r.config]) === cur.model).map((r) => {
      const pts = []; let best = Infinity;
      r.log.forEach((a, i) => { if (a[2]) { best = Math.min(best, a[2]); pts.push({ x: X(a, r, i), y: best, own: a[2], n: i + 1, t: a[0], better: a[2] === best }); } });
      const fails = r.log.map((a, i) => (a[2] ? null : { x: X(a, r, i), n: i + 1, why: a[3] })).filter(Boolean);
      return { r, pts, fails, end: Math.max(X(null, r), pts.at(-1)?.x ?? 0, fails.at(-1)?.x ?? 0) };
    });
    const all = series.flatMap((s) => s.pts.map((p) => p.y));
    box.replaceChildren();
    if (!all.length) { box.append(el("p", { class: "chartnote" }, "No model has a working build for this task yet, so there is nothing to plot.")); box.style.height = "auto"; return; }
    box.style.height = "";
    const xmax = Math.max(...series.map((s) => s.end), 1) * 1.02;
    const lo = Math.min(...all, floor[tid] ?? Infinity), hi = Math.max(...all);
    const log = hi / lo > 12;
    const y0 = log ? lo / 1.25 : 0, y1 = log ? hi * 1.25 : hi * 1.1;
    const sx = (x) => m.l + (x / xmax) * (W - m.l - m.r);
    const sy = (y) => (log ? m.t + (1 - Math.log(y / y0) / Math.log(y1 / y0)) * (H - m.t - m.b) : m.t + (1 - (y - y0) / (y1 - y0)) * (H - m.t - m.b));
    const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Smallest working build over the run" });
    const ticks = log ? [1, 2, 3, 5, 7].flatMap((k) => [1, 10, 100, 1000].map((p) => k * p)).sort((a, b) => a - b).filter((t) => t >= y0 && t <= y1).filter((t, i, a) => a.length <= 6 || [1, 2, 5].includes(+String(t)[0])) : niceTicks(y1, 4);
    ticks.forEach((t) => g.append(svg("line", { x1: m.l, x2: W - m.r, y1: sy(t), y2: sy(t), stroke: "#e3ebf0" }), svg("text", { x: m.l - 8, y: sy(t) + 4, "text-anchor": "end" }, t)));
    niceTicks(xmax, 5).forEach((t) => g.append(svg("text", { x: sx(t), y: H - 8, "text-anchor": "middle" }, t)));
    g.append(svg("text", { x: W - m.r + 10, y: H - 8 }, { time: "min", tries: "tries", steps: "steps" }[cur.mode]));
    g.append(svg("text", { x: 4, y: 12 }, "volume (blocks)"));
    const path = (s) => { let dd = ""; s.pts.forEach((p, i) => { dd += i ? `H${sx(p.x)}V${sy(p.y)}` : `M${sx(p.x)},${sy(p.y)}`; }); return dd + `H${sx(s.end)}`; };
    const selRun = cur.sel?.run;
    series.filter((s) => s.pts.length && s.r !== selRun).forEach((s) => {
      g.append(svg("path", { d: path(s), fill: "none", stroke: "#8f9da8", "stroke-width": 1.5, "stroke-linejoin": "round" }),
        svg("path", { class: "ctx", d: path(s), fill: "none", stroke: "transparent", "stroke-width": 12, onclick: () => go(tid, s.r.config) }, svg("title", {}, `${cfgName(cfg[s.r.config])}: ${s.r.volume}`)));
    });
    const tip = el("div", { class: "tip" }); box.append(g, tip);
    const s = series.find((x) => x.r === selRun);
    if (s) {
      const hint = (x, y, text) => { tip.textContent = text; tip.style.left = `${(x / W) * 100}%`; tip.style.top = `${y}px`; tip.style.display = "block"; };
      s.fails.forEach((f) => g.append(svg("path", { d: `M${sx(f.x) - 3},${H - m.b - 7}l6,6m0,-6l-6,6`, stroke: "#8f9da8", "stroke-width": 1.5 }),
        svg("rect", { x: sx(f.x) - 6, y: H - m.b - 12, width: 12, height: 14, fill: "transparent", onmouseenter: () => hint(sx(f.x), H - m.b - 10, `Try ${f.n} failed`), onmouseleave: () => (tip.style.display = "none") })));
      if (s.pts.length) {
        g.append(svg("path", { d: path(s), fill: "none", stroke: "#2f8436", "stroke-width": 2.5, "stroke-linejoin": "round" }));
        s.pts.forEach((p) => {
          const on = cur.version === p.n;
          g.append(svg("circle", { cx: sx(p.x), cy: sy(p.own), r: on ? 7 : 4.5, fill: on ? "#16222c" : "#2f8436", stroke: "#fff", "stroke-width": 2 }),
            svg("circle", { cx: sx(p.x), cy: sy(p.own), r: 12, fill: "transparent", style: "cursor:pointer",
              onmouseenter: () => hint(sx(p.x), sy(p.own), `Try ${p.n} · ${mins(p.t)} · volume ${p.own}`), onmouseleave: () => (tip.style.display = "none"), onclick: () => show(s.r, p.n) }));
        });
      }
    }
    // one label per line at its right end: effort and final volume, nudged apart so they never overlap
    const labs = series.filter((x) => x.pts.length).map((x) => ({ x, y: sy(x.pts.at(-1).y) + 4, on: x.r === selRun })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < labs.length; i++) labs[i].y = Math.max(labs[i].y, labs[i - 1].y + 13);
    labs.forEach((l) => g.append(svg("text", { class: l.on ? "lab" : "", x: W - m.r + 10, y: l.y }, `${l.x.r.effort} ${l.x.pts.at(-1).y}`)));
    if (s && s.fails.length) box.append(el("p", { class: "chartnote" }, "× a try that failed (selected run)"));
    if (floor[tid] && task[tid].known && task[tid].known[0] === floor[tid]) {
      g.append(svg("line", { x1: m.l, x2: W - m.r, y1: sy(floor[tid]), y2: sy(floor[tid]), stroke: "#16222c", "stroke-dasharray": "2 4" }),
        svg("text", { x: m.l + 4, y: sy(floor[tid]) - 5 }, `${task[tid].known[1]}: ${floor[tid]}`));
    }
  }
  function niceTicks(max, n) { const raw = max / n, p = 10 ** Math.floor(Math.log10(raw)), st = [1, 2, 5, 10].map((k) => k * p).find((k) => k >= raw); const out = []; for (let t = st; t <= max * 1.0001; t += st) out.push(+t.toFixed(6)); return out; }
  let lastW = 0;
  new ResizeObserver(() => { const w = $("chart").clientWidth; if (dlg.open && w !== lastW) { lastW = w; chart(); } }).observe($("chart"));
  route();
})();
