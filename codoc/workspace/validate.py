"""Checks for a log this workspace did not write (docs/concurrency.md, Roles
and authority).

Authority is checked when an op arrives and the answer is recorded; replay
honours the record, so a role changed after the fact never deletes what
somebody already wrote. So a log file is a capability: edit the recorded
`conflict` off a refused `plan.accept` and every clone that rebuilds will
accept the plan. `codoc validate-logs` runs these checks over the session
files a merge would add or change, against the tracked role table.

Four refusals, and nothing else:

- an op recorded as a **success** whose `(actor, kind)` the table would refuse,
  which is the forged verdict;
- an op in a session file carrying a field only a snapshot writes
  (`ops.SNAPSHOT_ONLY`), which would let it claim another actor's words;
- a **snapshot** file, which claims a whole state rather than a sequence of ops
  and is legitimate only when the workspace wrote it by folding its own log;
- a stamp **far ahead of wall time**, which would drag every clock that
  observes it into the future and order real work behind it.

Everything else a log can be wrong about (a torn tail, an out-of-sequence id,
an unreadable line) `log.read_session` already reports and the rebuild already
skips the file whole; those are reported here too.

These checks are for a *foreign* log. A log this workspace wrote went through
the applier, where the same table already answered.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.ops import Op
from codoc.calculus.doc import PARAGRAPH, Block
from codoc.calculus.store import Plan, Store
from codoc.workspace import log as L
from codoc.workspace.clock import parse
from codoc.workspace.roles import Roles

FUTURE_SKEW_MS = 24 * 60 * 60 * 1000       # a day: a clock this wrong is not drift
SNAPSHOT_BATCH = "snap-"                   # a fold's own session, and so its batch


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.reason}"


def validate_logs(paths, *, roles: Roles, store: Store | None = None, now_ms: int | None = None,
                  skew_ms: int = FUTURE_SKEW_MS) -> list[Finding]:
    """What is wrong with these log files, read as a stranger's. ``store`` is
    the workspace's own, so a suggester's op on a plan opened before the pull
    request is recognised; without one only the plans these files open count."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    out: list[Finding] = []
    for p in map(Path, paths):
        if p.name.startswith(L.SNAPSHOT_PREFIX):
            out.append(Finding(str(p), 0, "a snapshot states a whole state; only the workspace that folds its own log may write one"))
            continue
        f = L.read_session(p)
        out.extend(Finding(str(p), pr.line, pr.reason) for pr in f.problems)
        view = _policy_view(store, f.ops)
        for i, op in enumerate(f.ops, start=2):        # line 1 is the header
            if op.conflict is None and (why := roles.policy(view, op)):
                out.append(Finding(str(p), i, f"recorded as applied, but the roles table refuses it: {why}"))
            if forged := [k for k in K.SNAPSHOT_ONLY.get(op.kind, ()) if k in op.payload]:
                out.append(Finding(str(p), i, f"{op.kind} carries {', '.join(forged)}, which only a snapshot writes"))
            ahead = _ms(op.at) - now_ms
            if ahead > skew_ms:
                out.append(Finding(str(p), i, f"stamped {ahead // 3_600_000}h ahead of this clock"))
    return out


def changed_logs(root: str | Path, base: str) -> list[Path]:
    """The log files a merge of ``base..HEAD`` would add or change. Anything
    outside ``.codoc/log/`` is not this validator's business."""
    root = Path(root)
    try:
        raw = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"],
                             capture_output=True, text=True, timeout=30, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    out = []
    for line in raw.splitlines():
        p = Path(line.strip())
        if p.suffix == ".jsonl" and p.parent.name == "log" and p.parent.parent.name == ".codoc":
            out.append(root / p)
    return out


def refused_verdicts(store: Store, roles: Roles) -> dict[str, int]:
    """Per actor, how many applied ops in the store the table would refuse
    today. Not a gate: a demotion is allowed to leave earlier work standing, so
    this is the signal that says a log carries something the table never would
    have let in (`codoc rebuild` prints it)."""
    out: dict[str, int] = {}
    for op in store.ops:
        if op.batch.startswith(SNAPSHOT_BATCH):
            continue        # a fold re-expresses ops that were authorized when written
        if op.conflict is None and roles.policy(store, op):
            out[op.actor] = out.get(op.actor, 0) + 1
    return out


def _policy_view(store: Store | None, ops: list[Op]) -> Store:
    """A store the policy can answer from.

    It reads the PLANS, which is nearly all of it, on purpose: a
    policy that needs the whole document is one this door cannot evaluate. The
    exception is a comment, where "is this thread yours" is who added the
    block, so the blocks these files add and the workspace's own come too.
    """
    view = Store()
    if store is not None:
        view.plans.update(store.plans)
        view.blocks.update(store.blocks)
        for bid, idx in store.ops_by_block.items():
            view.ops_by_block[bid] = list(idx)
        view.ops = list(store.ops)
    for op in ops:
        if op.kind == K.PLAN_OPEN and (pid := op.target.get("pid")):
            view.plans.setdefault(str(pid), Plan(id=str(pid), actor=op.actor))
        if op.kind == K.BLOCK_ADD and op.conflict is None and (bid := op.target.get("bid")):
            view.blocks.setdefault(str(bid), Block(str(bid), str(op.payload.get("kind") or PARAGRAPH), "",
                                                   content=dict(op.payload.get("content") or {})))
            view.ops_by_block.setdefault(str(bid), []).append(len(view.ops))
            view.ops.append(op)
    return view


def _ms(stamp: str) -> int:
    try:
        return parse(stamp)[0]
    except (ValueError, AttributeError):
        return 0
