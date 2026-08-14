"""Replay a repository's commit history through Loop A and record what happened.

One process owns one repository for its whole history. That is not a style
choice: cocoindex's App is a module-level singleton, so a process can drive
exactly one ``codoc_dir``. Parallelism therefore goes ACROSS repositories, never
across commits — and commits have to be sequential anyway, since each one's
starting state is the previous one's result.

What the loop does per commit:

1. snapshot the bindings and the resolvability of their addresses
2. check out the child commit
3. re-index only the touched files and hand the changeset to Loop A
4. snapshot again, diff the two, and classify each binding's fate against
   :mod:`evals.replay.gitfacts`

Everything is appended to a JSONL as it goes. A replay of a few hundred commits
takes hours and *will* be interrupted; a run that only reports at the end would
lose the evidence of the failure that killed it, which during shakeout is the
most valuable thing it produces.
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from codoc.config import usage_snapshot
from codoc.loop.loop_a import run_loop_a
from codoc.pipelines.indexing.reader import read_all_chunks
from codoc.store.db import open_store

from evals.replay.gitfacts import CommitFacts, checkout, commit_facts, commits_between
from evals.replay.symbols import SymbolFacts, symbol_facts


@dataclass(frozen=True)
class Addr:
    file: str
    symbol_path: str


def _bindings(codoc_dir: Path) -> dict[Addr, str]:
    """Address → feature id, for every live binding."""
    with open_store(str(codoc_dir)) as store:
        return {
            Addr(b.file, b.symbol_path): b.feature_id for b in store.all_bindings()
        }


def _index_addrs(codoc_dir: Path) -> set[Addr]:
    """Every address the index currently knows about.

    Source text is the heavy column and this only needs the keys, so it is left
    behind — the read runs once per replayed commit.
    """
    return {
        Addr(row.file, row.symbol_path)
        for row in read_all_chunks(
            str(codoc_dir), with_embeddings=False, with_source=False,
        )
    }


@dataclass
class StepRecord:
    """One commit's outcome. Written as one JSONL line."""

    sha: str
    parent: str
    subject: str
    index: int                     # position in the replay, 0-based

    # what git says changed
    files_added: int = 0
    files_deleted: int = 0
    files_modified: int = 0
    files_renamed: int = 0

    # what Loop A did
    auto: dict = field(default_factory=dict)
    proposed: int = 0
    applied_structural: int = 0
    held_back: int = 0
    llm_called: bool = False
    llm_calls: int = 0

    # cost
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    # state after this commit
    bindings_after: int = 0
    features_after: int = 0
    unresolvable_after: int = 0    # bindings whose address is not in the index
    coverage: float = 0.0          # cov(B): indexed chunks that carry a binding
    pending_after: int = 0         # proposals awaiting a verdict — see note below
    accepted: int = 0              # attended arm only: proposals accepted this step

    # attribution outcomes vs git ground truth
    followed_rename: int = 0       # file-level: whole path renamed
    missed_rename: int = 0
    # Symbol-level moves, split three ways. Collapsing them into one rate hid
    # the difference that matters: a chunk that arrives at its new address under
    # a different feature has NOT rotted, it has been re-attributed, and a tree
    # reorganizing itself around a refactor may be doing that correctly. A chunk
    # that arrives nowhere is the actual failure the system exists to prevent.
    followed_move: int = 0         # same feature owns it at the new address
    rebound_move: int = 0          # bound at the new address, different feature
    lost_move: int = 0             # not bound at the new address at all
    detached_on_delete: int = 0
    stale_after_delete: int = 0
    survived_untouched: int = 0
    disturbed_untouched: int = 0   # binding in an UNTOUCHED file changed — always a bug
    symbol_moves: int = 0          # moves ground truth saw, bound or not

    error: str = ""

    def as_json(self) -> str:
        return json.dumps(asdict(self))


