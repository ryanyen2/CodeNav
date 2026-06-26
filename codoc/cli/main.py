"""codoc CLI.

Core commands:

``init``    bootstrap a fresh repo into a feature tree + render tree.codoc
``watch``   the daemon: run both loops as you edit code / tree.codoc
``status``  tree size, pending proposals, recent activity
``sync``    one-shot escape hatch: apply tree.codoc edits, then reflect code

Plumbing (agents / no-IDE workflows):

``accept`` / ``reject``  resolve a pending proposal from the CLI
``reflect``              recovery-grade code → tree reconciliation
``propose``              author a plan proposal from the command line
``install-hooks``        (re)install the Claude Code hooks + MCP registration

Day to day, everything happens by editing ``.codoc/tree.codoc`` and letting
watch/sync react.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(
    name="codoc",
    help="codoc keeps a feature-tree view of your code, synced as you edit.",
    no_args_is_help=True,
)


def _codoc_dir(root: str) -> str:
    return str(Path(root) / ".codoc")


@app.command()
def init(
    root: str = typer.Option(".", "--root", help="Repository root."),
    hooks: bool = typer.Option(
        True,
        "--hooks/--no-hooks",
        help="Install codoc Claude Code hooks into .claude/settings.json.",
    ),
):
    """Index the repo, propose an initial feature tree, render tree.codoc."""
    from codoc.loop.bootstrap import run_init

    typer.echo(f"Indexing {root} and bootstrapping the feature tree…")
    res = run_init(root)
    typer.echo(f"✓ {res.summary()}")
    typer.echo(f"  Edit {_codoc_dir(root)}/tree.codoc, then run `codoc watch`.")

    if hooks:
        try:
            from codoc.agent.install_hooks import install_hooks
            install_hooks(root)
            typer.echo("  ✓ Claude Code hooks installed in .claude/settings.json")
            typer.echo("  ✓ codoc MCP server registered in .mcp.json (codoc_tree, codoc_reflect, …)")
            typer.echo("  ✓ /codoc:plan command + codoc-intent skill installed in .claude/")
        except Exception as exc:
            typer.echo(f"  ⚠  Could not install hooks: {exc}", err=True)


@app.command()
def watch(
    root: str = typer.Option(".", "--root", help="Repository root."),
    no_realize: bool = typer.Option(False, "--no-realize", help="Sync the tree but never queue realization directives."),
    dry: bool = typer.Option(False, "--dry", help="Reflect + apply tree edits, but don't queue realization directives."),
    auto_realize: bool = typer.Option(
        False, "--auto-realize",
        help="Unattended fallback: implement queued tree edits when no interactive "
             "session is around — via the Claude Agent SDK when installed "
             "(codoc[sdk]; live readout), else a headless `claude -p /codoc:sync`.",
    ),
):
    """Watch code + tree.codoc and run both loops continuously."""
    from codoc.loop.watch import run_watch

    run_watch(root, _codoc_dir(root), no_realize=no_realize, dry_run=dry,
              auto_realize=auto_realize, printer=typer.echo)


@app.command()
def serve(
    root: str = typer.Option(".", "--root", help="Repository root."),
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Bind address. Localhost only by default — remote reach is via the "
             "tunnel (--tunnel, unit U6), never by binding 0.0.0.0.",
    ),
    port: int = typer.Option(8787, "--port", help="Local port for the hub."),
    static_dir: str = typer.Option(
        None, "--static-dir", help="Built standalone SPA directory (unit U2)."),
    tunnel: bool = typer.Option(
        False, "--tunnel",
        help="Expose the hub over a cloudflared tunnel (needs cloudflared + a "
             "Cloudflare Access policy — see docs/serve-deployment.md)."),
):
    """Serve codoc as a web app from this machine, supervising the daemon.

    The hub is a separate process (peer to the VS Code extension): it atomically
    claims single ownership of the repo, keeps one ``codoc watch`` daemon alive,
    and serves the intent-tree editor. The listener binds localhost; remote,
    GitHub-authorized access arrives over a tunnel in a later unit.
    """
    import uvicorn

    from codoc.serve.app import build_app
    from codoc.serve.ratelimit import RateLimiter
    from codoc.serve.static import resolve_static_dir
    from codoc.serve.supervise import DaemonSupervisor, OwnershipError

    cd = _codoc_dir(root)

    # Auto-discover the built SPA so the U2 placeholder only shows when the bundle
    # genuinely is not built (run `npm run build` in vscode-codoc/).
    spa_dir = resolve_static_dir(root, static_dir)
    if spa_dir is None:
        typer.echo(
            "  ⚠ standalone editor bundle not found — serving placeholder. "
            "Build it with `npm run build` in vscode-codoc/, or pass --static-dir.",
            err=True,
        )
    supervisor = DaemonSupervisor(root, cd)
    try:
        supervisor.start()
    except OwnershipError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    tunnel_proc = None
    if tunnel:
        from codoc.serve.tunnel import launch_tunnel
        try:
            tunnel_proc = launch_tunnel(port)
            typer.echo("  ↳ cloudflared tunnel launched — gate it with Cloudflare Access.")
        except FileNotFoundError:
            typer.echo("  ⚠ cloudflared not found — install it (see docs/serve-deployment.md).", err=True)

    # Per-identity write rate limit: ~2 writes/s sustained, burst 60. Bounds a
    # remote flood from DoSing the daemon / amplifying the SSE fan-out.
    rate_limiter = RateLimiter(capacity=60, refill_per_sec=2)
    typer.echo(f"codoc serve · http://{host}:{port} · supervising daemon · {cd}")
    if spa_dir is not None:
        typer.echo(f"  ↳ editor bundle · {spa_dir}")
    try:
        uvicorn.run(build_app(cd, static_dir=spa_dir, rate_limiter=rate_limiter),
                    host=host, port=port, log_level="warning")
    finally:
        if tunnel_proc is not None:
            tunnel_proc.terminate()
        supervisor.stop()


@app.command()
def realize(
    root: str = typer.Option(".", "--root", help="Repository root."),
    engine: str = typer.Option(
        "auto", "--engine",
        help="auto | sdk | cli — sdk streams a live per-action readout "
             "(claude-agent-sdk); cli is a blind `claude -p /codoc:sync`.",
    ),
    permission_mode: str = typer.Option(
        "acceptEdits", "--permission-mode",
        help="Claude Agent SDK permission mode for the sdk engine.",
    ),
):
    """Implement the queued tree edits (.codoc/realize.md) now, in the foreground.

    The SDK engine shows one compact line per agent action (edit / read /
    reflect / fetch) and mirrors every action into ``.codoc/activity.json`` so
    the IDE shows live signals on the matching features.
    """
    import subprocess

    from codoc.loop.edits import append_handoffs, read_manifest
    from codoc.loop.loop_b import realize_path, run_loop_b
    from codoc.loop.sdk_realize import resolve_engine, run_sdk_realize, sdk_available

    codoc_dir = _codoc_dir(root)
    # `codoc realize` IS the CLI hand-off gesture (held-draft model): a doc AMEND
    # mints a HELD draft, not surprise code. Flush every held draft now — write the
    # positive hand-off signal and run one Loop B pass to (re)build realize.md.
    if not realize_path(codoc_dir).exists():
        held = [d.feature_id for d in read_manifest(codoc_dir)
                if not d.handed_off and d.feature_id]
        if held:
            append_handoffs(codoc_dir, held)
            run_loop_b(root, codoc_dir)
    if not realize_path(codoc_dir).exists():
        typer.echo("Nothing queued — no held drafts and no realize.md. "
                   "Edit tree.codoc, then `codoc realize` to hand off the change.")
        raise typer.Exit(0)

    if engine not in ("auto", "sdk", "cli"):
        typer.echo(f"Unknown --engine {engine!r} — expected auto, sdk, or cli.", err=True)
        raise typer.Exit(2)
    engine = resolve_engine(engine)
    if engine == "sdk":
        if not sdk_available():
            typer.echo("claude-agent-sdk is not installed — pip install 'codoc[sdk]', "
                       "or use --engine cli.", err=True)
            raise typer.Exit(2)
        raise typer.Exit(run_sdk_realize(root, _codoc_dir(root),
                                         permission_mode=permission_mode,
                                         printer=typer.echo))
    from codoc.loop.autorealize import find_claude
    claude = find_claude()
    if claude is None:
        typer.echo("`claude` CLI not found on PATH.", err=True)
        raise typer.Exit(2)
    typer.echo("codoc realize · /codoc:sync · claude -p (no streamed readout)")
    raise typer.Exit(subprocess.call([claude, "-p", "/codoc:sync"], cwd=root))


@app.command()
def status(root: str = typer.Option(".", "--root", help="Repository root.")):
    """Show feature count, pending proposals, and recent activity."""
    from codoc.store.db import open_store

    with open_store(_codoc_dir(root)) as store:
        feats = store.list_features()
        pending = store.pending_events()
        from codoc.loop.status import refresh_status

        st = refresh_status(_codoc_dir(root), store)
        state = json.loads(st.read_text()).get("state", "in_sync")
        typer.echo(f"codoc · {len(feats)} features · {len(pending)} pending · state: {state}")

        # Coverage invariant: every indexed chunk should be attributed to a
        # feature. A gap means code is silently unbound (a Loop A drop) — surface
        # it so it can't hide behind an "in_sync" status.
        try:
            from codoc.pipelines.indexing.reader import read_all_chunks
            n_chunks = len(read_all_chunks(_codoc_dir(root), with_embeddings=False,
                                           with_source=False))
            n_bound = len(store.all_bindings())
            gap = n_chunks - n_bound
            if n_chunks and gap / n_chunks > 0.05:
                typer.echo(f"  ⚠ coverage: {n_bound}/{n_chunks} chunks bound "
                           f"({gap} unattributed) — run `codoc reflect`")
        except Exception:
            pass  # index not built yet / unreadable — coverage check is best-effort
        if pending:
            typer.echo("\nPending proposals (review in tree.codoc, Accept/Reject in the IDE):")
            for e in pending:
                title = e.op.title or e.op.feature_id or ""
                typer.echo(f"  · {e.op.kind.value:11} {title}  ⟨{e.id}⟩")
        recent = [e for e in store.recent_events(8) if e.applied]
        if recent:
            typer.echo("\nRecent changes:")
            for e in recent:
                title = e.op.title or e.op.feature_id or ""
                typer.echo(f"  · {e.source:9} {e.op.kind.value:11} {title}")


@app.command()
def sync(
    root: str = typer.Option(".", "--root", help="Repository root."),
    dry: bool = typer.Option(False, "--dry", help="Don't queue tree-edit directives for the session."),
):
    """One-shot: apply tree.codoc edits (Loop B), then reflect code (Loop A).

    Loop B no longer spawns a coding agent — code-implying tree edits are queued
    in ``.codoc/realize.md`` for the live Claude Code session to implement via
    ``/codoc:sync``.
    """
    from codoc.codoc_file.render import write_tree
    from codoc.loop.loop_a import reconcile_drift
    from codoc.loop.loop_b import run_loop_b
    from codoc.loop.status import refresh_status
    from codoc.store.db import open_store

    cd = _codoc_dir(root)
    rb = run_loop_b(root, cd, dry_run=dry)
    typer.echo(f"▸ codoc→code  {rb.summary()}")
    if rb.directives:
        label = "would queue (dry run)" if dry else "queued for the session (run /codoc:sync)"
        typer.echo(f"  {label}:")
        for d in rb.directives:
            typer.echo(f"    · {d.splitlines()[0]}")
    # Reflect with the recovery-grade, state-based reconciler (not the temporal
    # diff) so a missed/crashed cycle self-heals and a no-op sync converges.
    ra = reconcile_drift(root, cd)
    typer.echo(f"▸ code→codoc  {ra.summary()}")
    with open_store(cd) as store:
        write_tree(store, cd)
        st = refresh_status(cd, store)
        state = json.loads(st.read_text()).get("state", "in_sync")
        typer.echo(f"▸ state       {state}")


@app.command()
def accept(
    event_id: str = typer.Argument(..., help="Proposal event id (⟨e-…⟩, with or without the ⟨⟩)."),
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Accept a pending proposal from the CLI (no IDE needed).

    Writes the verdict to inbox.json and drains it through Loop B — the same path
    the IDE's Accept action uses — so an accepted plan node gets implemented and a
    code-drift proposal is applied.
    """
    _verdict(root, event_id, accept=True)


