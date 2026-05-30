"""A tiny, dependency-free BDD harness for codoc's two loops.

``World`` is one codoc workspace under test: a real repo directory + a real
``.codoc`` SQLite store, driven through the *deterministic* seams of both loops
so a scenario can assert exactly where a code change lands in the feature tree.

The design mirrors how codoc actually runs, minus the non-determinism:

* **Loop A (code → codoc)** is driven through :func:`apply_changeset` with an
  *injected* ``propose`` callable, so the single LLM pass is replaced by a fixed
  list of ``NodeOp``s. Everything else in Loop A — auto-ops, move/rename
  relocation, placeholder adoption, graph-neighbor coverage, dedup, the coverage
  net — is fully deterministic and runs for real.
* **Loop B (codoc → code)** is driven through :func:`run_loop_b`: real
  ``tree.codoc`` text edits and real ``inbox.json`` verdicts, asserting the
  directives queued for the live session (never spawning anything).

Each verb narrates itself into ``world.transcript`` (and prints it), so a failing
scenario reads back as a Given/When/Then story. The non-deterministic real-LLM
flows live in ``test_e2e_userflows.py`` and print a report for manual inspection
instead of asserting exact positions.

Naming convention: ``given_*`` sets up state, ``when_*`` drives a loop, ``then_*``
asserts + narrates, and bare helpers (``owner_of`` …) are silent queries.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import inbox
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.loop.loop_b import LoopBResult, realize_path, run_loop_b
from codoc.loop.status import refresh_status, status_path
from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


# ── propose() injectors — stand in for the single Loop A LLM pass ────────────
def propose_nothing(*_a, **_k) -> list[NodeOp]:
    """The LLM returned no ops (forces the coverage net / adoption paths)."""
    return []


def propose_ops(*ops: NodeOp):
    """The LLM returns exactly ``ops`` (deterministic placement under test)."""
    def _p(*_a, **_k) -> list[NodeOp]:
        return list(ops)
    return _p


def propose_never(*_a, **_k):
    """Asserts the LLM is NOT consulted (the path should be fully deterministic)."""
    raise AssertionError("Loop A consulted the LLM but this path must be deterministic")


def chunk(file: str, symbol: str, *, tok: str = "", src: str = "", types: str = "") -> ChunkRef:
    """A chunk involved in a change: ``tok`` = tokens_hash (content identity),
    ``types`` = types_hash (AST-shape identity), ``src`` = its source text."""
    return ChunkRef(file=file, symbol_path=symbol, fingerprint=tok, source=src, types_hash=types)


class World:
    def __init__(self, root: Path, codoc_dir: Path) -> None:
        self.root = root
        self.codoc_dir = codoc_dir
        self.transcript: list[str] = []
        self.last_a = None          # last LoopAResult
        self.last_b: LoopBResult | None = None

    # -- store lifecycle: every verb uses a fresh, short-lived connection so a
    #    World-held handle never overlaps with Loop B opening its own store. ----
    @contextmanager
    def _store(self):
        s = open_store(str(self.codoc_dir))
        try:
            yield s
        finally:
            s.close()

    def _say(self, phase: str, text: str) -> None:
        line = f"{phase:<5} {text}"
        self.transcript.append(line)
        print(line)

    # ── GIVEN ────────────────────────────────────────────────────────────────
    def given_feature(self, title: str, *, parent: str | None = None, description: str = "",
                      realized: bool = True, binds=()) -> str:
        """Create a live feature (optionally with bindings); return its id."""
        f = Feature(title=title, description=description, parent_id=parent, realized=realized)
        with self._store() as s:
            s.upsert_feature(f)
            for spec in binds:
                file, symbol = spec[0], spec[1]
                tok = spec[2] if len(spec) > 2 else "fp"
                types = spec[3] if len(spec) > 3 else ""
                s.upsert_binding(Binding(feature_id=f.id, file=file, symbol_path=symbol,
                                         fingerprint=tok, types_hash=types))
        owned = ", ".join(b[1] for b in binds)
        self._say("GIVEN", f'feature "{title}"' + (f" owning [{owned}]" if owned else " (no code yet)"))
        return f.id

    def given_placeholder(self, title: str, *, description: str = "", parent: str | None = None) -> str:
        """An accepted plan node with no code yet (``realized=False``)."""
        fid = self.given_feature(title, description=description, parent=parent, realized=False)
        self.transcript[-1] += "  [unrealized placeholder]"
        return fid

    def given_binding(self, fid: str, file: str, symbol: str, *, tok: str = "fp", types: str = "") -> None:
        with self._store() as s:
            s.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=symbol,
                                     fingerprint=tok, types_hash=types))
        self._say("GIVEN", f"{symbol} bound to {self.title_of(fid)!r}")

    def given_call_edge(self, src_sym: str, dst_sym: str, *, kind: str = "call") -> None:
        """A code-graph edge: ``src_sym`` calls/imports ``dst_sym`` (internal)."""
        with self._store() as s:
            s.insert_edges([{
                "src_file": src_sym.split("::", 1)[0], "src_symbol": src_sym,
                "dst_name": dst_sym.split("::")[-1].split(".")[-1], "dst_symbol": dst_sym,
                "dst_file": dst_sym.split("::", 1)[0], "kind": kind, "internal": 1,
            }])
        self._say("GIVEN", f"{src_sym} {kind}s {dst_sym}")

    def given_pending_add(self, title: str, binds=(), *, description: str = "",
                          parent: str | None = None, realized=None, source: str = "loop_a") -> str:
        """An ADD_NODE proposal already waiting in the inbox; returns its event id."""
        op = NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                    parent_id=parent, bindings=[(f, s) for f, s in binds], realized=realized)
        return self.given_pending(op, source=source, label=f'propose ADD "{title}"')

    def given_pending(self, op: NodeOp, *, source: str = "loop_a", label: str = "") -> str:
        e = Event(source=source, applied=False, op=op)
        with self._store() as s:
            s.append_event(e)
        self._say("GIVEN", label or f"pending {op.kind.value} proposal")
        return e.id

    def render(self) -> None:
        """Write the current store out to ``tree.codoc`` (the human surface)."""
        with self._store() as s:
            write_tree(s, self.codoc_dir)
        self._say("GIVEN", "tree.codoc rendered from the store")

    # ── WHEN ─────────────────────────────────────────────────────────────────
    def when_code_changes(self, *, added=(), removed=(), modified=(),
                          propose=propose_nothing, adopt_placeholders: bool = False, label: str = ""):
        """Drive Loop A over a change set with an injected ``propose`` (the LLM)."""
        cs = ChangeSet(added=list(added), removed=list(removed), modified=list(modified))
        with self._store() as s:
            res = apply_changeset(cs, s, propose=propose, adopt_placeholders=adopt_placeholders)
            refresh_status(self.codoc_dir, s)
        self.last_a = res
        if not label:
            bits = []
            if added:    bits.append(f"+{len(list(added))}")
            if modified: bits.append(f"~{len(list(modified))}")
            if removed:  bits.append(f"-{len(list(removed))}")
            label = "code changes (" + " ".join(bits) + ") flow through Loop A"
        self._say("WHEN", label + f"  → {res.summary()}")
        return res

    def edit_tree(self, old: str, new: str) -> None:
        """Simulate a human editing ``tree.codoc`` text (must call ``render`` first)."""
        path = tree_path(self.codoc_dir)
        text = path.read_text()
        assert old in text, f"edit_tree: {old!r} not found in tree.codoc"
        path.write_text(text.replace(old, new))
        self._say("WHEN", f"user edits tree.codoc: {old!r} → {new!r}")

    def when_accept(self, event_id: str) -> None:
        inbox.append_verdict(self.codoc_dir, event_id, accept=True)
        self._say("WHEN", f"user ACCEPTS proposal {event_id}")

    def when_reject(self, event_id: str) -> None:
        inbox.append_verdict(self.codoc_dir, event_id, accept=False)
        self._say("WHEN", f"user REJECTS proposal {event_id}")

    def when_loop_b(self, *, dry_run: bool = False) -> LoopBResult:
        """Drain verdicts + apply tree edits → build/queue realize directives."""
        res = run_loop_b(str(self.root), str(self.codoc_dir), dry_run=dry_run)
        self.last_b = res
        self._say("WHEN", f"Loop B runs ({'dry' if dry_run else 'live'})  → {res.summary()}")
        return res

    # ── silent queries ─────────────────────────────────────────────────────────
    def owner_of(self, file: str, symbol: str) -> str | None:
        with self._store() as s:
            b = s.binding_at(file, symbol)
            return b.feature_id if b else None

    def feature(self, fid: str) -> Feature | None:
        with self._store() as s:
            return s.get_feature(fid)

    def title_of(self, fid: str | None) -> str:
        if not fid:
            return "(none)"
        f = self.feature(fid)
        return f.title if f else fid

    def features(self, *, include_retired: bool = False) -> list[Feature]:
        with self._store() as s:
            return s.list_features(include_retired=include_retired)

    def proposals(self) -> list[Event]:
        with self._store() as s:
            return s.pending_events()

    def pending_add_id(self, title_substr: str) -> str:
        """Event id of the lone pending ADD_NODE whose title contains the substring."""
        hits = [e for e in self.proposals()
                if e.op.kind is NodeOpKind.ADD_NODE and title_substr.lower() in (e.op.title or "").lower()]
        assert len(hits) == 1, f"expected one pending ADD matching {title_substr!r}, got {[e.op.title for e in hits]}"
        return hits[0].id

    def status(self) -> str:
        return json.loads(status_path(self.codoc_dir).read_text())["state"]

    def realize_text(self) -> str:
        p = realize_path(self.codoc_dir)
        return p.read_text() if p.exists() else ""

    # ── THEN (assert + narrate) ────────────────────────────────────────────────
    def then_owner_is(self, file: str, symbol: str, fid: str, *, note: str = "") -> None:
        got = self.owner_of(file, symbol)
        assert got == fid, f"{symbol} owned by {self.title_of(got)!r}, expected {self.title_of(fid)!r}"
        self._say("THEN", f"{symbol} sits under {self.title_of(fid)!r}" + (f" ({note})" if note else ""))

    def then_unbound(self, file: str, symbol: str) -> None:
        got = self.owner_of(file, symbol)
        assert got is None, f"{symbol} unexpectedly bound to {self.title_of(got)!r}"
        self._say("THEN", f"{symbol} is unbound (not silently mis-placed)")

    def then_parent_is(self, fid: str, parent_fid: str | None) -> None:
        f = self.feature(fid)
        assert f is not None and f.parent_id == parent_fid, \
            f"{self.title_of(fid)!r} parent is {self.title_of(f.parent_id if f else None)!r}, expected {self.title_of(parent_fid)!r}"
        self._say("THEN", f"{self.title_of(fid)!r} is positioned under {self.title_of(parent_fid)!r}")

    def then_realized(self, fid: str, expected: bool) -> None:
        f = self.feature(fid)
        assert f is not None and f.realized is expected, \
            f"{self.title_of(fid)!r} realized={f.realized if f else None}, expected {expected}"
        self._say("THEN", f"{self.title_of(fid)!r} realized={expected}")

    def then_retired(self, fid: str, expected: bool = True) -> None:
        with self._store() as s:
            f = s.get_feature(fid)
        assert f is not None and f.retired is expected, \
            f"{self.title_of(fid)!r} retired={f.retired if f else None}, expected {expected}"
        self._say("THEN", f"{self.title_of(fid)!r} retired={expected}")

    def then_proposal_count(self, n: int) -> None:
        got = self.proposals()
        assert len(got) == n, f"expected {n} pending proposals, got {len(got)}: {[e.op.kind.value for e in got]}"
        self._say("THEN", f"{n} proposal(s) pending review")

    def then_proposed_add(self, title_substr: str) -> str:
        eid = self.pending_add_id(title_substr)
        self._say("THEN", f'a new node is proposed (not auto-applied) matching "{title_substr}"')
        return eid

    def then_status(self, state: str) -> None:
        got = self.status()
        assert got == state, f"status is {got!r}, expected {state!r}"
        self._say("THEN", f"status.json = {state}")

    def then_title_count(self, title: str, n: int) -> None:
        got = [f for f in self.features() if f.title == title]
        assert len(got) == n, f"expected {n} live feature(s) titled {title!r}, got {len(got)}"
        self._say("THEN", f"exactly {n} feature(s) titled {title!r} (no duplicate)")

    def then_feature_exists(self, title: str) -> str:
        hits = [f for f in self.features() if f.title == title]
        assert len(hits) == 1, f"expected exactly one feature titled {title!r}, got {len(hits)}"
        self._say("THEN", f'feature "{title}" now exists')
        return hits[0].id

    def then_directive_mentions(self, *substrs: str) -> None:
        assert self.last_b is not None, "no Loop B result — call when_loop_b first"
        blob = "\n".join(self.last_b.directives)
        for sub in substrs:
            assert sub in blob, f"directive missing {sub!r}; directives:\n{blob}"
        self._say("THEN", "a realize directive is queued mentioning " + ", ".join(repr(s) for s in substrs))

    def then_no_directives(self) -> None:
        assert self.last_b is not None, "no Loop B result — call when_loop_b first"
        assert self.last_b.directives == [], f"unexpected directives: {self.last_b.directives}"
        self._say("THEN", "no code-realize directive is queued (documentation only)")

    def then_impacted_includes(self, fid: str) -> None:
        assert self.last_a is not None, "no Loop A result — call when_code_changes first"
        assert fid in self.last_a.impacted, \
            f"{self.title_of(fid)!r} not flagged as impacted; impacted={[self.title_of(x) for x in self.last_a.impacted]}"
        self._say("THEN", f"dependent feature {self.title_of(fid)!r} is flagged as impacted")
