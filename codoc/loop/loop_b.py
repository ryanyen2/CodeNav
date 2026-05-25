"""Loop B — codoc → code.

Parse the edited ``tree.codoc`` → apply proposal verdicts + direct user edits →
for edits that imply a code change, build a directive from the feature's
description + bound symbols and spawn a coding agent (``claude -p``) once → then
re-run Loop A on the files the agent wrote so any under-specified intent surfaces
as a refinement proposal. That re-run is the loop closure.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field

from codoc.agent.base import format_prompt, load_prompt
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_tree_file
from codoc.loop.apply import apply_op
from codoc.loop.loop_a import LoopAResult, run_loop_a
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store, open_store

_SKIP_DIRS = {".git", ".codoc", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache"}


@dataclass
class LoopBResult:
    accepted: int = 0
    rejected: int = 0
    user_edits: int = 0
    directives: list[str] = field(default_factory=list)
    spawned: bool = False
    files_written: list[str] = field(default_factory=list)
    refinement: LoopAResult | None = None
    error: str = ""

    def summary(self) -> str:
        parts = [f"accepted {self.accepted}", f"rejected {self.rejected}", f"edits {self.user_edits}"]
        if self.spawned:
            parts.append(f"agent wrote {len(self.files_written)} files")
        if self.refinement and (self.refinement.proposed or self.refinement.auto):
            parts.append(f"reflect: {self.refinement.summary()}")
        if self.error:
            parts.append(f"error: {self.error}")
        return " · ".join(parts)


def _implies_code(op: NodeOp) -> bool:
    if op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.RETIRE_NODE):
        return True
    return op.kind is NodeOpKind.AMEND and bool(op.description)


def build_directive(op: NodeOp, store: Store) -> str:
    if op.kind is NodeOpKind.ADD_NODE:
        return f'NEW FEATURE: "{op.title}"\n  Intent: {op.description or "(none)"}\n  Implement this feature in the codebase.'
    if op.kind is NodeOpKind.AMEND:
        f = store.get_feature(op.feature_id)
        title = op.title or (f.title if f else op.feature_id)
        binds = [b.symbol_path for b in store.bindings_for_feature(op.feature_id)] if f else []
        loc = ", ".join(binds) if binds else "(no bound code yet)"
        return f'UPDATE FEATURE: "{title}"\n  New intent: {op.description}\n  Bound code: {loc}\n  Align the bound code with the new intent.'
    if op.kind is NodeOpKind.RETIRE_NODE:
        f = store.get_feature(op.feature_id)
        binds = [b.symbol_path for b in store.bindings_for_feature(op.feature_id)] if f else []
        loc = ", ".join(binds) if binds else "(no bound code)"
        return f'RETIRE FEATURE: "{f.title if f else op.feature_id}"\n  Bound code: {loc}\n  Remove or refactor this code so the feature no longer exists.'
    return ""


def build_realize_prompt(directives: list[str], root_dir: str) -> str:
    body = "\n\n".join(f"### {i + 1}. {d}" for i, d in enumerate(directives))
    return format_prompt(load_prompt("realize"), root_dir=root_dir, directives=body)


def _spawn_claude(prompt: str, root_dir: str, *, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions"],
        cwd=root_dir, capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "")[:2000]


def _files_modified_since(root_dir: str, since: float) -> list[str]:
    out: list[str] = []
    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(p) >= since:
                    out.append(os.path.relpath(p, root_dir))
            except OSError:
                pass
    return out


def run_loop_b(
    root_dir: str,
    codoc_dir: str,
    *,
    dry_run: bool = False,
    spawn=_spawn_claude,
    refine=run_loop_a,
    config=None,
) -> LoopBResult:
    store = open_store(codoc_dir)
    try:
        return _apply_edits(store, root_dir, codoc_dir, dry_run=dry_run, spawn=spawn, refine=refine, config=config)
    finally:
        store.close()


def _apply_edits(store, root_dir, codoc_dir, *, dry_run, spawn, refine, config) -> LoopBResult:
    parsed = parse_tree_file(codoc_dir)
    diff = diff_codoc(parsed, store)
    res = LoopBResult()
    directive_ops: list[NodeOp] = []

    # 1. Proposal verdicts.
    for v in diff.verdicts:
        e = store.get_event(v.event_id)
        if e is None:
            continue
        if v.accept:
            apply_op(e.op, store, source="user", applied=True)
            store.delete_event(e.id)
            res.accepted += 1
            if _implies_code(e.op):
                directive_ops.append(e.op)
        else:
            store.delete_event(e.id)
            res.rejected += 1

    # 2. Direct user edits (intentional → applied immediately).
    for op in diff.user_ops:
        apply_op(op, store, source="user", applied=True)
        res.user_edits += 1
        if _implies_code(op):
            directive_ops.append(op)

    res.directives = [build_directive(op, store) for op in directive_ops]
    res.directives = [d for d in res.directives if d]

    if dry_run or not res.directives:
        return res

    # 3. Spawn the coding agent once with all directives.
    prompt = build_realize_prompt(res.directives, root_dir)
    started = time.time()
    try:
        code, _ = spawn(prompt, root_dir)
    except Exception as e:  # subprocess failure, claude missing, timeout
        res.error = str(e)
        return res
    res.spawned = True
    res.files_written = _files_modified_since(root_dir, started)

    # 4. Reflect on what was written — closes the loop.
    if res.files_written:
        res.refinement = refine(root_dir, codoc_dir, file_scope=set(res.files_written), source="loop_b", config=config)
    return res