@app.command()
def reject(
    event_id: str = typer.Argument(..., help="Proposal event id (⟨e-…⟩, with or without the ⟨⟩)."),
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Reject (drop) a pending proposal from the CLI (no IDE needed)."""
    _verdict(root, event_id, accept=False)


def _verdict(root: str, event_id: str, *, accept: bool) -> None:
    from codoc.codoc_file.render import write_tree
    from codoc.loop import inbox
    from codoc.loop.loop_b import run_loop_b
    from codoc.store.db import open_store

    eid = event_id.strip().strip("⟨⟩")
    cd = _codoc_dir(root)
    with open_store(cd) as store:
        if store.get_event(eid) is None:
            typer.echo(f"Error: no pending proposal ⟨{eid}⟩", err=True)
            raise typer.Exit(code=1)
    inbox.append_verdict(cd, eid, accept=accept)
    rb = run_loop_b(root, cd)
    verb = "accepted" if accept else "rejected"
    typer.echo(f"✓ {verb} ⟨{eid}⟩  ·  {rb.summary()}")
    with open_store(cd) as store:
        write_tree(store, cd)


@app.command()
def reflect(
    root: str = typer.Option(".", "--root", help="Repository root."),
    scope: str = typer.Option(None, "--scope", help="Comma-separated repo-relative files to reflect (default: whole repo)."),
):
    """Reflect code → tree by reconciling the index against the store (idempotent).

    The recovery-grade reflection: unlike the watch daemon's temporal diff, it
    re-derives the full code↔tree divergence from current state, so it catches
    changes a missed/crashed cycle dropped. Spawned by the Stop hook when no
    daemon is running; also runnable by hand.
    """
    from codoc.loop.loop_a import reconcile_drift

    file_scope = {s.strip() for s in scope.split(",") if s.strip()} if scope else None
    res = reconcile_drift(root, _codoc_dir(root), file_scope=file_scope)
    typer.echo(f"▸ reflect  {res.summary()}")


@app.command()
def propose(
    kind: str = typer.Argument(..., help="add_node | amend | retire_node | move_node"),
    root: str = typer.Option(".", "--root", help="Repository root."),
    title: str = typer.Option(None, "--title", help="Feature title (add_node / amend)."),
    description: str = typer.Option(None, "--description", help="Feature description prose."),
    parent: str = typer.Option(None, "--parent", help="Parent feature id (add_node / move_node)."),
    feature: str = typer.Option(None, "--feature", help="Target feature id (amend / retire_node / move_node)."),
    rationale: str = typer.Option("", "--rationale", help="One-line justification shown in the diff block."),
    bind: list[str] = typer.Option(None, "--bind", help="Binding as file.py::symbol_path (repeatable)."),
):
    """Author an agent plan proposal in the codoc feature tree.

    Creates a pending proposal (applied=False Event) rendered as an in-place
    overlay in ``tree.codoc``. The user can Accept or Reject it in the VS Code
    IDE (or via ``codoc accept``/``codoc reject``); acceptance triggers Loop B
    to implement.

    Example::

        codoc propose add_node --title "Date formatting" \\
            --description "ISO-8601 date helpers." \\
            --bind utils/dates.py::format_date
    """
    from codoc.agent.propose import propose_plan

    try:
        eid = propose_plan(
            root,
            kind=kind,
            title=title,
            description=description,
            parent_id=parent,
            feature_id=feature,
            rationale=rationale,
            binds=list(bind) if bind else [],
        )
        typer.echo(f"✓ Proposal created  ⟨{eid}⟩")
        typer.echo("  Accept it in the VS Code IDE (inline action on the diff block).")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def migrate(root: str = typer.Option(".", "--root", help="Repository root.")):
    """One-time, idempotent heal for workspaces predating the store-authoritative
    refactor: migrate ``tree.doc.json`` comment threads into the store and converge
    duplicate (re-minted) features onto a single keeper.

    Safe to rerun — a clean workspace is a no-op. The watch daemon runs this once
    on startup, so most workspaces self-heal; this is the manual escape hatch.
    """
    from codoc.loop.migrate import migrate_workspace

    res = migrate_workspace(_codoc_dir(root))
    typer.echo(f"▸ migrate  {res.summary()}")
    for note in res.notes:
        typer.echo(f"  ⚠ {note}")


@app.command(name="install-hooks")
def install_hooks_cmd(
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Install codoc Claude Code hooks into .claude/settings.json.

    Idempotent — safe to run multiple times.
    """
    from codoc.agent.install_hooks import install_hooks

    install_hooks(root)
    typer.echo("✓ Claude Code hooks installed in .claude/settings.json")
    typer.echo("  Hooks: SessionStart, Stop, PreToolUse(Edit|Write|Read), PostToolUse(Edit|Write)")


if __name__ == "__main__":
    app()
