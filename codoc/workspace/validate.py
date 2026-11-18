"""Checks for a log this workspace did not write (docs/concurrency.md, Roles
and authority).

Authority is checked when an op arrives and the answer is recorded; replay
honours the record, so a role changed after the fact never deletes what
somebody already wrote. So a log file is a capability: edit the recorded
`conflict` off a refused `plan.accept` and every clone that rebuilds will
accept the plan. `codoc validate-logs` runs these checks over the session
files a merge would add or change, against the tracked role table.

Five refusals, and nothing else:

- an op recorded as a **success** whose `(actor, kind)` the table would refuse,
  which is the forged verdict (a withdrawal of the actor's own empty plan is
  judged on every op tagged with that plan a writer recorded as applied);
- an op in a session file carrying a field only a snapshot writes
  (`ops.SNAPSHOT_ONLY`), which would let it claim another actor's words;
- a **snapshot** file, which claims a whole state rather than a sequence of ops
  and is legitimate only when the workspace wrote it by folding its own log;
- a stamp **far ahead of wall time**, which would drag every clock that
  observes it into the future and order real work behind it;
- an op **tagged with a plan after that plan's verdict**, recorded as applied,
  which the applier would have refused (`plan_not_open`). The verdicts are the
  workspace's and these files', ordered by clock; one a fold re-emitted is
  stamped with the fold's clock, so it never flags an op written before it.

Everything else a log can be wrong about (a torn tail, an out-of-sequence id,
an unreadable line) `log.read_session` already reports and the rebuild already
skips the file whole; those are reported here too.

These checks are for a *foreign* log. A log this workspace wrote went through
the applier, where the same table already answered.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, replace
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


def validate_logs(paths, *, roles: Roles, store: Store | None = None, recorded: list[Op] | None = None,
                  now_ms: int | None = None, skew_ms: int = FUTURE_SKEW_MS) -> list[Finding]:
    """What is wrong with these log files, read as a stranger's. ``store`` is
    the workspace's own, so a suggester's op on a plan opened before the pull
    request is recognised; without one only the plans these files open count.
    ``recorded`` is the workspace's ops as its session files record them (each
    writer's own answer); a replayed store's answers follow the merge order,
    which a forger chooses. Without it the store's ops stand in."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    out: list[Finding] = []
    files = []
    for p in map(Path, paths):
        if p.name.startswith(L.SNAPSHOT_PREFIX):
            out.append(Finding(str(p), 0, "a snapshot states a whole state; only the workspace that folds its own log may write one"))
            continue
        files.append((p, L.read_session(p)))
    # A plan's past is read from everything known, the workspace's log and
    # every file under check, by clock.
    theirs = [o for _, f in files for o in f.ops]
    ours = recorded if recorded is not None else (store.ops if store else [])
    known = list({o.id: o for o in [*ours, *theirs]}.values())
    view = _policy_view(store, theirs)
    turns = _turns(known)
    for p, f in files:
        out.extend(Finding(str(p), pr.line, pr.reason) for pr in f.problems)
        for i, op in enumerate(f.ops, start=2):        # line 1 is the header
            if op.conflict is None and (why := _refusal(roles, view, op, known, turns)):
                out.append(Finding(str(p), i, f"recorded as applied, but the roles table refuses it: {why}"))
            if op.conflict is None and op.plan and op.kind not in K.TURNS and _last_turn(turns, op.plan, op.at) in DECIDED:
                out.append(Finding(str(p), i, f"recorded as applied under plan {op.plan} after its verdict"))
            if forged := [k for k in K.SNAPSHOT_ONLY.get(op.kind, ()) if k in op.payload]:
                out.append(Finding(str(p), i, f"{op.kind} carries {', '.join(forged)}, which only a snapshot writes"))
            ahead = _ms(op.at) - now_ms
            if ahead > skew_ms:
                out.append(Finding(str(p), i, f"stamped {ahead // 3_600_000}h ahead of this clock"))
    return out


