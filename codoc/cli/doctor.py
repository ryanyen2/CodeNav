"""codoc doctor — pre-flight check for the codoc installation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer


def doctor(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    check_schema: bool = typer.Option(False, "--check-schema", help="Report schema version and exit"),
) -> None:
    """Run pre-flight checks: tree-sitter grammars, API key, SQLite, git hook, schema version."""

    from codoc.cli._utils import find_codoc_dir

    root = Path(root_dir).resolve()
    codoc_dir = find_codoc_dir(root) or (root / ".codoc")

    checks: list[tuple[str, str, bool]] = []  # (name, detail, ok)

    # ── .codoc/ present ──────────────────────────────────────────────
    checks.append((".codoc/ present", str(codoc_dir), codoc_dir.is_dir()))

    # ── Schema version ───────────────────────────────────────────────
    schema_ver = "unknown"
    schema_ok = False
    if codoc_dir.is_dir():
        db_path = codoc_dir / "codoc.db"
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA busy_timeout=2000")
                row = conn.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                conn.close()
                if row:
                    schema_ver = row[0]
                    schema_ok = schema_ver == "2"
                else:
                    schema_ver = "pre-v2 (run codoc init to upgrade)"
            except Exception as exc:
                schema_ver = f"error: {exc}"

    if check_schema:
        typer.echo(f"schema_version: {schema_ver}")
        raise typer.Exit(code=0 if schema_ok else 1)

    checks.append(("schema_version=2", schema_ver, schema_ok))

    # ── SQLite WAL ────────────────────────────────────────────────────
    wal_ok = False
    wal_detail = "n/a"
    if codoc_dir.is_dir():
        db_path = codoc_dir / "codoc.db"
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA busy_timeout=2000")
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                conn.close()
                wal_ok = mode == "wal"
                wal_detail = mode
            except Exception as exc:
                wal_detail = str(exc)
    checks.append(("SQLite WAL mode", wal_detail, wal_ok))

    # ── OPENAI_API_KEY ────────────────────────────────────────────────
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    provider = os.environ.get("CODOC_PROVIDER", "openai")
    api_key_ok = True
    api_key_detail = "ok"
    if provider == "openai":
        api_key_ok = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("CODOC_BASE_URL"))
        api_key_detail = "set" if api_key_ok else "OPENAI_API_KEY not set"
    checks.append(("LLM API key", api_key_detail, api_key_ok))

    # ── tree-sitter grammars ─────────────────────────────────────────
    grammar_ok = False
    grammar_detail = "n/a"
    try:
        from codoc.lang import get_adapter
        adapter = get_adapter(".py")
        if adapter is not None:
            grammar_ok = True
            grammar_detail = "python grammar ok"
        else:
            grammar_detail = "python grammar not found"
    except Exception as exc:
        grammar_detail = str(exc)
    checks.append(("tree-sitter python grammar", grammar_detail, grammar_ok))

    # ── git hook ─────────────────────────────────────────────────────
    hook_ok = False
    hook_detail = "not installed"
    git_hooks = root / ".git" / "hooks"
    if git_hooks.is_dir():
        post_commit = git_hooks / "post-commit"
        if post_commit.exists():
            content = post_commit.read_text(encoding="utf-8", errors="replace")
            if "codoc" in content:
                hook_ok = True
                hook_detail = "post-commit hook contains codoc"
            else:
                hook_detail = "post-commit hook exists but does not call codoc"
        else:
            hook_detail = ".git/hooks/post-commit not found"
    else:
        hook_detail = "not in a git repo"
    checks.append(("git post-commit hook", hook_detail, hook_ok))

    # ── node_id ───────────────────────────────────────────────────────
    node_id_ok = False
    node_id_detail = "n/a"
    if codoc_dir.is_dir():
        nid_file = codoc_dir / "node_id"
        if nid_file.exists():
            node_id_ok = True
            node_id_detail = nid_file.read_text().strip()[:12]
        else:
            node_id_detail = "missing — run codoc init"
    checks.append(("HLC node_id", node_id_detail, node_id_ok))

    # ── stale lock ───────────────────────────────────────────────────
    lock_ok = True
    lock_detail = "none"
    if codoc_dir.is_dir():
        lock_file = codoc_dir / "codoc.lock"
        if lock_file.exists():
            lock_ok = False
            lock_detail = f"stale lock at {lock_file} — remove if no codoc process is running"
    checks.append(("codoc.lock absent", lock_detail, lock_ok))

    # ── Print results ─────────────────────────────────────────────────
    all_ok = all(ok for _, _, ok in checks)
    width = max(len(name) for name, _, _ in checks) + 2
    for name, detail, ok in checks:
        mark = "✓" if ok else "✗"
        label = f"{name:<{width}}"
        typer.echo(f"  {mark}  {label} {detail}")

    if all_ok:
        typer.echo("\nAll checks passed.")
    else:
        failed = [name for name, _, ok in checks if not ok]
        typer.echo(f"\n{len(failed)} check(s) failed: {', '.join(failed)}", err=True)
        raise typer.Exit(code=1)