def _classify(
    facts: CommitFacts,
    symfacts: SymbolFacts,
    before: dict[Addr, str],
    after: dict[Addr, str],
    rec: StepRecord,
) -> None:
    """Score each pre-existing binding's fate against what ground truth says.

    Two independent sources, because one is not enough. Git proves file-level
    renames and deletions. :mod:`evals.replay.symbols` proves symbol-level moves
    by name, which is the far more common case and the one git reports as two
    unrelated modifications.
    """
    renamed, deleted = facts.renamed, facts.deleted
    touched = facts.touched

    # Symbol moves first: they claim their addresses out of the file-level pass
    # below, which would otherwise see two modified files and score nothing.
    rec.symbol_moves = symfacts.move_count
    moved_addrs: set[Addr] = set()
    for name, (old_file, new_file) in symfacts.moved.items():
        old = Addr(old_file, f"{old_file}::{name}")
        feature_id = before.get(old)
        if feature_id is None:
            continue   # nothing was bound there; not a miss, just not under test
        moved_addrs.add(old)
        new = Addr(new_file, f"{new_file}::{name}")
        landed = after.get(new)
        if landed == feature_id:
            rec.followed_move += 1
        elif landed is not None:
            rec.rebound_move += 1
        else:
            rec.lost_move += 1

    for addr, feature_id in before.items():
        if addr in moved_addrs:
            continue
        if addr.file.endswith(".py") and addr.file in renamed:
            # For a file we can parse, the symbol detector is authoritative and
            # has already had its say. A leftover address here did NOT move to
            # the renamed path: flask's sansio split renamed a file and
            # redistributed its contents in the same commit, so assuming
            # otherwise invented failures. It is a deletion or a move elsewhere,
            # both covered by their own ground truth.
            continue
        if addr.file in renamed:
            moved_to = Addr(renamed[addr.file], addr.symbol_path)
            # Following the rename means the SAME feature owns the symbol at its
            # new path. Landing on the right path under a different feature is a
            # mis-attribution, not a success, so the feature id is checked too.
            if after.get(moved_to) == feature_id:
                rec.followed_rename += 1
            else:
                rec.missed_rename += 1
        elif addr.file in deleted:
            if addr in after:
                rec.stale_after_delete += 1
            else:
                rec.detached_on_delete += 1
        elif addr.file not in touched:
            # Nothing git can see happened to this file. The binding must be
            # exactly where it was, under the same feature. Anything else is the
            # loop reaching outside the change it was given.
            if after.get(addr) == feature_id:
                rec.survived_untouched += 1
            else:
                rec.disturbed_untouched += 1


def _accept_pending(repo: Path, codoc_dir: Path) -> int:
    """Accept every pending proposal and drain it, as an attentive maintainer would.

    The unattended arm answers "do bindings survive on their own", which is the
    system's actual claim. It cannot answer what the tree looks like in use,
    because a structural op — a NEW feature node — is deliberately gated on a
    human verdict, so an unattended replay leaves that code unattributed and
    coverage falls for a reason that is not rot. This arm supplies the missing
    human: same verdict path as the IDE's Accept and the `codoc accept` CLI.

    Accepting is safe to automate here. `classify.edit_mints_directive` only
    mints code-writing work for an ADD_NODE that is an unrealized *plan*
    placeholder, and Loop A's drift proposals describe code that already exists,
    so nothing queues an agent to go and write anything. Retires cannot appear
    at all: `run_loop_a` passes `allow_retire=False`.
    """
    from codoc.loop import inbox
    from codoc.loop.loop_b import run_loop_b

    with open_store(str(codoc_dir)) as store:
        pending = [e.id for e in store.pending_events()]
    if not pending:
        return 0
    for eid in pending:
        inbox.append_verdict(str(codoc_dir), eid, accept=True)
    run_loop_b(str(repo), str(codoc_dir))
    return len(pending)


def _scoring_trace(
    facts: CommitFacts, symfacts: SymbolFacts,
    before: dict[Addr, str], after: dict[Addr, str],
) -> dict:
    """Everything needed to re-score this commit without replaying it.

    Ground-truth logic changes during shakeout — the first version of it scored
    file renames against a symbol model that omitted module-level assignments,
    and correcting that would otherwise have meant re-running a paid bootstrap
    and hours of replay to recompute numbers from evidence already gathered.

    The whole binding map is kept, not just the touched part. Keeping only
    touched files looked like a free saving — bindings elsewhere are not
    supposed to change — but that reasoning is circular: "not supposed to
    change" is the untouched-file invariant, and dropping those rows deletes the
    only evidence that could falsify it. A rescore then reports the invariant as
    having no data rather than as holding. Roughly 60 bytes per binding per
    commit is a few megabytes per corpus, which is nothing against the cost of
    not being able to check the one property that catches the loop reaching
    outside the change it was given.
    """
    keep = lambda m: {  # noqa: E731
        f"{a.file}\t{a.symbol_path}": fid for a, fid in m.items()
    }
    return {
        "sha": facts.sha,
        "renamed": facts.renamed,
        "deleted": sorted(facts.deleted),
        "added": sorted(facts.added),
        "modified": sorted(facts.modified),
        "moved": {k: list(v) for k, v in symfacts.moved.items()},
        "before": keep(before),
        "after": keep(after),
        "n_before": len(before),
        "n_after": len(after),
    }