def base_records(root: str | Path, base: str, under_check: list[Path], local: list[Path]) -> tuple[list[Op], list[Finding]]:
    """The workspace's ops as their writers recorded them, and the files that
    rewrite that record. A session file is only ever appended to, so a file
    under check that does not start with its ``base`` version rewrote history
    (a recorded answer edited is a capability forged). For a file under check
    the base version's ops are the record; the lines it appends are checked as
    a stranger's. Any other local file is the record as it is.

    The base is the merge base of ``base`` and HEAD, which the merge's diff is
    against: a branch that forked before ``base`` grew a file holds a shorter
    copy of it. Only whole lines count, since a daemon that starts cuts a torn
    last line the base committed."""
    import tempfile
    root, checked = Path(root), {p.resolve() for p in under_check}
    fork = subprocess.run(["git", "-C", str(root), "merge-base", base, "HEAD"], capture_output=True, text=True)
    at = fork.stdout.strip() if fork.returncode == 0 else base
    ops: list[Op] = []
    findings: list[Finding] = []
    for p in local:
        if p.resolve() not in checked:
            ops.extend(L.read_session(p).ops)
            continue
        rel = p.resolve().relative_to(root.resolve()).as_posix()
        shown = subprocess.run(["git", "-C", str(root), "show", f"{at}:{rel}"], capture_output=True, text=True)
        if shown.returncode != 0:
            continue                    # new in this merge: all of it is checked as a stranger's
        whole = shown.stdout[: shown.stdout.rfind("\n") + 1]
        if not p.read_text(encoding="utf-8").startswith(whole):
            findings.append(Finding(str(p), 0, f"rewrites lines {base} already has; a session file is only appended to"))
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / p.name
            q.write_text(whole, encoding="utf-8")
            ops.extend(L.read_session(q).ops)
    return ops, findings


def changed_logs(root: str | Path, base: str, change: str = "AM") -> list[Path]:
    """The log files a merge of ``base...HEAD`` would add or change, or with
    ``change="D"`` remove. A rename counts as a removal and an add, so a file
    moved into ``archive/`` is removed. Anything outside ``.codoc/log/`` is not
    this validator's business."""
    root = Path(root)
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                      capture_output=True, timeout=30).returncode != 0:
        raise ValueError(f"the base {base} does not exist in this repository; pass --base <branch>")
    run = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--no-renames", f"--diff-filter={change}", f"{base}...HEAD"],
                         capture_output=True, text=True, timeout=30)
    if run.returncode != 0:     # a shallow clone or an unrelated base: checking nothing must not pass
        raise ValueError(f"cannot diff {base}...HEAD: {run.stderr.strip()}")
    raw = run.stdout
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
    have let in (`codoc rebuild` prints it). Each op is judged by the rule
    `validate_logs` uses (``_refusal``): a withdrawal against its plan as it
    stood, not as the withdrawal left it."""
    out: dict[str, int] = {}
    view, turns = _policy_view(store, []), _turns(store.ops)
    for op in store.ops:
        if op.batch.startswith(SNAPSHOT_BATCH):
            continue        # a fold re-expresses ops that were authorized when written
        if op.conflict is None and _refusal(roles, view, op, store.ops, turns):
            out[op.actor] = out.get(op.actor, 0) + 1
    return out


def _turns(known: list[Op]) -> list[tuple[str, str, str]]:
    """Every applied verdict and reopen, ``(at, kind, pid)``, by clock."""
    return sorted((o.at, o.kind, str(o.target.get("pid"))) for o in known if o.kind in K.TURNS and o.conflict is None)


def _refusal(roles: Roles, view: Store, op: Op, known: list[Op], turns) -> str | None:
    """Why the table refuses ``op``, or None, with the plan a reject names put
    as it stood when the reject was written (``_as_it_stood``)."""
    if op.kind == K.PLAN_REJECT:
        _as_it_stood(view, op, known, turns)
    return roles.policy(view, op)


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


DECIDED = {K.PLAN_ACCEPT: "accepted", K.PLAN_REJECT: "rejected"}


def _last_turn(turns: list[tuple[str, str, str]], pid: str, at: str) -> str | None:
    """The kind of plan ``pid``'s latest verdict or reopen before clock ``at``."""
    last = None
    for when, kind, p in turns:
        if when >= at:
            break
        if p == pid:
            last = kind
    return last


def _as_it_stood(view: Store, op: Op, known: list[Op], turns) -> None:
    """Put the plan ``op`` rejects into ``view`` as a withdrawal is judged on it
    (`roles._withdrawing_own_empty_plan`): open unless a verdict came before
    ``op``'s clock, and holding every op tagged with it that a writer recorded
    as applied, at any clock. The applier refuses every tagged op after a
    withdrawal, so an applied one anywhere means the plan was not empty, and a
    forger who backdates the reject gains nothing."""
    pid = str(op.target.get("pid") or "")
    if (plan := view.plans.get(pid)) is not None:
        view.plans[pid] = replace(plan, status=DECIDED.get(_last_turn(turns, pid, op.at) or "", "open"),
                                  ops=[o.id for o in known if o.plan == pid and o.conflict is None
                                       and o.kind not in K.TURNS])


def _ms(stamp: str) -> int:
    try:
        return parse(stamp)[0]
    except (ValueError, AttributeError):
        return 0
