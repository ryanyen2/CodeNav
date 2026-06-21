"""realize_pr.py — realize a directive on a worktree and open a code PR (U7).

When an authorized hand-off makes a directive ready, the hub realizes it OFF the
live working tree (a dedicated git worktree + feature branch named from the
directive id), runs the agent in the U11 sandbox, gates the result against the
directive scope, then — from the ORCHESTRATOR, not the agent — commits and opens
a PR against the base branch. Invariants:

  • the agent process holds NO GitHub token (the orchestrator runs ``gh`` with the
    scoped installation token in its own env);
  • code lands on a feature branch as a PR — never a push to the base branch;
  • a write outside the directive scope ABORTS PR creation (U11 post-run gate).

``run`` (a subprocess runner) and ``agent`` (the sandboxed realize call) are
injected, so the orchestration sequence + safety gates are testable without git,
gh, or the SDK. The live wiring (a real subprocess runner + a Claude-Agent-SDK
agent bound to ``sandbox.tool_policy(scope)``) is deployment config.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.serve.sandbox import out_of_scope_changes


def branch_name(directive_id: str) -> str:
    """A safe feature-branch name from the directive id (the ``d-…`` id)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in directive_id)
    return f"codoc/realize-{safe}"


def worktree_add_command(worktree_path: str, branch: str) -> list[str]:
    return ["git", "worktree", "add", "-b", branch, worktree_path]


def pr_create_command(branch: str, title: str, body: str, *, base: str = "main") -> list[str]:
    """``gh pr create`` argv — always ``--head <branch>`` against ``--base``; never
    a direct push to the base branch."""
    return ["gh", "pr", "create", "--base", base, "--head", branch,
            "--title", title, "--body", body]


def _pr_body(directive: dict) -> str:
    fid = directive.get("feature_id") or ""
    did = directive.get("id") or ""
    return (f"Realizes codoc directive `{did}`"
            + (f" for feature `{fid}`" if fid else "")
            + ".\n\nOpened by the codoc hub from a hand-off. Review as usual.")


@dataclass
class RealizeResult:
    ok: bool
    branch: str
    reason: str = ""
    out_of_scope: list[str] = field(default_factory=list)


def realize_directive(
    directive: dict,
    worktree_path: str,
    *,
    run,
    agent,
    scope: list[str] | None = None,
    base: str = "main",
) -> RealizeResult:
    """Orchestrate one directive's realization. See module docstring for invariants.

    ``run(argv, cwd=None) -> int`` runs git/gh; ``agent(directive, worktree_path,
    scope) -> list[str]`` runs the sandboxed agent and returns the files it changed.
    The agent is never handed a token — only ``run`` (the orchestrator) is."""
    branch = branch_name(directive.get("id") or "")
    run(worktree_add_command(worktree_path, branch))

    changed = agent(directive, worktree_path, scope=scope) or []

    bad = out_of_scope_changes(changed, scope)
    if bad:
        # U11 post-run gate: a write outside scope aborts before any PR is opened.
        return RealizeResult(ok=False, branch=branch,
                             reason="agent wrote outside the directive scope",
                             out_of_scope=bad)

    if not changed:
        return RealizeResult(ok=False, branch=branch, reason="no changes produced")

    run(["git", "add", "-A"], cwd=worktree_path)
    run(["git", "commit", "-m", f"codoc: realize {directive.get('id') or ''}"],
        cwd=worktree_path)
    title = directive.get("title") or f"codoc: realize {directive.get('id') or ''}"
    run(pr_create_command(branch, title, _pr_body(directive), base=base),
        cwd=worktree_path)
    return RealizeResult(ok=True, branch=branch)
