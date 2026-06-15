"""Generation-quality eval harness (Plan B, U5) — scriptable + gated for CI.

Scores a *generated* feature tree against a rubric and emits a structured report
plus invariant checks usable in CI. Modeled on :mod:`tests.bdd.e2e_report`
(bootstrap → checks → report → exit code), but split along two axes the plan
(KTD3) requires:

* **Deterministic invariant dimensions** — coverage (every chunk bound), non-
  duplication (no duplicate feature titles), hierarchy balance (no single
  junk-drawer node holding a huge fraction of the tree), and ref-validity (count
  unresolved ``codoc:`` refs from ``tree.index.json`` when present; skipped
  gracefully when absent). These GATE the exit code, so CI stays stable.
* **LLM-judged dimensions** — the ``codoc-ux-tester`` rubric (Layout / Verbosity
  / Duplicates / Binding-quality / Missing-coverage / Subtree). These are
  REPORT-ONLY: printed, never asserted, never part of the exit code, and run only
  when ``OPENAI_API_KEY`` is set.

Self-gating: the script checks ``get_llm_config().api_key`` itself. With no key it
skips the LLM dimensions AND any bootstrap step that needs the real LLM — it
evaluates a small deterministic fixture tree instead, so the deterministic
invariants still run and the script exits 0 on a clean fixture. (The
``pytest.mark.skipif`` in ``test_e2e_userflows.py`` only gates the pytest path;
``python -m tests.bdd.eval_report`` needs its own guard.)

Run it directly::

    python -m tests.bdd.eval_report          # exits 0 with no key on a clean fixture

Exit code = number of FAILED deterministic invariant dimensions (LLM dims never
contribute). Like ``e2e_report``, cocoindex's index is a per-process singleton, so
the real-LLM bootstrap path lives here and is invoked as a subprocess by the
gated pytest runner.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from codoc.agent.base import run_agent
from codoc.codoc_file.render import INDEX_FILENAME
from codoc.config import get_llm_config
from codoc.store.db import Store

# ── tuning constants (shared with the unit tests) ────────────────────────────
# Hierarchy balance: the largest single non-root parent may own at most this
# fraction of all features before it reads as a junk drawer. A flat bootstrap
# that dumps everything under one theme is the failure this catches; a value
# above this ratio fails the dimension. Only enforced once the tree is big
# enough that a fraction is meaningful (see HIERARCHY_MIN_FEATURES).
HIERARCHY_JUNK_DRAWER_RATIO = 0.6
HIERARCHY_MIN_FEATURES = 5


# ── scoring result types (pure data — unit-testable) ─────────────────────────
@dataclass
class Dimension:
    """One scored dimension of the rubric.

    ``gating`` dimensions feed the exit code; report-only (LLM-judged) ones do
    not. ``skipped`` dimensions (e.g. ref-validity with no registry, LLM dims
    with no key) never count as a failure.
    """
    name: str
    ok: bool
    detail: str
    gating: bool
    skipped: bool = False

    @property
    def failed(self) -> bool:
        return self.gating and not self.skipped and not self.ok


@dataclass
class EvalReport:
    dimensions: list[Dimension] = field(default_factory=list)

    def add(self, dim: Dimension) -> None:
        self.dimensions.append(dim)

    @property
    def failures(self) -> list[Dimension]:
        return [d for d in self.dimensions if d.failed]

    def exit_code(self) -> int:
        """Number of FAILED gating dimensions — LLM dims never contribute."""
        return len(self.failures)

    def render(self) -> str:
        lines: list[str] = []
        for d in self.dimensions:
            if d.skipped:
                mark, lane = "•", "skip"
            elif not d.gating:
                mark, lane = ("✓" if d.ok else "✗"), "report"
            else:
                mark, lane = ("✓" if d.ok else "✗"), "gate"
            lines.append(f"  [{mark}] ({lane}) {d.name} — {d.detail}")
        return "\n".join(lines)


# ── deterministic invariant dimensions (these gate the exit code) ────────────
# Mirror tests/bdd/e2e_report.py's _no_dup_titles / _coverage rather than
# reinventing them; they take the same (store[, codoc_dir]) shape.
def _no_dup_titles(store: Store) -> tuple[bool, str]:
    """No two live features share a title (the non-duplication invariant)."""
    titles = [f.title for f in store.list_features()]
    dups = sorted({t for t in titles if titles.count(t) > 1})
    return (not dups), (f"duplicate titles: {dups}" if dups else f"all {len(titles)} unique")


def _coverage(store: Store, chunk_keys: list[tuple[str, str]]) -> tuple[bool, str]:
    """Every indexed chunk is bound or claimed by a pending proposal — nothing
    silently dropped. ``chunk_keys`` is ``[(file, symbol_path), …]`` from the
    index (passed in so the function stays pure / index-free for unit tests)."""
    claimed = {(f, s) for e in store.pending_events() for (f, s) in e.op.bindings}
    dropped = [
        sym for (file, sym) in chunk_keys
        if store.binding_at(file, sym) is None and (file, sym) not in claimed
    ]
    return (not dropped), (
        f"{len(chunk_keys)} chunks, {len(dropped)} unaccounted: {dropped[:5]}"
        if dropped else f"all {len(chunk_keys)} chunks accounted for"
    )


def _hierarchy_balance(store: Store) -> tuple[bool, str]:
    """No single non-root parent owns a junk-drawer fraction of all features.

    A simple ratio check: count direct children per parent (counting top-level
    nodes under a synthetic root), take the heaviest *real* parent (not the
    root), and fail if it holds more than :data:`HIERARCHY_JUNK_DRAWER_RATIO` of
    all features. Skipped (always ok) on trees too small for a fraction to mean
    anything (:data:`HIERARCHY_MIN_FEATURES`)."""
    features = store.list_features()
    total = len(features)
    if total < HIERARCHY_MIN_FEATURES:
        return True, f"{total} features — too small to assess balance"
    counts: dict[str, int] = {}
    for f in features:
        if f.parent_id is not None:
            counts[f.parent_id] = counts.get(f.parent_id, 0) + 1
    if not counts:
        return True, f"{total} features, all top-level (no junk drawer)"
    worst_id, worst_n = max(counts.items(), key=lambda kv: kv[1])
    frac = worst_n / total
    worst = store.get_feature(worst_id)
    name = worst.title if worst else worst_id
    ok = frac <= HIERARCHY_JUNK_DRAWER_RATIO
    return ok, (
        f"largest parent {name!r} holds {worst_n}/{total} ({frac:.0%}; "
        f"limit {HIERARCHY_JUNK_DRAWER_RATIO:.0%})"
    )


def _ref_validity(codoc_dir: str) -> tuple[bool, str, bool]:
    """Count unresolved inline ``codoc:`` refs from ``tree.index.json``.

    Returns ``(ok, detail, skipped)``. The registry's ``refs[].resolved`` flag is
    reused directly (Plan A's writer) — no re-derivation. When the registry is
    ABSENT the dimension is SKIPPED gracefully (``skipped=True``), never an
    error: the eval harness must run against a tree generated before Plan A
    landed."""
    index_path = Path(codoc_dir) / INDEX_FILENAME
    if not index_path.exists():
        return True, "no tree.index.json — ref-validity skipped", True
    try:
        data = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt registry is a skip, not a harness crash.
        return True, f"tree.index.json unreadable ({exc}) — skipped", True
    refs = data.get("refs") or []
    dead = [r for r in refs if not r.get("resolved", True)]
    ok = not dead
    sample = [f"{r.get('label')} → {r.get('file')}#{r.get('symbol')}" for r in dead[:5]]
    return ok, (
        f"{len(refs)} refs, {len(dead)} unresolved: {sample}"
        if dead else f"all {len(refs)} refs resolved"
    ), False


def compute_invariants(store: Store, codoc_dir: str, chunk_keys: list[tuple[str, str]]) -> list[Dimension]:
    """The four DETERMINISTIC, gating dimensions — pure (no LLM, no live index).

    Importable + unit-testable on a hand-built Store fixture. ``chunk_keys`` is
    the index's ``(file, symbol_path)`` list (passed in so coverage stays pure)."""
    dims: list[Dimension] = []

    ok, detail = _coverage(store, chunk_keys)
    dims.append(Dimension("coverage", ok, detail, gating=True))

    ok, detail = _no_dup_titles(store)
    dims.append(Dimension("non-duplication", ok, detail, gating=True))

    ok, detail = _hierarchy_balance(store)
    dims.append(Dimension("hierarchy-balance", ok, detail, gating=True))

    ok, detail, skipped = _ref_validity(codoc_dir)
    dims.append(Dimension("ref-validity", ok, detail, gating=True, skipped=skipped))

    return dims


# ── LLM-judged dimensions (report-only — never gate the exit code) ───────────
# The codoc-ux-tester rubric. These are scored ONLY when OPENAI_API_KEY is set;
# they are printed but never asserted and never contribute to the exit code.
RUBRIC_DIMENSIONS = [
    "Layout",
    "Verbosity",
    "Duplicates",
    "Binding-quality",
    "Missing-coverage",
    "Subtree",
]


def _tree_outline(store: Store) -> str:
    """A compact root-first outline of the tree, for the LLM judge prompt."""
    lines: list[str] = []

    def walk(parent_id: str | None, depth: int) -> None:
        for f in store.children(parent_id):
            n = len(store.bindings_for_feature(f.id))
            desc = (f.description or "").split("\n", 1)[0][:120]
            lines.append("  " * depth + f"- {f.title} ({n} bind) — {desc}")
            walk(f.id, depth + 1)

    walk(None, 0)
    return "\n".join(lines) or "(empty tree)"


def judge_rubric(store: Store) -> list[Dimension]:
    """LLM-judged rubric dimensions (report-only). Returns one non-gating
    Dimension per rubric axis with the model's 1–5 score + a one-line note.

    The caller is responsible for only invoking this when an API key is present;
    if a judge call fails for any reason it degrades to an unscored note rather
    than crashing the harness (these dims never gate, so a failure is benign)."""
    outline = _tree_outline(store)
    prompt = (
        "You are a critical developer auditing a codoc feature tree (a human-intent "
        "view of a codebase). Score each rubric dimension 1 (poor) to 5 (excellent) "
        "and give a one-line reason. Respond as JSON: a list of "
        '{"dimension","score","reason"} objects, one per dimension.\n\n'
        f"Dimensions: {', '.join(RUBRIC_DIMENSIONS)}\n\n"
        f"The tree:\n{outline}\n"
    )
    dims: list[Dimension] = []
    try:
        scores = _parse_judge(run_agent(prompt))
    except Exception as exc:  # noqa: BLE001 — report-only, must never crash the gate
        for name in RUBRIC_DIMENSIONS:
            dims.append(Dimension(f"rubric:{name}", True, f"judge unavailable ({exc})",
                                  gating=False, skipped=True))
        return dims
    for name in RUBRIC_DIMENSIONS:
        entry = scores.get(name.lower(), {})
        score = entry.get("score")
        reason = entry.get("reason", "")
        if score is None:
            dims.append(Dimension(f"rubric:{name}", True, "no score returned",
                                  gating=False, skipped=True))
        else:
            # ok is informational only (>=3 reads as "acceptable") — never gates.
            dims.append(Dimension(f"rubric:{name}", bool(score and score >= 3),
                                  f"score {score}/5 — {reason}", gating=False))
    return dims


def _parse_judge(parsed: dict | list) -> dict[str, dict]:
    """Normalize the judge output (already JSON-parsed by ``run_agent``) into
    ``{dimension_lower: {...}}``. Accepts either a list of rows or a dict keyed
    by dimension."""
    rows: list = []
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        # Either {"dimensions":[...]} or {"Layout":{...}, ...}.
        if isinstance(parsed.get("dimensions"), list):
            rows = parsed["dimensions"]
        else:
            rows = [{"dimension": k, **(v if isinstance(v, dict) else {"score": v})}
                    for k, v in parsed.items()]
    out: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        dim = str(r.get("dimension", "")).strip().lower()
        if dim:
            out[dim] = r
    return out


# ── fixture builders ─────────────────────────────────────────────────────────
def build_clean_fixture(store: Store) -> list[tuple[str, str]]:
    """A small, well-formed tree used when no LLM is available. Returns the
    ``(file, symbol_path)`` chunk keys this tree fully covers — every key is
    bound, no duplicate titles, no junk drawer.

    This stands in for a real bootstrap so the deterministic invariants always
    have something to run against (and pass) with no API key."""
    from codoc.model.binding import Binding
    from codoc.model.feature import Feature

    auth = Feature(title="Authentication", description="Verify users and mint sessions.")
    math = Feature(title="Arithmetic helpers", description="Small number utilities.")
    store.upsert_feature(auth)
    store.upsert_feature(math)
    login = Feature(title="Login flow", description="Authenticate a user and return a session.",
                    parent_id=auth.id)
    session = Feature(title="Session tokens", description="Create a session token for a user.",
                      parent_id=auth.id)
    store.upsert_feature(login)
    store.upsert_feature(session)

    keys = [
        ("auth.py", "auth.py::login", login.id, "fp-login"),
        ("auth.py", "auth.py::create_session", session.id, "fp-session"),
        ("math_utils.py", "math_utils.py::add", math.id, "fp-add"),
        ("math_utils.py", "math_utils.py::subtract", math.id, "fp-sub"),
    ]
    for file, sym, fid, fp in keys:
        store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=sym, fingerprint=fp))
    return [(file, sym) for file, sym, _f, _fp in keys]


