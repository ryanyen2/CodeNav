"""Real-LLM end-to-end userflow report (non-deterministic — read it, don't assert it).

Bootstraps a tiny repo with the REAL cocoindex index + the REAL LLM, then walks a
sequence of code edits — add, modify, dependency-add, rename, delete — running the
real Loop A after each. Because the LLM's exact choice (attach vs. propose a node,
refresh vs. amend a description, retire vs. keep) is non-deterministic, this prints
a human-readable report of *where each change landed in the tree* so a person can
eyeball whether codoc reflected the change in the right position.

It also checks a handful of invariants that must hold no matter what the LLM
decides (nothing silently dropped, no duplicate titles, modify refreshes, delete
detaches). Exit code = number of invariant failures.

Run it directly to inspect the report::

    python -m tests.bdd.e2e_report

cocoindex's index is a per-process singleton, so this lives in its own module and
is invoked as a subprocess by ``test_e2e_userflows.py`` (keeping it isolated from
the other real-index test in the suite).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from codoc.loop.bootstrap import run_init
from codoc.loop.loop_a import run_loop_a
from codoc.pipelines.indexing.reader import read_all_chunks
from codoc.store.db import Store, open_store

AUTH = '''\
def create_session(user):
    """Create a new session token for a user."""
    return f"token-{user}"


def login(username, password):
    """Authenticate a user and return a session."""
    if password == "secret":
        return create_session(username)
    return None
'''

MATH = '''\
def add(a, b):
    """Add two numbers."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b
