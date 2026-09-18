"""The hub's realize worker: a hand-off becomes a pull request.

On the maintainer's machine a hand-off is built by the person's own session
(`/codoc:sync`). On the hub nobody is at a terminal, so the server decides:
every feature whose hand-off is not yet covered by a realization, and whose
hand-off has no pull request yet, is realized **off the live tree** on its own
git worktree and branch, by an agent under the enforced sandbox
(`realize_agent`: no shell, edits confined to the feature's bound files,
secrets unreadable, GitHub tokens scrubbed from its environment), and only the
orchestrator — which holds the token — opens the PR.

Nothing is written to the store here. A directive is realized when the code
lands and a reflect pass under a closed session says so (`loops.realize.settle`),
which for the hub means: the PR merges, the maintainer's daemon indexes it. The
worker's own ledger (`cache/hub-prs.json`, ignored) records which hand-off ids
already have a PR, so a queue that stays queued does not open a second one.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus.derive import directive, title
from codoc.loops.realize import queued
from codoc.serve.realize_agent import make_sandboxed_agent
from codoc.serve.sandbox import out_of_scope_changes
from codoc.substrate import client

LEDGER = "cache/hub-prs.json"
# A hand-off whose branch carries the agent's commit but has no pull request
# yet: `built:<branch>:<attempts>`. The next pass resumes from the push rather
# than paying for another model session.
BUILT = "built:"
MAX_ATTEMPTS = 5


def branch_name(handoff_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in handoff_id)
    return f"codoc/realize-{safe}"


def pr_create_command(branch: str, title_text: str, body: str, *, base: str = "main") -> list[str]:
    """``gh pr create`` argv: always a feature branch against ``base``, never a
    push to the base branch."""
    return ["gh", "pr", "create", "--base", base, "--head", branch, "--title", title_text, "--body", body]


def read_ledger(codoc_dir: str | Path) -> dict[str, str]:
    try:
        d = json.loads((Path(codoc_dir) / LEDGER).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_pr(codoc_dir: str | Path, handoff_id: str, branch: str) -> None:
    path = Path(codoc_dir) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    table = read_ledger(codoc_dir)
    table[handoff_id] = branch
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


@dataclass
class Request:
    """One queued feature, as the worker sees it."""
    fid: str
    handoff: str
    title: str
    text: str
    scope: list[str] = field(default_factory=list)


def pending(codoc_dir: str | Path, store, done: dict[str, str] | None = None) -> list[Request]:
    """The hand-offs this hub may build. A feature with no bindings is not one:
    there is no set of files to confine the agent to, and a contributor's
    accepted proposal must never be a licence to write anywhere. Those stay in
    the queue for the maintainer, who builds them with a person present."""
    done = read_ledger(codoc_dir) if done is None else done
    out = []
    for fid in queued(store):
        w = store.watermarks(fid)
        hid = w.handoffs[-1] if w.handoffs else ""
        scope = sorted({b.file for b in store.bindings.values() if b.feature == fid})
        answer = done.get(hid, "")
        if not hid or (answer and not answer.startswith(BUILT)) or not scope:
            continue
        out.append(Request(fid, hid, title(store, fid, "rejected") or fid, directive(store, fid), scope))
    return out


@dataclass
class Result:
    ok: bool
    branch: str
    reason: str = ""
    out_of_scope: list[str] = field(default_factory=list)
    transient: bool = False      # git or gh failed: worth another interval, not a verdict on the request
    built: bool = False          # the agent ran and its work is committed: retry from the push, not the model


def realize_one(req: Request, worktree: str, *, run, agent, base: str = "main", root: str | None = None,
                resume: bool = False) -> Result:
    """Worktree → sandboxed agent → scope gate → commit → push → PR. Every git
    and gh call is checked: a worktree git refused is not a worktree to run an
    agent in, and a pull request that was not opened is not a pull request.
    ``run(argv, cwd=None) -> returncode``; ``agent(directive, worktree, scope)``
    returns the files it changed.

    ``resume`` picks the work up at the push: the agent already ran and its
    commit is on the branch, and asking the model again would spend another
    session to produce a different diff for the same request."""
    branch = branch_name(req.handoff)
    if resume:
        if run(["git", "worktree", "add", worktree, branch], cwd=root):
            return Result(False, branch, "git could not re-create the worktree for the built branch",
                          transient=True, built=True)
        return _publish(req, worktree, branch, base, run)
    if run(["git", "worktree", "add", "-b", branch, worktree], cwd=root):
        # The branch may be there already: a hub killed between the agent's
        # commit and the ledger write left the work on it and no record of it.
        # Building again would spend another model session on the same request
        # and, worse, `_cleanup` would delete the commit first — this pass is
        # classed not-built, so it does not keep the branch. The branch existing
        # IS the record: resume at the push.
        if run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=root) == 0:
            return realize_one(req, worktree, run=run, agent=agent, base=base, root=root, resume=True)
        return Result(False, branch, "git refused to create the worktree or branch", transient=True)
    changed = agent({"text": req.text, "id": req.handoff, "title": req.title}, worktree, scope=req.scope)
    if changed is None:
        # The sandbox could not be enforced, so nothing ran. That is not a
        # refusal of this request; burying it would refuse every one for ever.
        return Result(False, branch, "the sandbox could not be enforced, so nothing ran", transient=True)
    bad = out_of_scope_changes(changed, req.scope, worktree)
    if bad:
        return Result(False, branch, "the agent wrote outside the feature's bindings", bad)
    if not changed:
        return Result(False, branch, "no changes produced")
    if run(["git", "add", "-A"], cwd=worktree) or run(["git", "commit", "-m", f"codoc: {req.title}"], cwd=worktree):
        return Result(False, branch, "git refused to commit the agent's work", transient=True)
    return _publish(req, worktree, branch, base, run)


def _publish(req: Request, worktree: str, branch: str, base: str, run) -> Result:
    """The half that can be retried without the model: push, then ask for the
    pull request."""
    # `gh pr create --head` "explicitly skips any forking or pushing behavior",
    # so the branch has to be on the remote before we ask for the pull request.
    if run(["git", "push", "-u", "origin", branch], cwd=worktree):
        return Result(False, branch, "git could not push the branch", transient=True, built=True)
    body = (f"Realizes the hand-off `{req.handoff}` on feature **{req.title}** (`{req.fid}`).\n\n"
            "Opened by the codoc hub from a hand-off in the tree. Review as usual; "
            "the tree marks the request realized once this lands and the daemon indexes it.")
    if run(pr_create_command(branch, f"codoc: {req.title}", body, base=base), cwd=worktree):
        return Result(False, branch, "gh could not open the pull request", transient=True, built=True)
    return Result(True, branch)


def _run(argv, cwd=None) -> int:
    return subprocess.run(argv, cwd=cwd, check=False).returncode


def _published_head(codoc_dir: str) -> int | None:
    """The store position the daemon last published, or None when unreadable."""
    try:
        return int(json.loads((Path(codoc_dir) / "status.json").read_text(encoding="utf-8"))["head"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def process_ready(root_dir: str, codoc_dir: str, *, base: str = "main", run=_run, agent=None,
                  store=None, printer=print, retry: list[str] | None = None) -> list[str]:
    """Realize every ready hand-off once; returns the hand-off ids that got a PR.
    Hand-offs left unanswered by a transient failure are appended to ``retry``,
    which is how the worker knows to come back to them before the tree moves."""
    store = store if store is not None else client.read(codoc_dir).store
    ledger = read_ledger(codoc_dir)
    reqs = pending(codoc_dir, store, ledger)
    if not reqs:
        return []
    agent = agent or make_sandboxed_agent()
    done: list[str] = []
    for req in reqs:
        worktree = str(Path(codoc_dir) / "worktrees" / branch_name(req.handoff).replace("/", "-"))
        tried = _attempts(ledger.get(req.handoff, ""))
        try:
            res = realize_one(req, worktree, run=run, agent=agent, base=base, root=root_dir, resume=tried > 0)
        except Exception as exc:  # noqa: BLE001 — one bad request never wedges the loop
            printer(f"  ⚠ hub realize {req.fid} failed: {exc}")
            _cleanup(worktree, branch_name(req.handoff), run, root_dir)
            continue
        if res.ok:
            record_pr(codoc_dir, req.handoff, res.branch)
            done.append(req.handoff)
            printer(f"  ✓ hub opened a PR on {res.branch} for {req.title!r}")
        elif res.out_of_scope:
            # A write outside scope is refused, recorded, and not retried: the same
            # unsafe directive would do the same thing again.
            record_pr(codoc_dir, req.handoff, "refused: out of scope")
            printer(f"  ✗ hub realize {req.fid} wrote out of scope ({', '.join(res.out_of_scope)}) — no PR")
        elif res.transient and res.built and tried + 1 >= MAX_ATTEMPTS:
            # The work is done and the remote will not take it. Stop paying for
            # attempts and say what stopped, with the branch still there.
            record_pr(codoc_dir, req.handoff, f"refused after {tried + 1} attempts: {res.reason}")
            printer(f"  ✗ hub realize {req.fid}: {res.reason} — gave up after {tried + 1}; branch {res.branch} kept")
        elif res.transient:
            # git or gh failed, or the sandbox could not run: the request was
            # never answered, so the next interval tries again rather than
            # burying it in the ledger. When the agent's work is already
            # committed, the branch stays and the retry starts at the push —
            # asking the model again would spend another session on the same
            # request.
            printer(f"  ⚠ hub realize {req.fid}: {res.reason}; will retry")
            if res.built:
                record_pr(codoc_dir, req.handoff, f"{BUILT}{res.branch}:{tried + 1}")
            if retry is not None:
                retry.append(req.handoff)
        else:
            # A run that produced nothing produces nothing again; a person hands
            # the feature off anew when they want another try.
            record_pr(codoc_dir, req.handoff, f"refused: {res.reason}")
            printer(f"  · hub realize {req.fid}: {res.reason}")
        _cleanup(worktree, res.branch, run, root_dir, keep_branch=res.built)
    return done


def _attempts(answer: str) -> int:
    """How many times this hand-off's branch has been built and not published."""
    if not answer.startswith(BUILT):
        return 0
    tail = answer.rpartition(":")[2]
    return int(tail) if tail.isdigit() else 1


