"""sandbox.py — the enforced realize-sandbox policy (U11).

A remote-triggered realization runs with the maintainer's keys on a git worktree,
so its filesystem + tool scope must be an ENFORCED boundary, not a prompt
instruction. This module is the pure, fully-tested policy:

  • ``ALLOWED_TOOLS`` — a minimal set; Bash (arbitrary shell) is excluded.
  • ``edit_allowed`` — an Edit/Write target must be inside the directive's
    ``Edit only:`` scope AND outside the denylist (.github/, .claude/, .codoc/,
    .mcp.json, the package manifest/lockfiles) — the paths a malicious suggestion
    would use to persist (workflow injection, hook/MCP registration, dependency
    confusion) past PR review.
  • ``is_secret_path`` — a Read target that looks like a secret (.env, *.key, a
    token cache) is refused, so secrets can't be exfiltrated into a PR.
  • ``can_use_tool`` — the predicate the SDK PreToolUse/canUseTool hook calls;
    ``out_of_scope_changes`` is the post-run gate that fails PR creation when the
    agent wrote outside scope.

The live wiring (passing the predicate + ``ALLOWED_TOOLS`` + a server-owned
settings allowlist to the Claude Agent SDK) lives in the realize flow (U7); this
is the policy it enforces.
"""
from __future__ import annotations

import fnmatch

# Read/Edit/Write/Glob/Grep only — no Bash. WebFetch is allowed only through the
# SSRF-hardened Consult path (U8), gated separately.
ALLOWED_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")

_DENY_EDIT_DIRS = (".github", ".claude", ".codoc")
_DENY_EDIT_FILES = frozenset({
    ".mcp.json", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "uv.lock", "Cargo.lock", "Gemfile.lock",
    # Files that run on someone else's machine after review, or decide what
    # the build installs: the same reason as the manifests above.
    "conftest.py", "noxfile.py", "tox.ini", "Makefile", "Dockerfile",
    ".pre-commit-config.yaml", ".gitlab-ci.yml", ".gitmodules", ".gitattributes",
    ".npmrc", ".yarnrc", ".yarnrc.yml", "requirements.txt", "requirements-dev.txt",
})

_SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "*.keystore", "id_rsa", "id_ed25519")
_SECRET_SUBSTRINGS = ("token", "secret", "credential", "password")
# `.codoc` (the control dir, dot kept) holds the App/token caches → never readable
# by the agent. NOT `codoc` (the source package), which the agent legitimately edits.
_SECRET_DIRS = (".codoc",)


def _segments(path: str) -> list[str]:
    return [s for s in str(path).replace("\\", "/").split("/") if s and s != "."]


def _posix(path: str, root: str | None = None) -> str:
    """A path as the scope names it: repo-relative. The agent's tools send
    absolute paths inside the worktree, so ``root`` is stripped first."""
    segs = _segments(path)
    if root:
        r = _segments(root)
        if segs[:len(r)] == r:
            segs = segs[len(r):]
    return "/".join(segs)


def is_secret_path(path: str) -> bool:
    """True if ``path`` looks like a secret the agent must not read."""
    segs = _segments(path)
    base = segs[-1].lower() if segs else ""
    if any(fnmatch.fnmatch(base, g) for g in _SECRET_GLOBS):
        return True
    lowered = _posix(path).lower()
    if any(s in lowered for s in _SECRET_SUBSTRINGS):
        return True
    return any(d in segs for d in _SECRET_DIRS)  # exact segment, dots kept


def is_denied_edit(path: str) -> bool:
    """True if ``path`` is in the never-edit denylist (CI/settings/manifest/secrets)."""
    segs = _segments(path)
    base = segs[-1] if segs else ""
    if base in _DENY_EDIT_FILES:
        return True
    if any(d in segs for d in _DENY_EDIT_DIRS):  # exact segment, dots kept
        return True
    return is_secret_path(path)


def edit_allowed(path: str, scope: list[str] | None = None, root: str | None = None) -> bool:
    """Whether the agent may Edit/Write ``path`` (absolute inside ``root``, or
    repo-relative). Always denied if denylisted; a directive's ``scope`` is the
    files its feature is bound to, and the path must be one of them. An empty
    scope permits nothing: a run the server cannot bound is not run at all
    (``worker.pending`` never queues one)."""
    if is_denied_edit(path):
        return False
    return _posix(path, root) in {_posix(s) for s in (scope or ())}


def out_of_scope_changes(changed: list[str], scope: list[str] | None = None, root: str | None = None) -> list[str]:
    """Post-run gate: the changed files that fall outside what was permitted. A
    non-empty result fails PR creation."""
    return [c for c in changed if not edit_allowed(c, scope, root)]


def can_use_tool(tool_name: str, tool_input: dict | None, *,
                 scope: list[str] | None = None, root: str | None = None) -> tuple[bool, str]:
    """The PreToolUse/canUseTool decision: (allowed, reason)."""
    if tool_name not in ALLOWED_TOOLS:
        return False, f"tool '{tool_name}' is not permitted for remote-originated realization"
    target = ""
    if tool_input:
        target = tool_input.get("file_path") or tool_input.get("path") or ""
    if tool_name == "Read":
        if target and is_secret_path(target):
            return False, "reading secrets is not permitted"
        return True, ""
    if tool_name in ("Edit", "Write"):
        if not edit_allowed(target, scope, root):
            return False, f"editing {target!r} is out of the directive's scope"
        return True, ""
    return True, ""


def tool_policy(scope: list[str] | None = None, root: str | None = None):
    """A scope-bound ``can_use_tool`` for the SDK hook."""
    def predicate(tool_name: str, tool_input: dict | None) -> tuple[bool, str]:
        return can_use_tool(tool_name, tool_input, scope=scope, root=root)

    return predicate