'''


# ── reporting helpers ────────────────────────────────────────────────────────
def _path(store: Store, fid: str | None) -> str:
    """The 'Parent > Child' path of a feature, root-first."""
    names: list[str] = []
    seen: set[str] = set()
    while fid and fid not in seen:
        seen.add(fid)
        f = store.get_feature(fid)
        if not f:
            break
        names.append(f.title)
        fid = f.parent_id
    return " > ".join(reversed(names)) or "(root)"


def placement(store: Store, file: str, symbol: str) -> str:
    """Where did this symbol end up — bound, proposed, or dropped?"""
    b = store.binding_at(file, symbol)
    if b:
        return f"BOUND   → {_path(store, b.feature_id)}"
    for e in store.pending_events():
        if (file, symbol) in e.op.bindings:
            return f"PROPOSED→ {e.op.kind.value} \"{e.op.title or ''}\"".rstrip()
    return "‼ UNPLACED (attribution dropped!)"


def tree_report(store: Store) -> str:
    lines: list[str] = []

    def walk(parent_id: str | None, depth: int) -> None:
        for f in store.children(parent_id):
            n = len(store.bindings_for_feature(f.id))
            tag = "" if f.realized else "  [plan]"
            lines.append("    " * depth + f"- {f.title}  ({n} binding{'' if n == 1 else 's'}){tag}")
            walk(f.id, depth + 1)

    walk(None, 0)
    return "\n".join(lines) or "(empty tree)"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n{'═' * 70}\n {title}\n{'═' * 70}")

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(f"{name}: {detail}")


def _no_dup_titles(store: Store) -> tuple[bool, str]:
    titles = [f.title for f in store.list_features()]
    dups = {t for t in titles if titles.count(t) > 1}
    return (not dups), (f"duplicate titles: {sorted(dups)}" if dups else "all unique")


def _coverage(store: Store, codoc_dir: str) -> tuple[bool, str]:
    """Every indexed chunk is bound or has a pending proposal — nothing dropped."""
    chunks = read_all_chunks(codoc_dir)
    claimed = {(f, s) for e in store.pending_events() for (f, s) in e.op.bindings}
    dropped = [
        c.symbol_path for c in chunks
        if store.binding_at(c.file, c.symbol_path) is None and (c.file, c.symbol_path) not in claimed
    ]
    return (not dropped), (f"{len(chunks)} chunks, {len(dropped)} unaccounted: {dropped[:5]}"
                           if dropped else f"all {len(chunks)} chunks accounted for")


# ── the scenario walk ────────────────────────────────────────────────────────
def run(workdir: Path) -> int:
    rep = Report()
    root_p = workdir / "repo"
    root_p.mkdir()
    (root_p / "auth.py").write_text(AUTH)
    (root_p / "math_utils.py").write_text(MATH)
    root = str(root_p)
    codoc_dir = str(root_p / ".codoc")

    # ── BOOTSTRAP ────────────────────────────────────────────────────────────
    rep.section("BOOTSTRAP — real index + real LLM")
    res = run_init(root)
    print(f"  {res.summary()}\n")
    store = open_store(codoc_dir)
    print("  Initial feature tree:")
    print("\n".join("  " + ln for ln in tree_report(store).splitlines()))
    ok, detail = _coverage(store, codoc_dir); rep.check("bootstrap covers every chunk", ok, detail)
    ok, detail = _no_dup_titles(store); rep.check("no duplicate feature titles", ok, detail)
    rep.check("auth.login bound", store.binding_at("auth.py", "auth.py::login") is not None)
    store.close()

    # ── ADD a brand-new file (new responsibility) ────────────────────────────
    rep.section("ADD — a new payments.py module")
    (root_p / "payments.py").write_text(
        'def charge(card, amount):\n    """Charge a credit card."""\n    return {"ok": True}\n')
    ra = run_loop_a(root, codoc_dir, file_scope={"payments.py"})
    print(f"  Loop A: {ra.summary()}")
    store = open_store(codoc_dir)
    print(f"  charge() placement:  {placement(store, 'payments.py', 'payments.py::charge')}")
    ok, d = _coverage(store, codoc_dir); rep.check("new code is bound or proposed (not dropped)", ok, d)
    ok, d = _no_dup_titles(store); rep.check("still no duplicate titles", ok, d)
    store.close()

    # ── ADD a dependent function (calls an existing symbol) ──────────────────
    rep.section("ADD (dependency) — a function that calls auth.login")
    (root_p / "auth.py").write_text(
        AUTH + '\n\ndef require_login(username, password):\n'
               '    """Gate access behind a login."""\n'
               '    return login(username, password) is not None\n')
    ra = run_loop_a(root, codoc_dir, file_scope={"auth.py"})
    print(f"  Loop A: {ra.summary()}")
    store = open_store(codoc_dir)
    print(f"  require_login() placement:  {placement(store, 'auth.py', 'auth.py::require_login')}")
    print("  (expected near the Authentication feature it depends on)")
    ok, d = _coverage(store, codoc_dir); rep.check("dependent code is bound or proposed", ok, d)
    store.close()

    # ── MODIFY a bound function body ─────────────────────────────────────────
    rep.section("MODIFY — change auth.login's body")
    store = open_store(codoc_dir)
    before = store.binding_at("auth.py", "auth.py::login")
    before_fp = before.fingerprint if before else None
    before_owner = _path(store, before.feature_id) if before else "(unbound)"
    store.close()
    (root_p / "auth.py").write_text(
        (root_p / "auth.py").read_text().replace('"secret"', '"hunter2"'))
    ra = run_loop_a(root, codoc_dir, file_scope={"auth.py"})
    print(f"  Loop A: {ra.summary()}")
    store = open_store(codoc_dir)
    after = store.binding_at("auth.py", "auth.py::login")
    after_owner = _path(store, after.feature_id) if after else "(unbound)"
    print(f"  login() owner: {before_owner!r} → {after_owner!r}")
    rep.check("modified function stays bound", after is not None)
    rep.check("fingerprint refreshed", bool(after and after.fingerprint != before_fp),
              f"{before_fp} → {after.fingerprint if after else None}")
    store.close()

    # ── RENAME a function in place ───────────────────────────────────────────
    rep.section("RENAME — math_utils.subtract → difference (same body)")
    store = open_store(codoc_dir)
    sub_owner = _path(store, b.feature_id) if (b := store.binding_at("math_utils.py", "math_utils.py::subtract")) else "(unbound)"
    store.close()
    (root_p / "math_utils.py").write_text(MATH.replace("def subtract", "def difference"))
    ra = run_loop_a(root, codoc_dir, file_scope={"math_utils.py"})
    print(f"  Loop A: {ra.summary()}")
    store = open_store(codoc_dir)
    print(f"  subtract() was under: {sub_owner!r}")
    print(f"  difference() placement: {placement(store, 'math_utils.py', 'math_utils.py::difference')}")
    rep.check("old symbol detached", store.binding_at("math_utils.py", "math_utils.py::subtract") is None)
    ok, d = _coverage(store, codoc_dir); rep.check("renamed code not dropped", ok, d)
    ok, d = _no_dup_titles(store); rep.check("rename created no duplicate node", ok, d)
    store.close()

    # ── DELETE a file ────────────────────────────────────────────────────────
    rep.section("DELETE — remove math_utils.py")
    (root_p / "math_utils.py").unlink()
    ra = run_loop_a(root, codoc_dir, file_scope={"math_utils.py"})
    print(f"  Loop A: {ra.summary()}")
    store = open_store(codoc_dir)
    rep.check("deleted code's binding detached",
              store.binding_at("math_utils.py", "math_utils.py::add") is None)
    print("\n  Final feature tree:")
    print("\n".join("  " + ln for ln in tree_report(store).splitlines()))
    store.close()

    # ── verdict ──────────────────────────────────────────────────────────────
    rep.section("INVARIANTS")
    if rep.failures:
        print(f"  INVARIANTS: {len(rep.failures)} FAILED")
        for f in rep.failures:
            print(f"    ✗ {f}")
    else:
        print("  INVARIANTS: ALL PASS")
    return len(rep.failures)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="codoc-e2e-"))
    try:
        return run(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
