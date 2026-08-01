"""realize_agent.py — the ENFORCED sandbox for hub-triggered realization (U7/U11 wiring).

``sandbox.py`` is the pure policy (allowed tools, scope, secret-read/edit denylist);
``consult.py`` is the pure SSRF-hardened Consult-URL gate. This module is the live
wiring that binds BOTH onto a Claude-Agent-SDK run so a remote-authored directive can
only touch what it is allowed to:

  • tool use goes through ``realize_tool_policy`` — ``sandbox.can_use_tool`` for
    everything, plus a WebFetch branch that defers to ``consult_url_allowed`` (default
    allowlist EMPTY → every WebFetch denied) so a Consult link cannot reach the LAN or a
    cloud metadata endpoint;
  • the agent runs with ``HUB_ALLOWED_TOOLS`` (Read/Edit/Write/Glob/Grep + a
    consult-gated WebFetch — NO Bash) and its environment SCRUBBED of every GitHub
    token, so it can never push or authenticate as the maintainer (the orchestrator in
    ``realize_pr.py`` holds the token and opens the PR);
  • FAIL-SAFE: if the SDK cannot be loaded or the canUseTool hook cannot be installed,
    the agent runs NOTHING and reports zero changes rather than falling back to an
    unsandboxed run.

The tool policy and the git change-detection are pure (tested here); the SDK call is
the thin live edge.
"""
from __future__ import annotations

import os
from typing import Callable

from codoc.serve import sandbox
from codoc.serve.consult import consult_url_allowed

# The sandbox's minimal set PLUS a consult-gated WebFetch (sandbox.ALLOWED_TOOLS omits
# WebFetch precisely because it must go through the consult gate, wired below).
HUB_ALLOWED_TOOLS = (*sandbox.ALLOWED_TOOLS, "WebFetch")


def realize_tool_policy(scope, *, consult_allowlist, resolve) -> Callable[[str, dict | None], "tuple[bool, str]"]:
    """The (allowed, reason) decision for one tool call, combining the sandbox policy
    with the consult-URL gate. Pure — the single enforcement point for a remote run."""
    base = sandbox.tool_policy(scope)

    def predicate(tool_name: str, tool_input: dict | None) -> "tuple[bool, str]":
        if tool_name == "WebFetch":
            url = (tool_input or {}).get("url") or ""
            ok, reason = consult_url_allowed(url, consult_allowlist, resolve=resolve)
            return (True, "") if ok else (False, f"consult blocked: {reason}")
        return base(tool_name, tool_input)

    return predicate


def consult_allowlist_from_env(environ: dict | None = None) -> frozenset:
    """The Consult-URL host allowlist — comma-separated in ``CODOC_CONSULT_ALLOWLIST``,
    default EMPTY (every WebFetch denied). A maintainer opts specific doc hosts in."""
    env = environ if environ is not None else os.environ
    raw = env.get("CODOC_CONSULT_ALLOWLIST", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


_GH_TOKEN_KEYS = ("CODOC_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_APP_PRIVATE_KEY")


def changed_files(worktree_path: str, *, run_capture) -> list[str]:
    """Repo-relative paths the agent modified in ``worktree_path`` (``git status
    --porcelain``). ``run_capture(argv, cwd) -> str`` is injected for testing."""
    out = run_capture(["git", "status", "--porcelain"], worktree_path) or ""
    files: list[str] = []
    for line in out.splitlines():
        line = line.rstrip("\n")
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:  # a rename: "old -> new" — take the destination
            path = path.split(" -> ", 1)[1]
        files.append(path.strip().strip('"'))
    return files


def _default_run_capture(argv, cwd) -> str:
    import subprocess

    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False).stdout


def build_directive_prompt(directive: dict, scope) -> str:
    """The prompt the sandboxed agent implements — the directive text plus an explicit
    scope note (belt-and-braces alongside the ENFORCED canUseTool policy)."""
    text = (directive.get("text") or "").strip() or "Implement the requested change."
    lines = [text, ""]
    if scope:
        lines.append("Edit only these files: " + ", ".join(scope) + ".")
    lines.append("You may not run shell commands, read secrets, or edit CI/settings/"
                 "manifest files; those tools are blocked.")
    return "\n".join(lines)


