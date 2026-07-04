/* ============================================================================
   codoc editor — prototype interaction layer
   Mock data + renderers + the signature animation drivers. No build step;
   classic script so it runs from file://. Everything is reduced-motion aware
   (the CSS @media gate freezes keyframes; JS just shortens to instant).
   ============================================================================ */
(function () {
  "use strict";

  const RM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ICONS = window.CODOC_ICONS || {};
  const AGENT = window.CODOC_AGENT || { workers: [], avatars: [] };
  const $ = (s, r) => (r || document).querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const wait = (ms) => new Promise((r) => setTimeout(r, RM ? 0 : ms));
  function icon(name, cls) {
    return `<svg class="icon-svg ${cls || ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  }

  // ── Mock model ───────────────────────────────────────────────────────────
  const COLLAB = [
    { id: "james", name: "James",     color: "var(--p-violet)", initials: "JM", kind: "human" },
    { id: "mara",  name: "Mara",      color: "var(--p-amber)",  initials: "MK", kind: "human" },
    { id: "agent", name: "Bug Fixer", color: "var(--p-teal)",   initials: "",   kind: "agent" },
  ];

  // The feature tree / document. `state`: idle | ghost (agent will resolve it).
  const TREE = [
    { id: "f-root", depth: 0, title: "Notifications", state: "idle" },
    { id: "f-fanout", depth: 1, title: "Fan-out & delivery", state: "idle" },
    { id: "f-dedupe", depth: 1, title: "Duplicate suppression", state: "ghost" },
    { id: "f-enqueue", depth: 2, title: "Email enqueue", state: "idle" },
    { id: "f-prefs", depth: 1, title: "Delivery preferences", state: "idle" },
  ];

  // Feature dependency graph (Flowistry-style flow): per feature, what it
  // depends on (up), what depends on it (down), and the code symbols it binds.
  const TITLE = Object.fromEntries(TREE.map((f) => [f.id, f.title]));
  const DEPS = {
    "f-fanout":  { up: [], down: ["f-dedupe", "f-enqueue"], code: ["fanout.py#dispatch", "models.py#NotificationRow"] },
    "f-dedupe":  { up: ["f-fanout"], down: ["f-enqueue"], code: ["migrations/0042_dedupe", "models.py#source_event_id"] },
    "f-enqueue": { up: ["f-dedupe", "f-fanout"], down: [], code: ["workers.py#enqueue_email"] },
    "f-prefs":   { up: [], down: ["f-fanout"], code: ["prefs.py#channel_for"] },
  };

  // ── Build the nav ──────────────────────────────────────────────────────
  function buildNav() {
    const nav = $("#nav");
    nav.appendChild(el("div", "nav-title", "Feature tree"));
    TREE.forEach((f) => {
      if (f.depth === 0) return; // root is the doc H1
      const row = el("div", "row");
      row.dataset.depth = f.depth;
      row.dataset.target = f.id;
      const dotCls = f.state === "ghost" ? "editing" : f.state === "idle" ? "idle" : "done";
      row.innerHTML =
        `${icon("chevron", "chev")}<span class="sdot ${dotCls}"></span><span class="lbl">${f.title}</span>`;
      row.addEventListener("click", () => scrollToFeature(f.id));
      nav.appendChild(row);
    });
  }

  // ── Build the document ───────────────────────────────────────────────────
  function cite(label, file) { return `<span class="cite" data-file="${file}">${icon("peek")}${label}</span>`; }

  function buildDoc() {
    const doc = $("#doc");
    doc.appendChild(el("h1", null, `Notifications <span class="sdot done" style="width:11px;height:11px"></span>`));
    doc.appendChild(el("p", "subtitle", "How a single event becomes at-most-one notification per recipient, per channel."));
    doc.appendChild(el("div", "meta-line",
      `<span>5 features</span><span>·</span><span>3 here now</span><span>·</span><span>synced to <code style="font-family:var(--font-mono)">main</code></span>`));

    // f-fanout — resolved/idle
    doc.appendChild(featureSection("f-fanout", 2, "Fan-out & delivery", [
      `When an event lands, ${cite("dispatch()", "fanout.py#dispatch")} fans it out to every subscribed recipient and writes one ${cite("notification_row", "models.py#NotificationRow")} per (recipient, channel).`,
      `Delivery is async — rows are picked up by ${cite("send_worker", "workers.py#send_worker")}, which is where retries live.`,
    ]));

    // f-dedupe — GHOST (the agent resolves this live)
    const dedupe = featureSection("f-dedupe", 2, "Duplicate suppression", [
      `Fan-out job retries were not idempotent: on a retry we re-inserted notification rows and re-enqueued the email, so a single mention produced duplicate notifications.`,
      `The fix persists ${cite("source_event_id", "models.py#NotificationRow.source_event_id")} (= the event id), adds a unique index on ${cite("(recipient_id, source_event_id, type)", "migrations/0042_dedupe.py")}, inserts with ON CONFLICT DO NOTHING, and only enqueues email for newly-inserted rows.`,
    ], { ghost: true });
    dedupe.insertAdjacentHTML("beforeend",
      `<blockquote>keep the migration backwards-compatible — backfill source_event_id before adding the index.</blockquote>`);
    dedupe.insertAdjacentHTML("beforeend",
      `<p style="font-size:14px;color:var(--body)">Reference: <a class="consult" href="#">${icon("link")}idempotent-fanout RFC</a></p>`);
    doc.appendChild(dedupe);

    // f-enqueue — peek demo
    doc.appendChild(featureSection("f-enqueue", 3, "Email enqueue", [
      `Only newly-inserted rows enqueue an email. Expand ${cite("enqueue_email()", "workers.py#enqueue_email")} to see the guard.`,
    ]));

    // f-prefs — suggesting-mode inline diff
    const prefs = featureSection("f-prefs", 2, "Delivery preferences", [
      `Each recipient chooses channels per notification type. <span class="del">A muted type still writes a row but</span> <span class="ins">A muted type is skipped at fan-out so</span> no row is written and <span class="rew">nothing</span> is enqueued.`,
    ]);
    prefs.querySelector("h2").insertAdjacentHTML("beforeend", `<span class="suggest-tag">${"Mara suggests"}</span>`);
    doc.appendChild(prefs);
  }

  function featureSection(id, level, title, paras, opts) {
    opts = opts || {};
    const s = el("section", "feature" + (opts.ghost ? " ghost" : ""));
    s.id = id;
    const h = el("h" + level, null, `${title} <span class="fid">⟨${id}⟩</span>`);
    s.appendChild(h);
    paras.forEach((p) => s.appendChild(el("p", null, p)));
    return s;
  }

  // ── Dependency-flow graph (Flowistry-inspired) ───────────────────────────
  // Renders the FOCUSED feature's neighbourhood: what it depends on (up, blue),
  // what depends on it (down, green), and the code symbols it binds. Re-laid out
  // with an animated edge draw-in whenever the focus changes.
  const SVGNS = "http://www.w3.org/2000/svg";
  let sliceOn = false;
  let flowFocus = null;

  function setupSlice() {
    const t = $("#slice-toggle");
    t.innerHTML = `${icon("peek")}<span>Slice</span>`;
    t.addEventListener("click", () => {
      sliceOn = !sliceOn;
      t.classList.toggle("on", sliceOn);
      applySlice();
    });
  }

  function neighbourhood(id) {
    const d = DEPS[id] || { up: [], down: [], code: [] };
    return new Set([id, ...d.up, ...d.down]);
  }

  function applySlice() {
    const doc = $("#doc");
    doc.classList.toggle("sliced", sliceOn);
    if (!sliceOn) { document.querySelectorAll("section.feature").forEach((s) => s.classList.remove("in-slice")); return; }
    const near = neighbourhood(flowFocus);
    document.querySelectorAll("section.feature").forEach((s) => s.classList.toggle("in-slice", near.has(s.id)));
  }

  function svgEl(name, attrs) {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function renderFlow(id) {
    if (!DEPS[id]) return;
    flowFocus = id;
    const canvas = $("#flow-canvas");
    const svg = $("#flow-svg");
    $("#flow-title").innerHTML = `${TITLE[id]} <small>flow</small>`;
    // clear
    canvas.querySelectorAll(".fnode").forEach((n) => n.remove());
    svg.innerHTML =
      `<defs>
        <marker id="ar-up" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="flow-arrow" d="M1 1 L7 4 L1 7 Z" fill="var(--ce-editing)"/></marker>
        <marker id="ar-down" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="flow-arrow" d="M1 1 L7 4 L1 7 Z" fill="var(--ce-staged)"/></marker>
        <marker id="ar-n" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M1 1 L7 4 L1 7 Z" fill="var(--hairline-2)"/></marker>
      </defs>`;

    const W = canvas.clientWidth, H = canvas.clientHeight;
    const d = DEPS[id];
    const rowY = { up: H * 0.16, focus: H * 0.44, down: H * 0.70, code: H * 0.90 };
    const place = (ids, y) => ids.map((nid, i) => ({ id: nid, x: W * ((i + 1) / (ids.length + 1)), y }));

    const focusPt = { id, x: W / 2, y: rowY.focus };
    const upPts = place(d.up, rowY.up);
    const downPts = place(d.down, rowY.down);
    const codePts = place(d.code, rowY.code);
    const pos = {};
    [focusPt, ...upPts, ...downPts, ...codePts].forEach((p) => (pos[p.id] = p));

    // edges first (under nodes)
    const edges = [];
    upPts.forEach((p) => edges.push(addEdge(svg, p, focusPt, "up", "url(#ar-up)")));     // focus depends on up
    downPts.forEach((p) => edges.push(addEdge(svg, focusPt, p, "down", "url(#ar-down)"))); // down depends on focus
    codePts.forEach((p) => edges.push(addEdge(svg, focusPt, p, "code", "url(#ar-n)")));

    // nodes
    const edgeByNode = {};
    edges.forEach((e) => { edgeByNode[e.other] = e.path; });
    addFlowNode(canvas, focusPt, TITLE[id], "focus", null);
    upPts.forEach((p, i) => addFlowNode(canvas, p, TITLE[p.id], "feature", "depends on", edgeByNode[p.id], i));
    downPts.forEach((p, i) => addFlowNode(canvas, p, TITLE[p.id], "feature", "used by", edgeByNode[p.id], i));
    codePts.forEach((p, i) => addFlowNode(canvas, p, p.id.split("/").pop(), "code", null, edgeByNode[p.id], i));

    if (sliceOn) applySlice();
  }

  function addEdge(svg, a, b, cls, marker) {
    const my = (a.y + b.y) / 2;
    const path = svgEl("path", {
      class: "flow-edge " + cls,
      d: `M ${a.x} ${a.y} C ${a.x} ${my}, ${b.x} ${my}, ${b.x} ${b.y}`,
      "marker-end": marker,
    });
    svg.appendChild(path);
    // animated draw-in
    if (!RM) {
      const len = path.getTotalLength();
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.style.transition = "stroke-dashoffset 520ms var(--ease-out)";
      requestAnimationFrame(() => { path.style.strokeDashoffset = "0"; });
    }
    return { path, other: a.id === flowFocus ? b.id : a.id };
  }

  function addFlowNode(canvas, pt, label, kind, role, edgePath, idx) {
    const n = el("div", "fnode " + kind);
    n.style.left = pt.x + "px";
    n.style.top = pt.y + "px";
    if (!RM && idx != null) n.style.animationDelay = 80 + idx * 70 + "ms";
    const glyph = kind === "code" ? icon("peek") : icon("node");
    n.innerHTML = `${glyph}<span>${label}</span>` + (role ? `<span class="role">${role}</span>` : "");
    if (pt.id.startsWith && pt.id.startsWith("f-") && kind !== "focus") {
      n.addEventListener("click", () => scrollToFeature(pt.id));
    }
    if (edgePath) {
      n.addEventListener("mouseenter", () => { edgePath.classList.add("hot"); document.getElementById(pt.id)?.classList.add("flash"); });
      n.addEventListener("mouseleave", () => { edgePath.classList.remove("hot"); document.getElementById(pt.id)?.classList.remove("flash"); });
    }
    canvas.appendChild(n);
  }

  // ── Presence layer (Figma-style cursors) ─────────────────────────────────
  function buildPresence() {
    const layer = $("#presence");
    const cluster = $("#presence-cluster");
    COLLAB.forEach((c, idx) => {
      // top-bar avatar
      const pa = el("div", "pa" + (c.kind === "agent" ? " agent" : ""));
      pa.style.background = c.color;
      pa.title = c.name;
      pa.innerHTML = c.kind === "agent" ? `<span style="color:#fff;width:16px;height:16px;display:grid;place-items:center">${AGENT.avatars[2] || ""}</span>` : c.initials;
      cluster.appendChild(pa);

      // floating cursor
      const cur = el("div", "cursor" + (c.kind === "agent" ? " agent" : ""));
      cur.style.setProperty("--c", c.color);
      cur.dataset.id = c.id;
      const flag = c.kind === "agent"
        ? `<span class="flag"><span class="w16">${AGENT.workers[0] || ""}</span>${c.name}</span>`
        : `<span class="flag">${c.name}</span>`;
      cur.innerHTML = `<div class="caret"></div>${flag}`;
      cur.style.top = (120 + idx * 90) + "px";
      cur.style.left = (360 + idx * 40) + "px";
      layer.appendChild(cur);
    });
    if (!RM) driftPresence();
  }

  function driftPresence() {
    const wrap = $("#doc-wrap");
    setInterval(() => {
      document.querySelectorAll(".cursor").forEach((cur) => {
        if (cur.dataset.id === "agent") return; // agent cursor is pinned by the driver
        const w = wrap.clientWidth, h = wrap.clientHeight;
        cur.style.top = (60 + Math.random() * (h - 160)) + "px";
        cur.style.left = (260 + Math.random() * Math.min(440, w - 320)) + "px";
      });
    }, 3200);
  }

  function moveAgentCursorTo(target) {
    const cur = document.querySelector('.cursor[data-id="agent"]');
    const wrap = $("#doc-wrap");
    if (!cur || !target) return;
    const t = target.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    cur.style.top = (t.top - w.top + 8) + "px";
    cur.style.left = Math.min(t.left - w.left + 12, w.width - 220) + "px";
  }

  // ── Codebase peek-through ─────────────────────────────────────────────────
  const SNIPPETS = {
    "workers.py#enqueue_email":
      `<span class="cm"># only newly-inserted rows enqueue — the dedupe guard</span>\n` +
      `<span class="kw">def</span> <span class="fn">enqueue_email</span>(rows):\n` +
      `    <span class="hl">    fresh = [r <span class="kw">for</span> r <span class="kw">in</span> rows <span class="kw">if</span> r.inserted]</span>\n` +
      `    <span class="kw">for</span> r <span class="kw">in</span> fresh:\n` +
      `        email_queue.put(<span class="str">"notify"</span>, r.id)`,
  };
  function wirePeek() {
    document.querySelectorAll(".cite").forEach((c) => {
      c.addEventListener("click", () => togglePeek(c));
    });
  }
  function togglePeek(citeEl) {
    const file = citeEl.dataset.file;
    let next = citeEl.closest("p").nextElementSibling;
    if (next && next.classList.contains("peek")) { next.classList.toggle("open"); return; }
    const code = SNIPPETS[file] || `<span class="cm"># ${file}</span>\n<span class="kw">def</span> <span class="fn">${(file.split("#")[1] || "symbol")}</span>(...):\n    ...`;
    const peek = el("div", "peek");
    peek.innerHTML =
      `<div class="peek-bar"><span class="lights"><span class="light r"></span><span class="light y"></span><span class="light g"></span></span><span class="path">${file.replace("#", "  ›  ")}</span></div><pre>${code}</pre>`;
    citeEl.closest("p").after(peek);
    requestAnimationFrame(() => peek.classList.add("open"));
  }

  // ── Active-section tracking (nav + minimap) ───────────────────────────────
  function wireScrollSpy() {
    const sections = Array.from(document.querySelectorAll("section.feature"));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) setActive(e.target.id); });
    }, { root: $("#doc-wrap"), rootMargin: "-20% 0px -65% 0px", threshold: 0 });
    sections.forEach((s) => io.observe(s));
  }
  function setActive(id) {
    document.querySelectorAll(".row").forEach((r) => r.classList.toggle("active", r.dataset.target === id));
    if (DEPS[id] && id !== flowFocus) renderFlow(id);
  }
  function scrollToFeature(id) {
    const s = document.getElementById(id);
    if (s) s.scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
    setActive(id);
  }

  // ── THE signature sequence: ghost → agent works → resolved word-by-word ───
  function splitWords(p) {
    if (p.dataset.split) return;          // already wrapped
    p.dataset.html = p.innerHTML;          // keep original (with inline <span> citations)
    // wrap bare text nodes' words; leave element nodes (cites) intact as one unit
    const frag = document.createDocumentFragment();
    Array.from(p.childNodes).forEach((node) => {
      if (node.nodeType === 3) {
        node.textContent.split(/(\s+)/).forEach((tok) => {
          if (/^\s+$/.test(tok)) { frag.appendChild(document.createTextNode(tok)); }
          else if (tok) { const w = el("span", "w pending", tok); frag.appendChild(w); }
        });
      } else {
        node.classList && node.classList.add("w", "pending");
        frag.appendChild(node);
      }
    });
    p.innerHTML = ""; p.appendChild(frag); p.dataset.split = "1";
  }

  async function runAgentSequence() {
    const sec = $("#f-dedupe");
    if (!sec) return;
    resetSequence(sec);
    const agent = COLLAB[2];

    // 1) agent arrives — move its cursor to the section, build the ribbon
    moveAgentCursorTo(sec);
    const ribbon = el("div", "ribbon");
    ribbon.style.setProperty("--agent-color", agent.color);
    ribbon.innerHTML =
      `<div class="r-head"><span class="avatar">${AGENT.avatars[0] || ""}</span>` +
      `<span>codoc · <span class="agent-name">${agent.name}</span></span>` +
      `<span class="working">${AGENT.workers[2] || icon("working")}</span></div>` +
      `<div class="steps"></div>`;
    sec.querySelector("h2").after(ribbon);
    const stepsBox = ribbon.querySelector(".steps");
    await wait(400);

    // 2) step-by-step plan (each previous step ticks done as the next arrives)
    const steps = [
      "reading fanout.py · workers.py · models.py",
      "root cause — fan-out retries re-insert + re-enqueue",
      "writing migration 0042_dedupe + ON CONFLICT DO NOTHING",
      "guarding enqueue_email() to fresh rows only",
      "opening PR #48217 → main",
    ];
    let prev = null;
    for (const text of steps) {
      if (prev) { prev.classList.remove("active"); prev.classList.add("done"); prev.querySelector(".tick").innerHTML = ICONS.check; }
      const step = el("div", "step active", `${icon("working", "tick")}<span>${text}</span>`);
      stepsBox.appendChild(step);
      prev = step;
      await wait(820);
    }
    if (prev) { prev.classList.remove("active"); prev.classList.add("done"); prev.querySelector(".tick").innerHTML = ICONS.check; }
    await wait(300);

    // 3) resolve the prose word-by-word, mute → ink, with the L→R sweep
    sec.classList.remove("ghost");
    const paras = Array.from(sec.querySelectorAll("p")).filter((p) => !p.querySelector(".consult"));
    for (const p of paras) {
      splitWords(p);
      p.classList.add("resolving-sweep");
      const words = Array.from(p.querySelectorAll(".w"));
      for (const w of words) {
        w.classList.remove("pending");
        w.classList.add("revealing");
        await wait(26);
      }
      setTimeout(() => p.classList.remove("resolving-sweep"), 1100);
      await wait(120);
    }

    // 4) settle — working → done check, spark on the heading, collapse the steps
    const head = ribbon.querySelector(".r-head");
    head.querySelector(".working").outerHTML = `<span class="done-check">${icon("check")}</span>`;
    const spark = el("span", null, icon("spark"));
    spark.style.color = "var(--ce-staged)";
    spark.firstChild.style.animation = RM ? "" : "word-rise .5s var(--ease-out)";
    sec.querySelector("h2 .fid").after(spark);
    setActive("f-dedupe");
    // flip the nav status dot editing → done
    const navdot = document.querySelector('.row[data-target="f-dedupe"] .sdot');
    if (navdot) { navdot.classList.remove("editing"); navdot.classList.add("done"); }

    await wait(700);
    // collapse the play-by-play into a single resolved summary
    const allSteps = Array.from(stepsBox.querySelectorAll(".step"));
    allSteps.slice(0, -1).forEach((s, i) => { s.style.animationDelay = (i * 40) + "ms"; s.classList.add("collapsing"); });
    await wait(360);
    allSteps.slice(0, -1).forEach((s) => s.remove());
    const summary = stepsBox.querySelector(".step");
    if (summary) summary.innerHTML = `<span class="tick" style="color:var(--ce-staged)">${icon("check")}</span><span>PR #48217 opened · ready to squash & merge</span>`;
  }

  function resetSequence(sec) {
    sec.querySelectorAll(".ribbon").forEach((r) => r.remove());
    sec.querySelectorAll("h2 svg.icon-svg").forEach((s) => { if (s.closest(".fid")) return; });
    // restore ghost prose
    sec.classList.add("ghost");
    sec.querySelectorAll("p[data-html]").forEach((p) => { p.innerHTML = p.dataset.html; delete p.dataset.split; });
    const navdot = document.querySelector('.row[data-target="f-dedupe"] .sdot');
    if (navdot) { navdot.classList.remove("done"); navdot.classList.add("editing"); }
    // remove a prior spark
    const fid = sec.querySelector("h2 .fid");
    if (fid && fid.nextElementSibling && fid.nextElementSibling.tagName === "SPAN") fid.nextElementSibling.remove();
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  function boot() {
    const bm = $("#brand-mark");
    if (bm) bm.innerHTML = (AGENT.avatars[1] || "");
    buildNav();
    buildDoc();
    buildPresence();
    wirePeek();
    setupSlice();
    wireScrollSpy();
    setActive("f-fanout");
    renderFlow("f-fanout");

    $("#replay").addEventListener("click", runAgentSequence);
    let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => flowFocus && renderFlow(flowFocus), 120); });

    // auto-play the signature sequence once, shortly after load
    setTimeout(runAgentSequence, 1100);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
