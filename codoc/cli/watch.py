"""codoc watch — FS watcher daemon.

Watches two sets of paths:

  Code files (non-.codoc)
    → debounced 500 ms → run_reflect_files

  .codoc/tree/*.codoc files
    → sync_from_dir → if actionable ops → run_realize

Usage: codoc watch [--root ROOT] [--no-realize]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

_IGNORE_DIRS = frozenset({
    ".git", ".codoc", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", ".eggs",
})

_CODOC_SUFFIX = ".codoc"
_CODOC_TREE_PREFIX = ".codoc/tree/"


def _resolve_paths(root: Optional[str], codoc_dir: Optional[str]) -> tuple[str, str]:
    """Resolve root_dir and codoc_dir from options or CWD."""
    cwd = Path.cwd()
    root_path = Path(root) if root else cwd
    codoc_path = Path(codoc_dir) if codoc_dir else root_path / ".codoc"
    return str(root_path.resolve()), str(codoc_path.resolve())


def _is_ignored(rel: Path) -> bool:
    return any(part in _IGNORE_DIRS for part in rel.parts)


def _is_codoc_tree_file(rel_str: str) -> bool:
    return rel_str.startswith(_CODOC_TREE_PREFIX) and rel_str.endswith(_CODOC_SUFFIX)


def watch_command(
    root: Optional[str] = typer.Option(None, "--root", help="Repository root (default: cwd)"),
    codoc_dir: Optional[str] = typer.Option(None, "--codoc-dir", help="Path to .codoc directory"),
    no_realize: bool = typer.Option(False, "--no-realize", help="Disable realize on .codoc changes"),
    dry_run_realize: bool = typer.Option(False, "--dry-realize", help="Build realize prompt but do not spawn claude"),
) -> None:
    """Watch for code and .codoc changes; auto-reflect and realize."""
    from watchfiles import watch as _fs_watch, Change
    from codoc.pipelines.reflective.runner import run_reflect_files
    from codoc.projection.sync import sync_from_dir

    root_dir, cd = _resolve_paths(root, codoc_dir)

    if not Path(cd).exists():
        typer.echo(f"Error: {cd!r} does not exist — run `codoc init` first.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Watching {root_dir} …")
    typer.echo(f"  Code changes → reflect   |   .codoc changes → sync{'' if no_realize else ' + realize'}")
    typer.echo("Press Ctrl+C to stop.")

    try:
        for raw_changes in _fs_watch(root_dir, debounce=500, ignore_permission_denied=True):
            code_files: list[str] = []
            codoc_changed = False

            for _change_type, path_str in raw_changes:
                path = Path(path_str)
                try:
                    rel = path.relative_to(root_dir)
                except ValueError:
                    continue

                rel_str = str(rel)

                if _is_codoc_tree_file(rel_str):
                    codoc_changed = True
                elif not _is_ignored(rel):
                    # Only track source-code-like files for reflect.
                    if path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h"}:
                        code_files.append(rel_str)

            # --- Route 1: code file changes → reflect ---
            if code_files:
                typer.echo(f"  ↪ reflect({len(code_files)} files)")
                try:
                    result = run_reflect_files(
                        root_dir=root_dir,
                        codoc_dir=cd,
                        file_paths=code_files,
                        author="watch",
                    )
                    n = result.get("proposals_emitted", 0)
                    if n:
                        typer.echo(f"    → {n} proposal(s) emitted")
                except Exception as exc:
                    typer.echo(f"    ✗ reflect failed: {exc}", err=True)

            # --- Route 2: .codoc changes → sync + (optionally) realize ---
            if codoc_changed:
                typer.echo("  ↪ sync(.codoc/tree/)")
                try:
                    sync_result = sync_from_dir(cd, author="watch")
                    n_ops = len(sync_result.applied)
                    if n_ops:
                        typer.echo(f"    → {n_ops} op(s) applied: {', '.join(sync_result.applied[:5])}")
                    else:
                        typer.echo("    → no changes")
                except Exception as exc:
                    typer.echo(f"    ✗ sync failed: {exc}", err=True)
                    continue

                if no_realize:
                    continue

                # Route .codoc changes to feedforward (placeholder) or realize (feedforward_pending/amended).
                try:
                    from codoc.storage.sqlite_store import SQLiteStore
                    from codoc.projection.parser import parse_tree_dir
                    from codoc.projection.differ import diff_tree

                    db_path = str(Path(cd) / "codoc.db")
                    with SQLiteStore(db_path) as store:
                        features = store.list_features()
                        placeholder_uuids = [
                            f.uuid for f in features
                            if not f.retired and f.status == "placeholder"
                        ]
                        feedforward_pending_uuids = [
                            f.uuid for f in features
                            if not f.retired and f.status == "feedforward_pending"
                        ]

                    # Step 1: run feedforward for placeholder features
                    if placeholder_uuids:
                        typer.echo(f"  ↪ feedforward({len(placeholder_uuids)} placeholder(s))")
                        try:
                            from codoc.pipelines.feedforward.runner import run_feedforward
                            ff_result = run_feedforward(cd, root_dir)
                            if ff_result.proposals_emitted:
                                typer.echo(f"    → {ff_result.proposals_emitted} feedforward proposal(s) emitted")
                            for err in ff_result.errors:
                                typer.echo(f"    ✗ feedforward: {err}", err=True)
                        except Exception as exc:
                            typer.echo(f"    ✗ feedforward failed: {exc}", err=True)

                    if no_realize:
                        continue

                    # Step 2: run realize for feedforward_pending features (accepted plan)
                    # or for features with actionable amend ops (existing behavior)
                    with SQLiteStore(db_path) as store:
                        parsed = parse_tree_dir(cd)
                        ops, _errs = diff_tree(parsed, store)

                        from codoc.pipelines.realize.runner import _is_actionable
                        actionable = [op for op in ops if _is_actionable(op)]

                        # Also trigger realize for feedforward_pending features even
                        # without explicit diff ops (the plan was accepted elsewhere)
                        if not actionable and not feedforward_pending_uuids:
                            continue

                        if feedforward_pending_uuids and not actionable:
                            # Build synthetic realize from pending features
                            from codoc.pipelines.realize.runner import run_realize
                            from codoc.projection.differ import AmendOp
                            pending_ops = []
                            for f in features:
                                if f.uuid in feedforward_pending_uuids:
                                    pending_ops.append(AmendOp(
                                        uuid=f.uuid,
                                        new_intent=f.intent,
                                        new_fields={"purpose": f.purpose, "rationale": f.rationale, "scenario": f.scenario},
                                    ))
                            actionable = pending_ops or actionable

                        if not actionable:
                            continue

                        n_actionable = len(actionable)
                        typer.echo(f"  ↪ realize({n_actionable} ops)")
                        from codoc.pipelines.realize.runner import run_realize
                        r = run_realize(
                            ops=actionable,
                            store=store,
                            root_dir=root_dir,
                            codoc_dir=cd,
                            dry_run=dry_run_realize,
                        )

                    if r.error:
                        typer.echo(f"    ✗ realize: {r.error}", err=True)
                    elif r.skipped:
                        pass
                    else:
                        typer.echo(
                            f"    → claude exit={r.claude_exit_code}, "
                            f"reflected {len(r.reflected_files)} files, "
                            f"{r.proposals_emitted} proposal(s)"
                        )
                except Exception as exc:
                    typer.echo(f"    ✗ realize failed: {exc}", err=True)

    except KeyboardInterrupt:
        typer.echo("\nStopped.")
