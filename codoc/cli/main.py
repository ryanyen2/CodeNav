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
import os
from pathlib import Path

import typer

app = typer.Typer(
    name="codoc",
    help="codoc keeps a feature-tree view of your code, synced as you edit.",
    no_args_is_help=True,
)


def _codoc_dir(root: str) -> str:
    return str(Path(root) / ".codoc")


def _workspace_exists(root: str) -> bool:
    return (Path(root) / ".codoc" / "codoc.db").exists()


def _require_workspace(root: str) -> None:
    """Fail fast with guidance when a command needs an initialized workspace but none
    exists — a fresh user running ``codoc status``/``sync`` before ``codoc init`` should
    see one clear line, not a raw sqlite ``unable to open database file`` traceback."""
    if not _workspace_exists(root):
        typer.echo(
            f"No codoc workspace here — run `codoc init` first (looked for "
            f"{_codoc_dir(root)}/codoc.db).",
            err=True,
        )
        raise typer.Exit(code=1)


def _read_state(status_path) -> str:
    """The status file's ``state``, tolerant of a truncated/corrupt ``status.json`` so a
    damaged control file can never turn ``status``/``sync`` into a traceback."""
    try:
        return json.loads(Path(status_path).read_text(encoding="utf-8")).get("state", "in_sync")
    except (OSError, ValueError):
        return "in_sync"


def _version_callback(value: bool) -> None:
    if not value:
        return
    from importlib.metadata import PackageNotFoundError, version

    try:
        typer.echo(f"codoc {version('codoc')}")
    except PackageNotFoundError:
        typer.echo("codoc (version unknown — not installed as a package)")
    raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the codoc version and exit.",
    ),
) -> None:
    """codoc keeps a feature-tree view of your code, synced as you edit."""