def _bootstrap_real(workdir: Path) -> tuple[str, list[tuple[str, str]]]:
    """Run a REAL bootstrap (real index + real LLM) on a tiny repo; returns
    ``(codoc_dir, chunk_keys)``. Only called when an API key is present."""
    from codoc.loop.bootstrap import run_init
    from codoc.pipelines.indexing.reader import read_all_chunks

    repo = workdir / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        'def create_session(user):\n    """Create a new session token."""\n'
        '    return f"token-{user}"\n\n\n'
        'def login(username, password):\n    """Authenticate and return a session."""\n'
        '    if password == "secret":\n        return create_session(username)\n    return None\n'
    )
    (repo / "math_utils.py").write_text(
        'def add(a, b):\n    """Add two numbers."""\n    return a + b\n\n\n'
        'def subtract(a, b):\n    """Subtract b from a."""\n    return a - b\n'
    )
    codoc_dir = str(repo / ".codoc")
    run_init(str(repo))
    chunks = read_all_chunks(codoc_dir, with_embeddings=False, with_source=False)
    return codoc_dir, [(c.file, c.symbol_path) for c in chunks]


# ── the run ──────────────────────────────────────────────────────────────────
def run(workdir: Path) -> int:
    from codoc.store.db import open_store

    have_key = bool(get_llm_config().api_key)
    report = EvalReport()

    print(f"{'═' * 70}\n GENERATION-QUALITY EVAL  (LLM judge: "
          f"{'ON' if have_key else 'OFF — self-gated, no OPENAI_API_KEY'})\n{'═' * 70}")

    if have_key:
        codoc_dir, chunk_keys = _bootstrap_real(workdir)
        store = open_store(codoc_dir)
        print("  Bootstrapped a real tree (index + LLM).\n")
    else:
        # Self-gate: no LLM → evaluate a deterministic fixture so the invariants
        # still run. No real index, so chunk_keys come from the fixture builder.
        codoc_dir = str(workdir / ".codoc")
        Path(codoc_dir).mkdir(parents=True, exist_ok=True)
        store = open_store(codoc_dir)
        chunk_keys = build_clean_fixture(store)
        # Render so tree.index.json (and the sidecar) exist for ref-validity.
        from codoc.codoc_file.render import write_tree
        write_tree(store, codoc_dir)
        print("  Evaluated a deterministic clean fixture tree.\n")

    print("  Tree outline:")
    print("\n".join("    " + ln for ln in _tree_outline(store).splitlines()))
    print()

    # Deterministic invariant dimensions (GATE the exit code).
    print(f"{'─' * 70}\n DETERMINISTIC INVARIANTS (gate exit code)\n{'─' * 70}")
    for dim in compute_invariants(store, codoc_dir, chunk_keys):
        report.add(dim)

    # LLM-judged rubric dimensions (REPORT-ONLY — never gate).
    print(f"{'─' * 70}\n RUBRIC (LLM-judged, report-only)\n{'─' * 70}")
    if have_key:
        for dim in judge_rubric(store):
            report.add(dim)
    else:
        for name in RUBRIC_DIMENSIONS:
            report.add(Dimension(f"rubric:{name}", True,
                                 "skipped (no OPENAI_API_KEY)", gating=False, skipped=True))

    store.close()

    print(report.render())

    code = report.exit_code()
    print(f"\n{'═' * 70}")
    if code:
        print(f"  INVARIANTS: {code} FAILED")
        for d in report.failures:
            print(f"    ✗ {d.name}: {d.detail}")
    else:
        print("  INVARIANTS: ALL PASS")
    print(f"{'═' * 70}")
    return code


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="codoc-eval-"))
    try:
        return run(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