def replay(
    repo: Path,
    *,
    base: str,
    head: str = "HEAD",
    subdir: str = "",
    out_path: Path,
    limit: int | None = None,
    skip_empty: bool = True,
    done: set[str] | None = None,
    auto_accept: bool = False,
) -> list[StepRecord]:
    """Walk ``base``..``head``, applying each commit through Loop A.

    Assumes the repository is already checked out at ``base`` with ``codoc init``
    run — bootstrap is a separate, expensive step and does not belong inside the
    measurement loop.

    ``done`` is the set of shas already recorded, which a resumed run must skip.
    Without it a resume re-walks from ``base`` and appends a second row for every
    commit it already has: the working tree is past them, so each re-application
    sees an empty changeset and records a plausible-looking no-op. The duplicates
    are invisible in a summary and drag every rate toward whatever a no-op scores.
    """
    done = done or set()
    codoc_dir = repo / ".codoc"
    shas = commits_between(repo, base, head)
    records: list[StepRecord] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path = out_path.with_name("traces.jsonl")
    written = 0

    with out_path.open("a") as sink, trace_path.open("a") as traces:
        for i, sha in enumerate(shas):
            if limit is not None and written >= limit:
                break
            if sha in done:
                continue
            facts = commit_facts(repo, sha, subdir=subdir)
            if skip_empty and facts.is_empty:
                # No indexed file changed. Checking it out and running a pass
                # would add a no-op row that drags every rate toward zero.
                continue

            rec = StepRecord(
                sha=sha, parent=facts.parent, subject=facts.subject, index=i,
                files_added=len(facts.added), files_deleted=len(facts.deleted),
                files_modified=len(facts.modified), files_renamed=len(facts.renamed),
            )
            try:
                before = _bindings(codoc_dir)
                usage_before = usage_snapshot()
                t0 = time.time()

                checkout(repo, sha)
                result = run_loop_a(
                    str(repo), str(codoc_dir), file_scope=facts.touched,
                    source="replay",
                )

                rec.seconds = round(time.time() - t0, 2)
                spent = usage_snapshot() - usage_before
                rec.calls = spent.calls
                rec.input_tokens = spent.input_tokens
                rec.output_tokens = spent.output_tokens
                rec.cache_read_tokens = spent.cache_read_tokens
                rec.cost_usd = round(spent.cost_usd, 6)

                rec.auto = dict(result.auto)
                rec.proposed = len(result.proposed)
                rec.applied_structural = len(result.applied_structural)
                rec.held_back = result.held_back
                rec.llm_called = result.llm_called
                rec.llm_calls = result.llm_calls

                # Before the after-snapshot, so accepted proposals are part of
                # the state this commit is scored against — an attentive
                # maintainer reviews as the change lands, not a commit later.
                if auto_accept:
                    rec.accepted = _accept_pending(repo, codoc_dir)

                after = _bindings(codoc_dir)
                indexed = _index_addrs(codoc_dir)
                rec.bindings_after = len(after)
                rec.unresolvable_after = sum(1 for a in after if a not in indexed)
                rec.coverage = (
                    round(len(set(after) & indexed) / len(indexed), 4) if indexed else 0.0
                )
                # Coverage and the pending backlog have to be read together.
                # A structural op (a NEW feature node) is deliberately not
                # auto-applied — it becomes a proposal waiting on a human
                # verdict, which is the whole authored-intent premise. An
                # unattended replay has no human, so those chunks stay unbound
                # and coverage falls. That is the absence of a reviewer, NOT
                # lost attribution, and reporting coverage alone would charge
                # the system for it.
                with open_store(str(codoc_dir)) as store:
                    rec.features_after = len(store.list_features())
                    rec.pending_after = len(store.pending_events())

                # Computed from the git objects, so it must run while both
                # commits are still reachable — it does not depend on which one
                # is checked out.
                symfacts = symbol_facts(
                    repo, facts.parent, sha, facts.touched, renamed=facts.renamed,
                )
                _classify(facts, symfacts, before, after, rec)
                traces.write(
                    json.dumps(_scoring_trace(facts, symfacts, before, after)) + "\n"
                )
                traces.flush()
            except Exception:
                # A crash on one commit must not end the replay. During shakeout
                # the traceback IS the result, so it is recorded and the walk
                # continues to find out whether the failure is isolated.
                rec.error = traceback.format_exc()[-2000:]

            records.append(rec)
            sink.write(rec.as_json() + "\n")
            sink.flush()
            written += 1

    return records
