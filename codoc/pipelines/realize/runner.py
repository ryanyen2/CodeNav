"""Realize pipeline: .codoc markup edits → Claude Code → reflect.

When the user edits the .codoc tree and saves, projection sync produces a list
of IntentOps (AmendOp, IntroduceOp, RetireOp, …). This module:

  1. Filters to ops that imply code changes.
  2. Builds a structured natural-language prompt describing the required edits,
     including each feature's bound code locations for context.
  3. Spawns the `claude` CLI in non-interactive (print) mode.
  4. After the subprocess exits, runs `run_reflect_files` on recently modified
     source files so the resulting chunks auto-bind correctly.

The realize pass is deliberately conservative: it does NOT auto-apply proposals
emitted by the post-realize reflect. The user still accepts/rejects them. The
value is in *reducing manual coding work*, not bypassing the review step.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.core.logging import get_logger

_log = get_logger(__name__)

_CLAUDE_TIMEOUT = 300  # seconds


@dataclass
class RealizeResult:
    """Summary of a realize pass."""
    skipped: bool = False           # True when no actionable ops found
    error: str = ""                 # non-empty if something went wrong
    prompt_chars: int = 0           # size of the prompt sent
    claude_exit_code: int | None = None
    claude_stdout: str = ""
    reflected_files: list[str] = field(default_factory=list)
    proposals_emitted: int = 0
    feedback_proposals: int = 0


# ---------------------------------------------------------------------------
# Op classification
# ---------------------------------------------------------------------------


def _is_actionable(op) -> bool:
    """Return True if the op implies a code change Claude should make."""
    from codoc.projection.differ import AmendOp, IntroduceOp, RetireOp
    if isinstance(op, IntroduceOp):
        return True
    if isinstance(op, RetireOp):
        return True
    if isinstance(op, AmendOp) and op.new_fields:
        return True
    return False


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _feature_binding_locations(feature_uuid: str, store) -> list[str]:
    """Return human-readable binding locations for a feature."""
    try:
        bindings = store.list_bindings(feature_uuid)
        return [
            f"{b.anchor.file}::{b.anchor.symbol_path}"
            if b.anchor.symbol_path
            else b.anchor.file
            for b in bindings
        ]
    except Exception:
        return []


def build_realize_prompt(ops: list, store, root_dir: str) -> str:
    """Build the Claude Code prompt describing the code changes to make."""
    from codoc.projection.differ import AmendOp, IntroduceOp, RetireOp

    lines: list[str] = [
        "You are making coordinated code changes based on edits to a codoc feature tree.",
        "Each item below describes a feature whose intent changed or was introduced/retired.",
        "Apply the minimum code changes needed to align the codebase with the new intent.",
        "",
        f"Working directory: {root_dir}",
        "",
        "## Changes to apply:",
        "",
    ]

    for i, op in enumerate(ops, 1):
        if isinstance(op, IntroduceOp):
            lines += [
                f"### {i}. INTRODUCE feature \"{op.title}\"",
                f"Intent: {op.intent}",
            ]
            if op.parent_uuid:
                parent_locs = _feature_binding_locations(op.parent_uuid, store)
                if parent_locs:
                    lines.append(f"Parent feature code locations: {', '.join(parent_locs[:5])}")
            lines += [
                "Action: Implement this new feature in the codebase. Choose the most",
                "appropriate existing file or create a new one. Keep the implementation",
                "minimal and focused on the stated intent.",
                "",
            ]

        elif isinstance(op, RetireOp):
            feature = store.get_feature(op.uuid)
            if feature is None:
                continue
            locs = _feature_binding_locations(op.uuid, store)
            lines += [
                f"### {i}. RETIRE feature \"{feature.slug}\"",
                f"Intent was: {feature.intent or feature.purpose}",
            ]
            if locs:
                lines.append(f"Bound code: {', '.join(locs[:10])}")
            lines += [
                "Action: Remove or refactor the code at these locations. If the code is",
                "used by other features, refactor rather than delete outright.",
                "",
            ]

        elif isinstance(op, AmendOp):
            feature = store.get_feature(op.uuid)
            if feature is None:
                continue
            locs = _feature_binding_locations(op.uuid, store)

            lines.append(f"### {i}. AMEND feature \"{feature.slug}\"")
            if locs:
                lines.append(f"Bound code: {', '.join(locs[:10])}")

            nf = op.new_fields
            if "purpose" in nf:
                lines += [
                    f"New purpose:  {nf['purpose']}",
                    f"Old purpose:  {feature.purpose or '(none)'}",
                ]
            if "rationale" in nf:
                lines += [
                    f"New rationale: {nf['rationale']}",
                    f"Old rationale: {feature.rationale or '(none)'}",
                ]
            if "scenario" in nf:
                lines.append(f"New scenario:\n{nf['scenario']}")

            lines += [
                "Action: Update the code at the bound locations to reflect the new intent.",
                "Focus on correctness of the implementation, not just comments/docs.",
                "",
            ]

    lines += [
        "## Guidelines:",
        "- Make the minimum changes necessary to fulfil each item.",
        "- Preserve existing tests unless they conflict with the new intent.",
        "- Do not add features beyond what is stated above.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude CLI invocation
# ---------------------------------------------------------------------------


def _find_claude() -> str | None:
    """Return the path to the `claude` CLI binary, or None if not found."""
    return shutil.which("claude")


def _spawn_claude(prompt: str, root_dir: str, timeout: int = _CLAUDE_TIMEOUT) -> subprocess.CompletedProcess:
    """Spawn `claude -p <prompt>` and return the completed process."""
    claude = _find_claude()
    if not claude:
        raise FileNotFoundError("claude CLI not found in PATH — is Claude Code installed?")

    return subprocess.run(
        [claude, "-p", prompt, "--dangerously-skip-permissions"],
        cwd=root_dir,
        timeout=timeout,
        text=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Post-realize reflect
# ---------------------------------------------------------------------------


def _recently_modified_files(root_dir: str, since: float, ignore_dirs: frozenset[str]) -> list[str]:
    """Return repo-relative paths of files modified after *since* (epoch seconds)."""
    root = Path(root_dir)
    result: list[str] = []
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in ignore_dirs for part in p.relative_to(root).parts):
            continue
        try:
            if p.stat().st_mtime > since:
                result.append(str(p.relative_to(root)))
        except OSError:
            pass
    return result


_IGNORE_DIRS = frozenset({
    ".git", ".codoc", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", ".eggs",
})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _extract_feature_uuids(ops: list) -> list[str]:
    """Collect feature UUIDs from a list of IntentOps."""
    from codoc.projection.differ import AmendOp, IntroduceOp, RetireOp
    uuids: list[str] = []
    for op in ops:
        if isinstance(op, (AmendOp, RetireOp)):
            uuids.append(op.uuid)
        elif isinstance(op, IntroduceOp) and op.provisional_uuid:
            uuids.append(op.provisional_uuid)
    return uuids


def run_realize(
    ops: list,
    store,
    root_dir: str,
    codoc_dir: str,
    timeout: int = _CLAUDE_TIMEOUT,
    dry_run: bool = False,
) -> RealizeResult:
    """Execute a realize pass for a list of IntentOps.

    Parameters
    ----------
    ops:
        The list of IntentOps produced by ``diff_tree``.
    store:
        Open SQLiteStore for binding/feature lookups.
    root_dir:
        Absolute path to the repository root.
    codoc_dir:
        Absolute path to ``.codoc/``.
    timeout:
        Hard timeout (seconds) for the claude subprocess.
    dry_run:
        If True, build the prompt and log it but do not spawn claude.

    Returns
    -------
    RealizeResult
        Summary of the realize pass.
    """
    actionable = [op for op in ops if _is_actionable(op)]
    if not actionable:
        _log.debug("realize: no actionable ops — skipping")
        return RealizeResult(skipped=True)

    prompt = build_realize_prompt(actionable, store, root_dir)
    _log.info("realize: %d actionable ops, prompt=%d chars", len(actionable), len(prompt))

    if dry_run:
        _log.info("realize: dry_run=True — prompt:\n%s", prompt)
        return RealizeResult(skipped=False, prompt_chars=len(prompt), claude_exit_code=None)

    if not _find_claude():
        _log.warning("realize: claude CLI not found — skipping realize step")
        return RealizeResult(error="claude CLI not found in PATH", prompt_chars=len(prompt))

    before = time.time()
    try:
        proc = _spawn_claude(prompt, root_dir, timeout=timeout)
    except subprocess.TimeoutExpired:
        return RealizeResult(
            error=f"claude timed out after {timeout}s",
            prompt_chars=len(prompt),
        )
    except Exception as exc:
        return RealizeResult(error=str(exc), prompt_chars=len(prompt))

    _log.info(
        "realize: claude exited with code %d (%.1fs)",
        proc.returncode, time.time() - before,
    )

    # Reflect on files modified during the realize session.
    modified = _recently_modified_files(root_dir, since=before, ignore_dirs=_IGNORE_DIRS)

    proposals_emitted = 0
    if modified:
        try:
            from codoc.pipelines.reflective.runner import run_reflect_files
            reflect_result = run_reflect_files(
                root_dir=root_dir,
                codoc_dir=codoc_dir,
                file_paths=modified,
                author="realize",
            )
            proposals_emitted = reflect_result.get("proposals_emitted", 0)
            _log.info(
                "realize: post-reflect on %d files → %d proposals",
                len(modified), proposals_emitted,
            )
        except Exception as exc:
            _log.warning("realize: post-reflect failed: %s", exc)

    # Feedback pass: detect divergences from the feedforward plan.
    feedback_proposals = 0
    feature_uuids = _extract_feature_uuids(actionable)
    if feature_uuids and modified:
        try:
            from codoc.pipelines.reflective.feedback import derive_feedback_proposals
            feedback_proposals = derive_feedback_proposals(
                feature_uuids=feature_uuids,
                modified_files=modified,
                codoc_dir=codoc_dir,
            )
            if feedback_proposals:
                _log.info("realize: feedback pass → %d divergence proposal(s)", feedback_proposals)
        except Exception as exc:
            _log.warning("realize: feedback pass failed: %s", exc)

    return RealizeResult(
        skipped=False,
        prompt_chars=len(prompt),
        claude_exit_code=proc.returncode,
        claude_stdout=proc.stdout[:2000],  # cap for logging
        reflected_files=modified,
        proposals_emitted=proposals_emitted,
        feedback_proposals=feedback_proposals,
    )
