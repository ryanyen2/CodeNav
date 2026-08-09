# 2026-08-09-002 — Reading both artifacts as a programmer, and what that changed

First real side-by-side on `psf/requests`: a generated `CLAUDE.md` against a
`codoc init` tree. Written up because the useful findings were not about prompt
wording. Three were outright defects that made the codoc artifact worse than
useless, and the most interesting one was a whole class of context the pipeline
never derived despite already holding the data.

## §1 Which artifact teaches the codebase better, and why

**As it stood: `CLAUDE.md`, overwhelmingly** — though the run was not a fair test
of the method (bootstrap was capped at 2 of 19 files; see §2). Setting the
defects aside and comparing what each artifact is *shaped* to hold, the honest
account is that they answer different questions, and one of those questions is
the one a newcomer actually asks first.

What the baseline carried that a correct codoc tree still would not have:

1. **A traversal.** Its strongest passage follows one request through four
   modules in the order it meets them — `api.py` → `sessions.py` →
   `adapters.py` → back to `sessions.py` for redirects. This is the single
   highest-value paragraph in the document. It is not a summary of any file; it
   is a path *across* files.
2. **Operational knowledge.** How to install, test, lint, typecheck; that the
   src layout means tests will not import without an editable install; that no
   network is needed because `pytest-httpbin` runs locally. None of this is in
   the code's structure at all.
3. **Invariants stated as prohibitions.** "Backwards-compat shims to leave
   intact: `compat.py`, `packages.py`." "Do not add `Co-Authored-By` trailers —
   upstream rejects them." This is exactly N3 material: things that are wrong to
   change, which no amount of reading the code reveals.

What codoc has that the baseline structurally cannot: per-feature granularity
with bindings that click through to code, reverse navigation from a symbol back
to its feature, and the property of being maintained rather than written once
and left to rot. Those are real and they are the reason to build codoc at all.

But the comparison exposed the honest asymmetry: **codoc produced an inventory
where a programmer needed a map.** A feature tree derived file-by-file and then
grouped by coupling counts is a taxonomy. It answers "what exists and what sits
near what". A newcomer's first question is "what happens when I call this", and
a taxonomy cannot answer it no matter how good each individual description is.

## §2 The three defects (fixed, commit 007dd01)

All found in one workspace, all failing the same way — by producing something
that looked like an answer.

1. **The tree-update pass got 246 unbound chunks in one prompt and returned one
   op.** "Place every chunk in `added`" stops being followable somewhere well
   under a hundred. Now batched by file, ≤25 per call, later batches seeing what
   earlier ones proposed.
2. **The coverage net then attached 245 of them to a single feature**, which
   ended up owning cookies, models, sessions and adapters while being titled
   "Package safety and metadata". Following a graph edge is good evidence for a
   handful of chunks and none for a hundred. Now budgeted per feature per pass;
   rejects become proposals rather than vanishing.
3. **The remaining 66 were proposed as nodes named after their symbols** —
   `HTTPDigestAuth.handle_401`, `__module__`, `CONTENT_TYPE_MULTI_PART` — with
   empty descriptions. That is the symbol index with extra steps. Now one
   proposal per file; a lone orphan keeps its symbol name.

Plus the reported blank document pane: `tree.doc.json` was written only by a
mutating Loop B pass or a file-change render, so a freshly-initialized workspace
showed a full outline of titles beside an empty page. Seeded at init and at
daemon startup.

## §3 The finding worth generalizing (commit 39ab699)

`graph.query.entry_points` was written, exported, unit-tested — and called from
nowhere in the pipeline. The call graph existed the whole time and was only ever
asked **local** questions: which symbols sit near this one (`neighbor_feature`),
how strongly do two features couple (`_feature_coupling`), what is in this
change's neighbourhood (`ego_graph`). Local answers compose into a taxonomy.
Nobody asked the global question, so nothing in any prompt's context described a
path, and no prompt however well written can produce a traversal from an
inventory.

`codoc/loop/surface.py` asks it. From every public symbol nothing internal
calls, walk inward, at each step taking the callee that itself calls the most —
the branch that keeps going, rather than whichever validation helper sorts
first. Rank by modules crossed rather than length (a seven-call chain inside one
class is that class's internals); deduplicate on the module sequence
(`get`/`post`/`put`/`delete` are one story with different first words). On
`requests` this recovers the lifecycle, proxy resolution and redirect handling.
It feeds the organization pass, which now asks for themes naming a **stage of
the work** rather than a **kind of thing**.

**The general lesson: when an artifact is weak, ask what question the pipeline
never asked its own data — before rewriting the instruction that consumes the
answer.** Prompt quality is bounded by context, context is bounded by what gets
derived, and the derivation step is invisible because nothing errors when a
question simply is not asked.

## §4 Still missing, in priority order

1. **Operational knowledge.** Build/test/lint commands, and gotchas like "the
   src layout requires an editable install". Derivable from `Makefile`,
   `pyproject.toml`, `tox.ini`, `.github/workflows`, `CONTRIBUTING.md`. Nothing
   in codoc reads any of them, and no feature tree over source files ever will.
   Probably belongs as a distinguished root node rather than smuggled into a
   feature description.
2. **Prohibitions.** "Leave this shim intact." The description contract now has
   a slot for this (the "what must hold" element, 2026-08-09-001 §2) but nothing
   *derives* candidates. Signals available: symbols exported but never called
   internally, modules whose only content is re-exports, `DeprecationWarning`
   sites, comments matching `don't`/`must not`/`do not remove`.
3. **The package's own prose.** `README`, module docstrings, and `__init__.py`
   are the highest-signal statements of intent a library has, and bootstrap sees
   them only as more chunks of source in file order.
4. **Fair re-run.** The eval's `--max-files` cap makes the arms incomparable —
   the baseline read the whole package while codoc described two files. Either
   drop the cap or record the artifact as partial and refuse to score it.
   `arms.build_codoc` already records the cap; `run.py` should act on it.