@app.command()
def init(
    root: str = typer.Option(".", "--root", help="Repository root."),
    force: bool = typer.Option(
        False, "--force",
        help="Re-bootstrap even if a workspace already exists (discards the current "
             "tree and re-derives it from code).",
    ),
    hooks: bool = typer.Option(
        True,
        "--hooks/--no-hooks",
        help="Install codoc Claude Code hooks into .claude/settings.json.",
    ),
    doc_language: str = typer.Option(
        "", "--doc-language", "--lang",
        help="Language to AUTHOR the feature tree in (BCP-47: en, zh-Hans, zh-Hant, "
             "ja, ko, fr, …). Persisted to .codoc/config.json and committed, so every "
             "contributor's daemon writes the same language. Default: en.",
    ),
):
    """Index the repo, propose an initial feature tree, render tree.codoc."""
    from codoc.loop.bootstrap import run_init

    # A running daemon (the VS Code extension's, or a manual `codoc watch`) must not
    # race a re-init: init rebuilds the index (which can wipe + recreate the LanceDB
    # state) and, with --force, deletes the store the daemon has open. Stop it first.
    from codoc.loop.watch import daemon_running

    if _workspace_exists(root) and daemon_running(_codoc_dir(root)):
        typer.echo(
            "A codoc daemon is watching this repo — stop it (or close the VS Code "
            "workspace) before running `codoc init`.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Guard against clobbering / duplicating an existing tree: a second `init` would
    # re-bootstrap from code with fresh ids on top of the current features. Require
    # --force, which starts from a clean store so there is no duplication.
    if _workspace_exists(root) and not force:
        n = 0
        try:
            from codoc.store.db import open_store
            with open_store(_codoc_dir(root)) as store:
                n = len(store.list_features())
        except Exception:  # noqa: BLE001 — a corrupt/partial store still counts as "exists"
            n = -1
        detail = f"{n} features" if n >= 0 else "possibly partial/corrupt"
        typer.echo(
            f"A codoc workspace already exists here ({detail}). Re-run with `--force` to "
            "rebuild it from scratch, or run `codoc watch` to keep working.",
            err=True,
        )
        raise typer.Exit(code=1)
    if force and _workspace_exists(root):
        # Clean slate so the rebuild can't stack fresh-id ADDs on the old features.
        (Path(_codoc_dir(root)) / "codoc.db").unlink(missing_ok=True)

    typer.echo(f"Indexing {root} and bootstrapping the feature tree…")
    try:
        res = run_init(root, printer=typer.echo,
                       doc_language=doc_language or None)
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        typer.echo("\n✗ init interrupted — no partial tree was written; re-run `codoc init`.",
                   err=True)
        raise typer.Exit(code=130)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ init failed: {exc}", err=True)
        typer.echo(
            "  The store was rolled back to a clean state (no partial tree). Fix the "
            "cause — e.g. set an LLM key or start the `claude` CLI (see `codoc init --help`) "
            "— and re-run `codoc init`.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    typer.echo(f"✓ {res.summary()}")
    if doc_language:
        from codoc.doclang import resolve

        typer.echo(f"  ✓ authoring language: {resolve(doc_language).name} "
                   f"(committed in .codoc/config.json — change it with `codoc lang`)")
    typer.echo(f"  Open {_codoc_dir(root)}/tree.codoc in VS Code, then run `codoc watch`.")

    if hooks:
        try:
            from codoc.agent.install_hooks import install_hooks
            commands = install_hooks(root)
            typer.echo("  ✓ Claude Code hooks installed in .claude/settings.json")
            typer.echo("  ✓ codoc MCP server registered in .mcp.json (codoc_tree, codoc_reflect, …)")
            typer.echo(f"  ✓ {', '.join(commands)} + codoc-intent skill installed in .claude/")
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
        help="Expose the hub over a cloudflared tunnel. NOTE: the hub's GitHub "
             "authentication is not yet wired, so a tunnel publishes the tree with NO "
             "access control — refused unless --i-understand-unauthenticated is also set."),
    insecure_public: bool = typer.Option(
        False, "--i-understand-unauthenticated",
        help="Acknowledge that --tunnel currently exposes the tree with no authentication "
             "and open it anyway (demo/local-network use only)."),
):
    """Serve codoc as a web app from this machine, supervising the daemon.

    The hub is a separate process (peer to the VS Code extension): it atomically
    claims single ownership of the repo, keeps one ``codoc watch`` daemon alive,
    and serves the intent-tree editor. The listener binds localhost by default.

    Remote, GitHub-authorized access is not finished: the read endpoints
    (``/api/payload``/``/api/media``/``/api/events``) are currently served without
    authentication, so exposing the hub off-machine (``--host 0.0.0.0`` or
    ``--tunnel``) makes the whole tree public. Keep it on localhost until auth lands.
    """
    # Guards BEFORE the heavy `serve`-extra imports so the safety refusal (and the
    # "no workspace" message) fire even when the extra isn't installed.
    _require_workspace(root)

    # GitHub-backed auth from the environment (client id/secret + a push-access token +
    # CODOC_SERVE_REPO). When configured, every /api/* route — reads included — is gated
    # on a valid collaborator session and the /auth/* sign-in flow is live.
    from codoc.serve.github_auth import GithubAuthConfig, build_auth_context

    gh_config = GithubAuthConfig.from_env()
    auth_ctx = build_auth_context(gh_config) if gh_config is not None else None

    # Safe-by-default: exposing the hub off-machine REQUIRES configured auth. Without it
    # the tree + code map would be public, so refuse unless the user explicitly opts in.
    exposes_publicly = tunnel or host not in ("127.0.0.1", "localhost", "::1")
    if exposes_publicly and auth_ctx is None and not insecure_public:
        typer.echo(
            "Refusing to expose the hub: no GitHub authentication is configured, so "
            "--tunnel (or a non-localhost --host) would publish your entire feature tree and "
            "code map to anyone who reaches the URL.\n"
            "  • Configure auth: set CODOC_GITHUB_CLIENT_ID / CODOC_GITHUB_CLIENT_SECRET / "
            "CODOC_GITHUB_TOKEN / CODOC_SERVE_REPO (see docs/serve-deployment.md), or\n"
            "  • Keep it local: `codoc serve` (binds 127.0.0.1), or\n"
            "  • Accept the risk for a demo: add --i-understand-unauthenticated.",
            err=True,
        )
        raise typer.Exit(code=1)

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
            typer.echo("  ↳ cloudflared tunnel launched.")
        except FileNotFoundError:
            typer.echo("  ⚠ cloudflared not found — install it (see docs/serve-deployment.md).", err=True)

    # Per-identity write rate limit: ~2 writes/s sustained, burst 60. Bounds a
    # remote flood from DoSing the daemon / amplifying the SSE fan-out.
    rate_limiter = RateLimiter(capacity=60, refill_per_sec=2)
    typer.echo(f"codoc serve · http://{host}:{port} · supervising daemon · {cd}")
    if auth_ctx is not None:
        typer.echo(f"  ↳ auth · GitHub collaborators of {gh_config.owner}/{gh_config.repo} "
                   "(sign in at /auth/login)")
        # Hub-owned realization: hand-offs realize on a worktree → PR, sandboxed. Only
        # runs when auth is configured (the hub trust boundary). Best-effort; never
        # blocks serving if git/gh/SDK are unavailable.
        from codoc.serve.realize_hub import start_realize_worker
        realize_worker = start_realize_worker(root, cd, gh_config, printer=typer.echo)
    else:
        realize_worker = None
        typer.echo("  ↳ auth · none (localhost-only; not exposed off-machine)")
    if spa_dir is not None:
        typer.echo(f"  ↳ editor bundle · {spa_dir}")
    try:
        uvicorn.run(build_app(cd, static_dir=spa_dir, auth=auth_ctx, rate_limiter=rate_limiter),
                    host=host, port=port, log_level="warning")
    finally:
        if realize_worker is not None:
            realize_worker.stop()
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

    # `realize` acts on the realize.md / manifest queue, which can exist without a full
    # store; its own "Nothing queued" guard below handles the un-initialized case.
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


