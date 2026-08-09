"""The two artifacts under comparison.

Both are produced from the same checkout, with its history present, by the same
model tier. What differs is the method: one summarizes a codebase into a file,
the other maintains a feature tree and goes looking for where reasons were
written down. Holding the inputs identical is what makes the comparison about
that difference rather than about access.

The baseline is generated, not borrowed. A repository's existing hand-written
``CLAUDE.md`` varies from excellent to absent, and using whatever each project
happens to ship would make the baseline arm a survey of other people's
diligence. ``claude /init`` is also what a user actually gets on day one, which
is the comparison a reader of the paper cares about.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# `/init` writes a file, so the headless run needs edit permission. Kept as one
# constant because it is the part most likely to need adjusting as the CLI
# moves, and a silently failing baseline arm would read as a codoc win.
CLAUDE_INIT_ARGV = [
    "claude", "-p", "/init",
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read,Glob,Grep,Write,Edit,Bash(git log:*)",
]
CLAUDE_INIT_TIMEOUT_S = 900


@dataclass
class Artifact:
    arm: str          # "codoc" | "claude_md"
    text: str
    path: Path
    ok: bool = True
    detail: str = ""


def _preexisting_aside(repo: Path) -> Path | None:
    """Move any shipped CLAUDE.md out of the way, so the baseline is generated
    rather than found — and so `/init` does not merely reformat someone else's
    work and get credited for it."""
    existing = repo / "CLAUDE.md"
    if not existing.exists():
        return None
    stashed = repo / "CLAUDE.md.preexisting"
    shutil.move(str(existing), str(stashed))
    return stashed


def build_claude_md(repo: Path, *, timeout_s: int = CLAUDE_INIT_TIMEOUT_S) -> Artifact:
    """Run `claude /init` in ``repo`` and collect the CLAUDE.md it writes."""
    _preexisting_aside(repo)
    target = repo / "CLAUDE.md"
    try:
        proc = subprocess.run(CLAUDE_INIT_ARGV, cwd=str(repo), capture_output=True,
                              text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        return Artifact("claude_md", "", target, ok=False, detail=f"{type(exc).__name__}: {exc}")
    if not target.exists():
        return Artifact("claude_md", "", target, ok=False,
                        detail=f"/init wrote no CLAUDE.md (rc={proc.returncode}): "
                               f"{(proc.stderr or proc.stdout or '')[-400:]}")
    return Artifact("claude_md", target.read_text(encoding="utf-8"), target)


def build_codoc(repo: Path, *, subdir: str = "", timeout_s: int = 3600,
                max_files: int | None = None) -> Artifact:
    """Run `codoc init` in ``repo`` and collect the rendered tree.

    ``subdir`` narrows indexing to the package under evaluation so the two arms
    describe the same material. ``max_files`` caps LLM spend on a large corpus;
    when set it is recorded on the artifact, because a truncated tree is a
    different artifact and must not be reported as a complete one.
    """
    root = repo / subdir if subdir else repo
    env = dict(os.environ)
    if max_files:
        env["CODOC_BOOTSTRAP_MAX_FILES"] = str(max_files)
    tree = root / ".codoc" / "tree.codoc"
    try:
        proc = subprocess.run(["codoc", "init"], cwd=str(root), capture_output=True,
                              text=True, timeout=timeout_s, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return Artifact("codoc", "", tree, ok=False, detail=f"{type(exc).__name__}: {exc}")
    if not tree.exists():
        return Artifact("codoc", "", tree, ok=False,
                        detail=f"init wrote no tree (rc={proc.returncode}): "
                               f"{(proc.stderr or proc.stdout or '')[-400:]}")
    detail = f"capped at {max_files} files" if max_files else ""
    return Artifact("codoc", tree.read_text(encoding="utf-8"), tree, detail=detail)


def build_all(repo: Path, *, subdir: str = "", max_files: int | None = None) -> list[Artifact]:
    """Both arms, baseline first.

    Order matters: `/init` runs before `codoc init` creates ``.codoc/``, so the
    baseline never sees codoc's own output sitting in the tree and describe it.
    """
    return [
        build_claude_md(repo),
        build_codoc(repo, subdir=subdir, max_files=max_files),
    ]
