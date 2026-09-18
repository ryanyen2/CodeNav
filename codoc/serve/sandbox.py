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
  • ``under_root`` — every path a tool names stays inside the worktree: a
    ``file_path`` or ``path``, and a ``Glob``'s ``pattern``, which is a path
    too. A Read of ``~/.config/gh/hosts.yml`` is one hop
    from the token that opens the PR, and Write is confined to files the
    directive already names, so an unconfined Read is an exfiltration path with
    a publisher attached.
  • ``is_secret_path`` — a Read, Grep or Glob target inside the worktree that
    still looks like a secret (.env, *.key, a token cache) is refused, so the
    repo's own secrets can't be exfiltrated into a PR either. A Grep or Glob
    that names no path searches the worktree, which is the repository the agent
    is here to change.
  • ``can_use_tool`` — the decision itself; ``out_of_scope_changes`` is the
    post-run gate that fails PR creation when the agent wrote outside scope.

This is the policy. The live wiring installs it as the SDK's **PreToolUse
hook** (``realize_agent._run_sandboxed_sdk``), not as ``can_use_tool``: an
``allowed_tools`` entry naming a whole tool auto-approves it before the
permission callback runs, so listing the tools this module guards is what would
stop it from ever being asked about them.
"""
from __future__ import annotations

import fnmatch
import os

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

# Names secrets actually have, not words that appear in a source file's name.
# The rule used to be a substring test, which refused `tokenizer.py` and
# `auth/password_reset.py` — a feature bound to a file named after what it
# implements was then un-buildable for ever, and the agent could not even read
# it. Confinement to the worktree (``under_root``) is what keeps the machine's
# own credentials out of reach; this list is for the repo's own.
_SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "*.keystore", "id_rsa", "id_ed25519",
                 "hosts.yml", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".htpasswd",
                 "credentials", "credentials.*", "secrets", "secrets.*", "*.secrets",
                 "token", "token.*", "*.token", "*.jks", "*.p12", "service-account*.json")
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


def _static_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard — the deepest directory it
    is certainly rooted at. ``/Users/*/.ssh/*`` is a question about ``/Users``."""
    head = pattern.split("*")[0].split("?")[0].split("[")[0]
    return head if head.endswith("/") else head.rsplit("/", 1)[0] + "/"


def under_root(path: str, root: str | None) -> bool:
    """Whether ``path`` resolves inside ``root``. Symlinks are followed first,
    so a link planted in the worktree does not lead out of it. Without a root
    there is no boundary to be inside, and nothing is."""
    if not root or not path:
        return False
    try:
        base = os.path.realpath(root)
        target = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    except (OSError, ValueError):
        return False
    return target == base or target.startswith(base + os.sep)


def is_secret_path(path: str, root: str | None = None) -> bool:
    """True if ``path`` looks like a secret the agent must not read. Read
    repo-relative: the worktree lives under the maintainer's own ``.codoc/``, so
    the absolute path is inside a secret directory by construction and it is the
    worktree's *own* ``.codoc/`` that must stay denied."""
    segs = _segments(_posix(path, root))
    base = segs[-1].lower() if segs else ""
    if any(fnmatch.fnmatch(base, g) for g in _SECRET_GLOBS):
        return True
    return any(d in segs for d in _SECRET_DIRS)  # exact segment, dots kept


def is_denied_edit(path: str, root: str | None = None) -> bool:
    """True if ``path`` is in the never-edit denylist (CI/settings/manifest/secrets)."""
    segs = _segments(_posix(path, root))
    base = segs[-1] if segs else ""
    if base in _DENY_EDIT_FILES:
        return True
    if any(d in segs for d in _DENY_EDIT_DIRS):  # exact segment, dots kept
        return True
    return is_secret_path(path, root)


def edit_allowed(path: str, scope: list[str] | None = None, root: str | None = None) -> bool:
    """Whether the agent may Edit/Write ``path`` (absolute inside ``root``, or
    repo-relative). Always denied if denylisted; a directive's ``scope`` is the
    files its feature is bound to, and the path must be one of them. An empty
    scope permits nothing: a run the server cannot bound is not run at all
    (``worker.pending`` never queues one)."""
    if is_denied_edit(path, root):
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
        # `pattern` too: a Glob takes its target there, and an absolute pattern
        # lists files anywhere. `Read` still refuses them, so what leaks is
        # names — which key files exist under `~/.ssh` — and a boundary that
        # answers "no" to the contents has no business answering "yes" to the
        # listing. Only its static prefix is a path; the glob part is not.
        target = tool_input.get("file_path") or tool_input.get("path") or ""
        if not target:
            pat = str(tool_input.get("pattern") or "")
            if pat.startswith("/") or pat.startswith("~"):
                target = _static_prefix(pat)
    # Every path a tool names is inside the worktree. A tool that names none
    # (a Glob or Grep with no `path`) runs from the worktree, which is the
    # boundary itself.
    if target and not under_root(target, root):
        return False, f"{tool_name} outside the worktree is not permitted"
    # Every tool that can read a file is a read: `Grep` returns the matching
    # lines and `Glob` the names, so a secret `Read` refuses is one they must
    # refuse too. A `Grep` that names no path searches the worktree, which is
    # the repository the agent is here to change.
    if tool_name in ("Read", "Grep", "Glob"):
        if target and is_secret_path(target, root):
            return False, "reading secrets is not permitted"
        return True, ""
    if tool_name in ("Edit", "Write"):
        if not edit_allowed(target, scope, root):
            return False, f"editing {target!r} is out of the directive's scope"
    return True, ""


def tool_policy(scope: list[str] | None = None, root: str | None = None):
    """A scope-bound ``can_use_tool`` for the SDK hook."""
    def predicate(tool_name: str, tool_input: dict | None) -> tuple[bool, str]:
        return can_use_tool(tool_name, tool_input, scope=scope, root=root)

    return predicate