def make_sandboxed_agent(*, consult_allowlist=None, resolve=None, run_capture=None):
    """Return an ``agent(directive, worktree_path, scope) -> list[str]`` for
    :func:`codoc.serve.realize_pr.realize_directive`.

    The returned agent runs the directive on a Claude-Agent-SDK session bound to
    :func:`realize_tool_policy` and ``HUB_ALLOWED_TOOLS``, in a token-scrubbed env, and
    returns the files it changed. FAIL-SAFE: any inability to enforce the sandbox (SDK
    missing, hook unsupported) yields an empty change list — never an unsandboxed run."""
    allowlist = consult_allowlist if consult_allowlist is not None else consult_allowlist_from_env()
    resolver = resolve or _default_resolve
    capture = run_capture or _default_run_capture

    def agent(directive: dict, worktree_path: str, scope=None) -> list[str]:
        policy = realize_tool_policy(scope, consult_allowlist=allowlist, resolve=resolver)
        ran = _run_sandboxed_sdk(
            prompt=build_directive_prompt(directive, scope),
            worktree_path=worktree_path,
            policy=policy,
        )
        if not ran:
            return []  # fail-safe: sandbox not enforceable → produced nothing
        return changed_files(worktree_path, run_capture=capture)

    return agent


def _default_resolve(host: str) -> list[str]:
    import socket

    return [ai[4][0] for ai in socket.getaddrinfo(host, None)]


def _run_sandboxed_sdk(*, prompt: str, worktree_path: str, policy) -> bool:
    """Run one directive on the Claude Agent SDK with the sandbox hook installed.
    Returns True if the session ran (under the enforced policy), False if the sandbox
    could not be installed (SDK missing / unsupported) so the caller fails safe."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except Exception:  # noqa: BLE001 — SDK not installed → cannot sandbox → refuse
        return False

    async def can_use_tool(tool_name, tool_input, *_a, **_k):
        allowed, reason = policy(tool_name, tool_input if isinstance(tool_input, dict) else {})
        return _permission_result(allowed, reason)

    try:
        options = ClaudeAgentOptions(
            cwd=worktree_path,
            allowed_tools=list(HUB_ALLOWED_TOOLS),
            permission_mode="default",  # so the canUseTool hook is consulted
            can_use_tool=can_use_tool,
        )
    except TypeError:
        # This SDK build does not accept a canUseTool hook — we cannot guarantee the
        # boundary, so refuse rather than run unsandboxed.
        return False

    import asyncio

    async def _drive() -> None:
        async for _msg in query(prompt=prompt, options=options):
            pass

    # Scrub GitHub credentials from the env the SDK's agent subprocess inherits, so the
    # agent can never authenticate/push as the maintainer (the orchestrator holds the
    # token and opens the PR). Restored after the run — the orchestrator's `gh` calls
    # in realize_pr run later, in the hub process, and still need it.
    saved = {k: os.environ.pop(k) for k in _GH_TOKEN_KEYS if k in os.environ}
    try:
        asyncio.run(_drive())
    except Exception:  # noqa: BLE001 — a run failure just means no (or partial) changes;
        pass           # the post-run out-of-scope gate + git diff still decide the PR
    finally:
        os.environ.update(saved)
    return True


def _permission_result(allowed: bool, reason: str):
    """Adapt (allowed, reason) to whatever permission-result shape the installed SDK
    expects; fall back to a truthy/falsey duck-typed object."""
    try:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        return PermissionResultAllow() if allowed else PermissionResultDeny(message=reason)
    except Exception:  # noqa: BLE001 — older/newer SDK: return a duck-typed decision
        class _Result:
            def __init__(self, allow: bool, message: str):
                self.allow = allow
                self.behavior = "allow" if allow else "deny"
                self.message = message

            def __bool__(self):
                return self.allow

        return _Result(allowed, reason)