def _cleanup(worktree: str, branch: str, run, root: str | None = None, *, keep_branch: bool = False) -> None:
    """Leave no worktree behind: the next attempt must be able to create one by
    the same name. The branch goes with it, unless it carries work the agent has
    already done and only the publishing failed."""
    argvs = [["git", "worktree", "remove", "--force", worktree]]
    if not keep_branch:
        argvs.append(["git", "branch", "-D", branch])
    for argv in argvs:
        try:
            run(argv, cwd=root)
        except Exception:  # noqa: BLE001 — best effort
            pass


class Worker:
    """A background thread that calls :func:`process_ready` on an interval."""

    def __init__(self, root_dir: str, codoc_dir: str, *, base: str = "main", interval: float = 15.0, printer=print) -> None:
        self.root_dir, self.codoc_dir, self.base, self.interval, self.printer = root_dir, codoc_dir, base, interval, printer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._head: int | None = None
        self._unanswered = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Replaying the log to find nothing is the common case, and the
                # published head says when there is nothing to find — but a
                # transient failure left a request unanswered, and that one is
                # retried whether or not anybody has edited the tree since
                # (`docs/serve-deployment.md`: "a git or gh failure is not a
                # refusal and is retried").
                head = _published_head(self.codoc_dir)
                if head != self._head or self._unanswered:
                    self._head = head
                    again: list[str] = []
                    process_ready(self.root_dir, self.codoc_dir, base=self.base, printer=self.printer, retry=again)
                    self._unanswered = bool(again)
            except Exception as exc:  # noqa: BLE001 — the worker thread never dies
                self.printer(f"  ⚠ hub realize worker: {exc}")
            self._stop.wait(self.interval)

    def start(self) -> "Worker":
        self._thread = threading.Thread(target=self._loop, name="codoc-hub-realize", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