@app.command(name="export-markdown")
def export_markdown_cmd(
    root: str = typer.Option(".", "--root", help="Repository root."),
    out: str = typer.Option("", "--out", help="Write to this file instead of stdout."),
    title: str = typer.Option("Codebase feature guide", "--title",
                              help="Top-level heading for the exported document."),
):
    """Export the live feature tree as plain markdown (no ids, no proposals).

    For workflows without the codoc extension — e.g. generating a CLAUDE.md that
    carries the same features, prose, and recorded rationale, with code cited as
    file.py::symbol paths instead of live bindings.
    """
    _require_workspace(root)
    from codoc.codoc_file.export import export_markdown
    from codoc.store.db import open_store

    with open_store(_codoc_dir(root)) as store:
        text = export_markdown(store, title=title)
    if out:
        from pathlib import Path as _P
        _P(out).write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out} ({len(text.splitlines())} lines)")
    else:
        typer.echo(text, nl=False)


@app.command()
def status(root: str = typer.Option(".", "--root", help="Repository root.")):
    """Show feature count, pending proposals, and recent activity."""
    _require_workspace(root)
    from codoc.store.db import open_store

    with open_store(_codoc_dir(root)) as store:
        feats = store.list_features()
        pending = store.pending_events()
        from codoc.loop.status import refresh_status

        st = refresh_status(_codoc_dir(root), store)
        state = _read_state(st)
        from codoc.doclang import workspace_doc_language

        lang = workspace_doc_language(_codoc_dir(root))
        line = f"codoc · {len(feats)} features · {len(pending)} pending · state: {state}"
        # Only shown when it is NOT English: on an English repo it is noise, but on a
        # tree authored in another language it is the setting most likely to be wrong
        # (a stale env override, an unmigrated .gitignore) and least likely to be
        # noticed — the symptom is prose quietly arriving in the wrong language.
        if not lang.is_default:
            line += f" · language: {lang.code}"
        typer.echo(line)

        # Coverage invariant: every indexed chunk should be attributed to a
        # feature. A gap means code is silently unbound (a Loop A drop) — surface
        # it so it can't hide behind an "in_sync" status.
        try:
            from codoc.pipelines.indexing.reader import read_all_chunks
            # Count DISTINCT chunks, not rows. The index can hold the same
            # (file, symbol_path) more than once — re-indexing a workspace on a
            # different machine leaves 134 duplicate rows out of 575 on a real
            # ember checkout — and a binding is unique per key, so comparing row
            # count against binding count reports a gap that does not exist.
            # Every downstream reader keys by (file, symbol_path), so the
            # duplicates are harmless; only this arithmetic was wrong, and it
            # told a fully-bound workspace to run `codoc reflect`.
            keys = {(c.file, c.symbol_path)
                    for c in read_all_chunks(_codoc_dir(root), with_embeddings=False,
                                             with_source=False)}
            bound = {(b.file, b.symbol_path) for b in store.all_bindings()}
            n_chunks, gap = len(keys), len(keys - bound)
            if n_chunks and gap / n_chunks > 0.05:
                typer.echo(f"  ⚠ coverage: {n_chunks - gap}/{n_chunks} chunks bound "
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
def history(
    feature: str = typer.Argument(..., help="Feature id (f-…) or a title fragment."),
    root: str = typer.Option(".", "--root", help="Repository root."),
    limit: int = typer.Option(15, "--limit", help="Max changes to show."),
):
    """Show one feature's change history — who changed it, when, and why."""
    _require_workspace(root)
    from datetime import datetime

    from codoc.doclang import norm_key
    from codoc.store.db import open_store

    with open_store(_codoc_dir(root)) as store:
        fid = feature.strip("⟨⟩")
        f = store.get_feature(fid)
        if f is None:
            # norm_key, not .lower(): a title fragment typed on a CJK keyboard
            # arrives with full-width punctuation (（ ， ）) that is a different
            # codepoint from its ASCII twin, so a plain lowercase compare failed to
            # match a title the user could see on screen and had just copied.
            needle = norm_key(feature)
            matches = [x for x in store.list_features() if needle in norm_key(x.title)]
            if not matches:
                typer.echo(f"no feature matches {feature!r}")
                raise typer.Exit(1)
            if len(matches) > 1:
                typer.echo(f"{feature!r} matches several features:")
                for m in matches[:10]:
                    typer.echo(f"  · {m.title}  ⟨{m.id}⟩")
                raise typer.Exit(1)
            f = matches[0]
        events = store.events_for_feature(f.id, limit=limit)
        typer.echo(f"{f.title}  ⟨{f.id}⟩ · {len(events)} change(s)")
        for e in events:
            when = datetime.fromtimestamp(e.at.wall_clock / 1000).strftime("%Y-%m-%d %H:%M")
            who = e.actor or e.source
            line = f"  {when}  {who:12} {e.op.kind.value:11}"
            if e.mode:
                line += f" ({e.mode})"
            if e.caused_by:
                line += f"  ← ⟨{e.caused_by}⟩"
            typer.echo(line)
            if e.op.rationale:
                typer.echo(f"      {e.op.rationale}")


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
    _require_workspace(root)
    from codoc.codoc_file.render import write_tree
    from codoc.loop.loop_a import reconcile_drift
    from codoc.loop.loop_b import run_loop_b
    from codoc.loop.status import refresh_status
    from codoc.store.db import open_store

    cd = _codoc_dir(root)
    # --dry APPLIES the tree edits (mutates the store, re-renders both files) but does not
    # hand realization to the agent — realize=False, not loop_b's read-mostly dry_run.
    rb = run_loop_b(root, cd, realize=not dry)
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
        state = _read_state(st)
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
    _require_workspace(root)
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
    _require_workspace(root)
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
    after: str = typer.Option(None, "--after", help="Place it after this sibling feature id (add_node / move_node)."),
    before: str = typer.Option(None, "--before", help="Place it before this sibling feature id (add_node / move_node)."),
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

    ``--after`` / ``--before`` name the siblings the node goes between (feature ids), for
    ``add_node`` and ``move_node``. Omit both to append. A ``move_node`` that repeats the
    node's current ``--parent`` and gives new anchors IS a reorder::

        codoc propose move_node --feature f-1a2b --parent f-cafe --after f-beef
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
            after_id=after or "",
            before_id=before or "",
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


@app.command()
def lang(
    code: str = typer.Argument(
        "", help="BCP-47 tag to author the tree in (en, zh-Hans, zh-Hant, ja, ko, "
                 "fr, …). Omit to show the current setting."),
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Show or set the language codoc AUTHORS the feature tree in.

    This is the tree's language, not the code's: identifiers, paths, and citations
    are never translated. Setting it changes what NEW and AMENDED prose comes out
    in; it does not retranslate the tree, so switching an established tree leaves
    the old nodes as they were until each is next amended.
    """
    _require_workspace(root)
    from codoc.doclang import (
        ENV_VAR, known_codes, read_config, resolve, workspace_doc_language,
        write_config,
    )

    cd = _codoc_dir(root)
    if not code:
        current = workspace_doc_language(cd)
        typer.echo(f"authoring language: {current.name}  ({current.code})")
        stored = read_config(cd).get("doc_language")
        override = os.environ.get(ENV_VAR, "").strip()
        # Naming the override matters: the committed value is what the team sees in
        # review, so a shell export silently winning is exactly the confusion this
        # line prevents.
        if override and stored and resolve(override).code != resolve(stored).code:
            typer.echo(f"  ⚠ {ENV_VAR}={override} is overriding the committed "
                       f"setting ({stored}) for this shell only")
        elif not stored:
            typer.echo("  (default — nothing set in .codoc/config.json)")
        typer.echo(f"  built-in profiles: {', '.join(known_codes())} "
                   "(any other BCP-47 tag also works)")
        _echo_language_mix(cd, current)
        return

    lang_profile = resolve(code)
    write_config(cd, doc_language=lang_profile.code)
    typer.echo(f"✓ authoring language: {lang_profile.name}  ({lang_profile.code})")
    typer.echo("  Written to .codoc/config.json — commit it so every contributor's "
               "daemon authors the same language.")
    if lang_profile.code.casefold() != code.strip().casefold():
        typer.echo(f"  (resolved {code!r} → {lang_profile.code})")
    typer.echo("  Existing nodes are unchanged; new and amended prose follows the "
               "new setting.")


@app.command()
def translate(
    root: str = typer.Option(".", "--root", help="Repository root."),
    to: str = typer.Option(
        "", "--to",
        help="Target language (BCP-47). Defaults to the workspace setting, so the "
             "usual flow is `codoc lang zh-Hans` then `codoc translate`."),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would change — which nodes, and what would be refused — "
             "without writing anything."),
    limit: int = typer.Option(
        0, "--limit",
        help="Translate at most this many nodes. Useful for a paid-key trial run: "
             "the command is resumable, so re-running picks up the rest."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
):
    """Rewrite the existing tree's prose into the authoring language.

    Switching the language (`codoc lang`) only changes what codoc writes from then
    on — it never touches prose already on the page, because that prose is the
    author's. This is the explicit conversion for a tree that was built in another
    language: one LLM pass per batch of features, rewriting titles and descriptions
    while copying every code citation, external link, and bolded focus span through
    unchanged.

    Safe to re-run: it selects nodes by their *detected* language, so an interrupted
    run resumes and an already-translated node is skipped. Every node's previous
    wording stays in the change ledger — `codoc history <feature>` shows it.
    """
    _require_workspace(root)
    from codoc.doclang import resolve, workspace_doc_language
    from codoc.loop.translate import translate_tree

    cd = _codoc_dir(root)
    lang = resolve(to) if to else workspace_doc_language(cd)
    # No "the target is English so there is nothing to do" guard here. English is a
    # target like any other: switching a Chinese tree back to `en` and translating it
    # is the same operation in the other direction, and refusing it made the language
    # a one-way door. Whether there is anything to do is decided by what the nodes
    # actually say, which `translate_tree` reports as `already`.
    if not dry_run and not yes:
        # An explicit confirmation, because this is the one command that rewrites
        # every description in the tree — and the count is the part worth seeing
        # before agreeing to it.
        typer.echo(f"This rewrites the prose of every node not already in "
                   f"{lang.name}, in place.")
        typer.echo("  Previous wording is kept in the change ledger "
                   "(`codoc history <feature>`), and tree.codoc is tracked in git.")
        typer.echo("  Run with --dry-run first to see the list.")
        typer.confirm(f"Translate this tree into {lang.name}?", abort=True)

    if not to:
        typer.echo(f"Translating into {lang.name} (the workspace setting)…")
    else:
        typer.echo(f"Translating into {lang.name}…")
    try:
        res = translate_tree(cd, language=lang, dry_run=dry_run, limit=limit,
                            repo_name=Path(root).resolve().name, printer=typer.echo)
    except KeyboardInterrupt:
        typer.echo("\n✗ interrupted — nodes translated so far are saved; re-run to "
                   "continue where it stopped.", err=True)
        raise typer.Exit(code=130)

    if not res.translated and not res.skipped:
        typer.echo(f"Nothing to translate — all {res.already} node(s) already read as "
                   f"{lang.name}.")
        return
    typer.echo(("(dry run) " if dry_run else "✓ ") + res.summary())
    for before, after in res.preview:
        typer.echo(f"    {before}  →  {after}")
    if res.skipped:
        typer.echo(f"\n{len(res.skipped)} node(s) left in their original language:")
        for s in res.skipped[:15]:
            typer.echo(f"  · {s.title or s.feature_id}: {s.reason}")
        if len(res.skipped) > 15:
            typer.echo(f"  … and {len(res.skipped) - 15} more")
        typer.echo("  Re-run to retry them, or edit those nodes by hand.")
    if dry_run and res.translated:
        typer.echo("\nNothing was written. Re-run without --dry-run to apply.")


def _echo_language_mix(codoc_dir: str, current) -> None:
    """Report which languages the tree is ACTUALLY written in, next to the setting.

    The setting says what codoc authors in; it does not say what is on the page. A
    tree can be deliberately bilingual, and it can also be accidentally bilingual —
    someone ran a few passes before setting the language, or a stale env override
    was in play for a session. Those look identical in `config.json` and completely
    different in the tree, so the only useful answer to "what language is this repo
    in" counts the nodes.
    """
    from collections import Counter

    from codoc.doclang import language_tag_for
    from codoc.store.db import open_store

    try:
        with open_store(codoc_dir) as store:
            tags = Counter(language_tag_for(f.description or f.title, current)
                           for f in store.list_features())
    except Exception:  # noqa: BLE001 — advisory; never break `codoc lang`
        return
    if not tags:
        return
    total = sum(tags.values())
    parts = [f"{tag} {n}" for tag, n in tags.most_common()]
    typer.echo(f"  nodes by language: {', '.join(parts)}  ({total} total)")
    off = total - tags.get(current.code, 0)
    if off:
        typer.echo(f"  {off} node(s) are not in {current.code}. That is fine if you "
                   "meant it — an amend keeps each node's own language, so nothing "
                   "will rewrite them.")


@app.command(name="install-hooks")
def install_hooks_cmd(
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Install the codoc Claude Code plugin: hooks, the codoc-intent skill, the
    /codoc:* commands, and the MCP server registration.

    Idempotent — safe to run multiple times, and the way to wire up a fresh clone
    without re-bootstrapping the tree.
    """
    from codoc.agent.install_hooks import install_hooks

    commands = install_hooks(root)
    typer.echo("✓ Claude Code plugin installed")
    typer.echo("  Hooks (.claude/settings.json): SessionStart, Stop, SessionEnd, "
               "PreToolUse(Edit|Write|Read), PostToolUse(Edit|Write), UserPromptSubmit")
    typer.echo("  Skill: .claude/skills/codoc-intent/SKILL.md")
    # What was actually written, not a list kept by hand: this line read
    # "/codoc:plan, /codoc:sync" for months after /codoc:ask shipped.
    typer.echo(f"  Commands: {', '.join(commands) or 'none'}")
    typer.echo("  MCP server: registered in .mcp.json")


if __name__ == "__main__":
    app()
