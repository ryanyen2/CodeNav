"""codoc: the command line.

    codoc init        index the repo, build the first tree, install the agent hooks
    codoc watch       the daemon: reflect code into the tree, apply edits, publish
    codoc status      where the tree and the code stand
    codoc sync        one pass: drain edits, reflect changed code, publish
    codoc realize     implement the queued hand-offs now, foreground
    codoc handoff     hand a feature's edits to the agent (the CLI's ⌘S)
    codoc accept      accept a plan            codoc reject   reject a plan
    codoc history     one feature's history and attribution
    codoc lang        show or set the authoring language
    codoc translate   rewrite an existing tree into that language
    codoc rebuild     replay the logs into the cache
    codoc snapshot    fold the logs into one file
    codoc validate-logs   check log files this workspace did not write
    codoc serve       the hub: serve the tree to GitHub-authorized people
    codoc install-hooks
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import typer

from codoc.workspace.layout import CODOC_DIR, LOG_DIR, REALIZE

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
ROOT = typer.Option(".", "--root", help="Repository root.")


def _codoc(root: str) -> Path:
    return Path(root) / CODOC_DIR


def _require(root: str) -> Path:
    cd = _codoc(root)
    # The directory is what `init` makes unconditionally. `log/` is not: it
    # appears with the first op, and a repository with no indexable file is
    # bootstrapped without writing one.
    if not cd.is_dir():
        typer.echo(f"No codoc workspace at {cd}. Run `codoc init` first.", err=True)
        raise typer.Exit(1)
    return cd


@contextmanager
def _applier(cd: Path, refusal: str):
    """``client.applying`` for a command that must apply itself. A daemon
    alive under the lock (``DaemonRunning``) prints ``refusal`` and exits."""
    from codoc.daemon import client
    try:
        with client.applying(cd) as ws:
            yield ws
    except client.DaemonRunning:
        typer.echo(refusal, err=True)
        raise typer.Exit(1)
    finally:
        client.release(cd)


def _write_gitignore(cd: Path) -> None:
    (cd / ".gitignore").write_text(
        "# codoc: the log, the config, the export and the media blocks reference travel with\n"
        "# the repo; caches, channels and local state do not.\n"
        "*\n!.gitignore\n!config.json\n!tree.codoc\n!log/\n!log/**\n!media/\n!media/**\n", encoding="utf-8")


@app.callback()
def _main(version: bool = typer.Option(False, "--version", is_eager=True, help="Show the version and exit.")) -> None:
    if version:
        from importlib.metadata import PackageNotFoundError, version as v
        try:
            typer.echo(f"codoc {v('codoc')}")
        except PackageNotFoundError:
            typer.echo("codoc (not installed as a package)")
        raise typer.Exit()


@app.command()
def init(root: str = ROOT,
         force: bool = typer.Option(False, "--force", help="Rebuild even if a workspace exists (the old log is kept as log.old)."),
         hooks: bool = typer.Option(True, "--hooks/--no-hooks", help="Install the Claude Code hooks and MCP server."),
         doc_language: str = typer.Option("", "--doc-language", "--lang", help="Language to author the tree in (BCP-47)."),
         no_organize: bool = typer.Option(False, "--no-organize", help="Skip the theme-grouping pass.")):
    """Index the repo, build the first tree from the code, publish tree.codoc."""
    from codoc.daemon.pidfile import daemon_running
    cd = _codoc(root)
    populated = (cd / LOG_DIR).is_dir() and any((cd / LOG_DIR).glob("*.jsonl"))
    if populated and daemon_running(cd):
        typer.echo("A codoc daemon is watching this repo. Stop it before `codoc init`.", err=True)
        raise typer.Exit(1)
    if populated and not force:
        typer.echo("A codoc workspace already exists here. Re-run with --force to rebuild, or `codoc watch`.", err=True)
        raise typer.Exit(1)
    if force and (cd / LOG_DIR).is_dir():
        import shutil
        shutil.rmtree(cd / "log.old", ignore_errors=True)
        (cd / LOG_DIR).rename(cd / "log.old")
        shutil.rmtree(cd / "cache", ignore_errors=True)
    cd.mkdir(parents=True, exist_ok=True)
    _write_gitignore(cd)
    if doc_language:
        from codoc.doclang import resolve
        from codoc.workspace.config import write_config
        write_config(cd, doc_language=resolve(doc_language).code)
    typer.echo(f"Indexing {root} and bootstrapping the feature tree…")
    try:
        res = _bootstrap(root, cd, organize=not no_organize)
    except KeyboardInterrupt:
        typer.echo("\n✗ init interrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ init failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"✓ {res.summary()}")
    typer.echo(f"  Open {cd}/tree.codoc in VS Code, then run `codoc watch`.")
    if hooks:
        try:
            from codoc.claude.install_hooks import install_hooks
            cmds = install_hooks(root)
            typer.echo(f"  ✓ hooks, MCP server and {', '.join(cmds)} installed")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  ⚠ could not install hooks: {exc}", err=True)


def _bootstrap(root: str, cd: Path, *, organize: bool):
    from codoc.daemon.publish import publish
    from codoc.doclang import workspace_doc_language
    from codoc.index.graph import Graph
    from codoc.index.reader import read_all_chunks
    from codoc.index.runner import update_index
    from codoc.loops.bootstrap import BootstrapResult, bootstrap
    from codoc.workspace import Workspace
    from codoc.workspace.layout import GRAPH_CACHE, atomic_write
    update_index(root, str(cd))
    rows = read_all_chunks(str(cd))
    graph = Graph()
    graph.rebuild(rows)
    atomic_write(cd / GRAPH_CACHE, graph.dumps())
    ws = Workspace(cd)
    try:
        if not rows:
            typer.echo("  No supported source files found (Python and TypeScript are indexed).")
            publish(cd, ws.store, root_dir=root)
            return BootstrapResult()
        res = bootstrap(ws, rows, graph, repo_name=os.path.basename(os.path.abspath(root)), root_dir=root,
                        doc_language=workspace_doc_language(cd), organize=organize, printer=typer.echo)
        publish(cd, ws.store, root_dir=root)
        return res
    finally:
        ws.close()


@app.command()
def watch(root: str = ROOT,
          auto_realize: bool = typer.Option(False, "--auto-realize", help="Run the queue headless when no session is live.")):
    """The daemon: reflect code changes, apply edits, publish the derived files."""
    from codoc.daemon.daemon import Daemon
    cd = _require(root)
    after_batch = None
    if auto_realize:
        from codoc.claude.autorealize import auto_realizer
        after_batch = auto_realizer(root, str(cd))
    Daemon(root, cd, printer=typer.echo, after_batch=after_batch).run()


@app.command()
def status(root: str = ROOT):
    """Where the tree and the code stand."""
    from codoc.mcp.tools import read_status
    cd = _require(root)
    st = read_status(str(cd))
    typer.echo(f"{st['state']}  ·  {st['features']} features  ·  {st['queued']} queued  ·  "
               f"{st['edited']} edited  ·  {st['proposals']} proposal(s)")
    for q in st["queued_list"]:
        typer.echo(f"  queued  {q['feature_id']}  {q['title']}")
    for e in st["edited_list"]:
        typer.echo(f"  edited  {e['feature_id']}  {e['title']}")
    if st["dead_cites"]:
        typer.echo(f"  {st['dead_cites']} citation(s) no longer resolve to code")


@app.command()
def sync(root: str = ROOT):
    """One pass: drain client edits, reflect every changed file, publish."""
    from codoc.daemon.daemon import Daemon
    from codoc.daemon.pidfile import daemon_running
    cd = _require(root)
    if daemon_running(cd):
        typer.echo("A daemon is watching; it syncs continuously.")
        raise typer.Exit(0)
    d = Daemon(root, cd, printer=typer.echo)
    try:
        note = d.process_batch([])
        note2 = d.reflect(set())
        typer.echo(f"▸ {note}; {note2}")
        from codoc.daemon.publish import publish
        publish(cd, d.ws.store, root_dir=root)
    finally:
        d.ws.close()


def _resolve_feature(store, ref: str) -> str:
    from codoc.calculus.derive import title
    ref = ref.strip("⟨⟩")
    if ref in store.blocks:
        return ref
    hits = [h.id for h in store.headings() if ref.lower() in title(store, h.id).lower()]
    if len(hits) == 1:
        return hits[0]
    typer.echo(f"{'no' if not hits else len(hits)} feature(s) match {ref!r}", err=True)
    raise typer.Exit(1)


def _report(res: list[dict], verb: str) -> None:
    for r in res:
        if r.get("conflict"):
            typer.echo(f"✗ not {verb}: {r['conflict']}", err=True)
            raise typer.Exit(1)
        if r.get("op") is None:
            typer.echo("· submitted to the daemon (no ack yet)")
        else:
            typer.echo(f"✓ {verb} ({r['op']})")


@app.command()
def handoff(feature: str, root: str = ROOT,
            asked: str = typer.Option("", "--asked", help="Your own words for what you want built."),
            delete_code: bool = typer.Option(False, "--delete-code", help="For a deleted feature: remove its code too.")):
    """Hand a feature's edits to the agent (what ⌘S does in the editor)."""
    from codoc.calculus import ops as K
    from codoc.daemon import client
    cd = _require(root)
    ws = client.read(cd)
    fid = _resolve_feature(ws.store, feature)
    _report(client.write(cd, client="cli", root_dir=root, bodies=[{
        "kind": K.FEATURE_HANDOFF, "target": {"fid": fid},
        "payload": {"asked": asked, "delete_code": delete_code}}]), "handed off")


@app.command()
def accept(plan: str, root: str = ROOT,
           build: bool = typer.Option(None, "--build/--no-build",
                                      help="Hand off the plan's features (default: only for a plan opened with build, as codoc_propose does).")):
    """Accept a plan (the whole of it).

    By default it also hands off what the editor's Accept hands off
    (`derive.accept_handoffs`): the features of a plan opened with `build`
    (`codoc_propose`), which is a request to build. `--build` hands off any plan's features; `--no-build`
    takes the words without the work. A delete plan is never handed off from
    here: removing the code is `codoc handoff --delete-code`.
    """
    from codoc.calculus import ops as K
    from codoc.calculus.derive import accept_handoffs, plan_features
    from codoc.daemon import client
    cd = _require(root)
    ws = client.read(cd)
    pl = ws.store.plans.get(plan)
    bodies = [{"kind": K.PLAN_ACCEPT, "target": {"pid": plan}, "payload": {}}]
    fids: list[str] = []
    if pl is not None and pl.kind != "delete":
        fids = accept_handoffs(ws.store, pl) if build is None else plan_features(ws.store, pl) if build else []
    for fid in fids:
        bodies.append({"kind": K.FEATURE_HANDOFF, "target": {"fid": fid},
                       "payload": {"asked": f"Build the plan {plan}."}})
    _report(client.write(cd, client="cli", root_dir=root, bodies=bodies), "accepted")


@app.command()
def done(feature: str, root: str = ROOT, summary: str = typer.Option("", "--summary", help="What was built.")):
    """Mark a handed-off feature realized by hand (when the build moved no bound
    symbol, so the daemon saw no evidence)."""
    from codoc.calculus import ops as K
    from codoc.index.git import head_sha
    from codoc.daemon import client
    cd = _require(root)
    ws = client.read(cd)
    fid = _resolve_feature(ws.store, feature)
    _report(client.write(cd, client="cli", root_dir=root, bodies=[{
        "kind": K.FEATURE_REALIZED, "target": {"fid": fid},
        "payload": {"sha": head_sha(root), "summary": summary}}]), "marked done")


@app.command()
def reject(plan: str, root: str = ROOT):
    """Reject a plan."""
    from codoc.calculus import ops as K
    from codoc.daemon import client
    cd = _require(root)
    _report(client.write(cd, client="cli", root_dir=root,
                         bodies=[{"kind": K.PLAN_REJECT, "target": {"pid": plan}, "payload": {}}]), "rejected")


@app.command()
def history(feature: str, root: str = ROOT, limit: int = typer.Option(15, "--limit")):
    """One feature's history (who, when, what) and current attribution."""
    from codoc.mcp.tools import read_history
    from codoc.daemon import client
    cd = _require(root)
    fid = _resolve_feature(client.read(cd).store, feature)
    h = read_history(str(cd), fid, limit)
    typer.echo(f"{h['title']}  ⟨{fid}⟩")
    for e in h["ops"]:
        extra = f"  {e['text']!r}" if e.get("text") else ""
        typer.echo(f"  {e['at'][:20]}  {e['actor']:<16} {e['kind']:<16}{extra}")
    for bid, runs in h["attribution"].items():
        typer.echo(f"  {bid}: " + ", ".join(f"{r['actor']}[{r['start']}:{r['end']}]" for r in runs))


@app.command()
def lang(tag: str = typer.Argument("", help="BCP-47 tag to set; omit to show."), root: str = ROOT):
    """Show or set the language the tree is authored in (.codoc/config.json, tracked)."""
    from codoc.doclang import resolve, workspace_doc_language
    from codoc.workspace.config import write_config
    cd = _require(root)
    if tag:
        write_config(cd, doc_language=resolve(tag).code)
        typer.echo(f"✓ authoring language: {resolve(tag).name}. Existing prose is unchanged; "
                   "run `codoc translate` to convert it.")
    else:
        typer.echo(f"authoring language: {workspace_doc_language(cd).name}")


@app.command()
def translate(root: str = ROOT, to: str = typer.Option("", "--to"), dry_run: bool = typer.Option(False, "--dry-run"),
              limit: int = typer.Option(0, "--limit")):
    """Rewrite the tree's prose into the authoring language, block by block; human-written nodes become plans."""
    from codoc.doclang import resolve
    from codoc.daemon.publish import publish
    from codoc.loops.translate import translate_tree
    cd = _require(root)
    # Under the applier lock for the whole run, so a daemonless writer (the MCP
    # server) waits instead of applying into a store this run does not see.
    with _applier(cd, "Stop the daemon before translating (it is the applier).") as ws:
        res = translate_tree(ws, cd, language=resolve(to) if to else None, dry_run=dry_run, limit=limit,
                             printer=typer.echo)
        if not dry_run:
            publish(cd, ws.store, root_dir=root)
    typer.echo(f"✓ {res.summary()}")
    for s in res.skipped[:10]:
        typer.echo(f"  skipped {s.feature_id} {s.title!r}: {s.reason}")


@app.command()
def rebuild(root: str = ROOT):
    """Replay the logs into the cache and re-publish the derived files."""
    from codoc.daemon.publish import publish
    from codoc.workspace.validate import refused_verdicts
    cd = _require(root)
    with _applier(cd, "A daemon is watching; it rebuilds when HEAD moves. Stop it to rebuild by hand.") as ws:
        ws.rebuild()
        publish(cd, ws.store, root_dir=root)
        typer.echo(f"✓ {len(ws.store.headings())} features from {len(ws.store.ops)} ops")
        for p in ws.problems:
            typer.echo(f"  ⚠ {p.path}:{p.line}: {p.reason}")
        # A signal, not a gate: a recorded answer stands whatever the table says
        # today, so a count here means a log carries work this table would refuse.
        for actor, n in sorted(refused_verdicts(ws.store, ws.roles).items()):
            typer.echo(f"  ⚠ {n} applied op(s) by {actor} the roles table would refuse today")


@app.command("validate-logs")
def validate_logs_cmd(paths: list[str] = typer.Argument(None, help="Log files to check; default: what a merge would add."),
                      root: str = ROOT, base: str = typer.Option("origin/main", "--base", help="Merge base to diff against.")):
    """Check a pull request's session logs against the tracked roles table.

    A log file is a capability: replay honours the answer recorded in it, so a
    forged success in a stranger's log lands on every clone that rebuilds. Run
    this before merging one.
    """
    from codoc.workspace import log as L
    from codoc.workspace.validate import changed_logs, validate_logs
    from codoc.workspace import Roles, Workspace
    cd = _require(root)
    files = [Path(p) for p in paths] if paths else changed_logs(root, base)
    if not files:
        typer.echo("no session logs to check")
        return
    ws = Workspace(cd, readonly=True)
    recorded = [op for p in L.list_sessions(cd) for op in L.read_session(p).ops]
    findings = validate_logs(files, roles=Roles.load(cd), store=ws.store, recorded=recorded)
    for f in findings:
        typer.echo(f"  ✗ {f}")
    typer.echo(f"{'✗' if findings else '✓'} {len(files)} log file(s), {len(findings)} finding(s)")
    raise typer.Exit(1 if findings else 0)


@app.command()
def snapshot(root: str = ROOT):
    """Fold the logs into one snapshot file and archive the rest."""
    from codoc.workspace.snapshot import write_snapshot
    cd = _require(root)
    # Under the lock every other daemonless writer takes: a fold moves files,
    # and a second process applying mid-fold would write into a log being rearranged.
    with _applier(cd, "Stop the daemon before snapshotting.") as ws:
        path = write_snapshot(cd, ws.store, ws.clock)
    if path is None:
        typer.echo("Nothing to fold: the oldest op belongs to a feature with an open plan. "
                   "Accept or reject it, then snapshot again.")
        return
    typer.echo(f"✓ {path.name}")


@app.command()
def realize(root: str = ROOT, engine: str = typer.Option("auto", "--engine", help="auto | sdk | cli"),
            permission_mode: str = typer.Option("acceptEdits", "--permission-mode")):
    """Implement the queued hand-offs now, in the foreground."""
    import subprocess
    from codoc.claude.autorealize import find_claude
    from codoc.claude.sdk_realize import resolve_engine, run_sdk_realize, sdk_available
    cd = _require(root)
    if not (cd / REALIZE).exists():
        typer.echo("Nothing queued. Edit the tree and hand off (⌘S in the editor, or `codoc handoff`).")
        raise typer.Exit(0)
    engine = resolve_engine(engine)
    if engine == "sdk":
        if not sdk_available():
            typer.echo("claude-agent-sdk is not installed. Run pip install 'codoc[sdk]' or --engine cli", err=True)
            raise typer.Exit(2)
        raise typer.Exit(run_sdk_realize(root, str(cd), permission_mode=permission_mode, printer=typer.echo))
    claude = find_claude()
    if claude is None:
        typer.echo("`claude` CLI not found on PATH.", err=True)
        raise typer.Exit(2)
    raise typer.Exit(subprocess.call([claude, "-p", "/codoc:sync"], cwd=root))


@app.command(name="install-hooks")
def install_hooks_cmd(root: str = ROOT):
    """(Re)install the Claude Code hooks, the /codoc:* commands and the MCP registration."""
    from codoc.claude.install_hooks import install_hooks
    cmds = install_hooks(root)
    typer.echo(f"✓ installed {', '.join(cmds)} and the codoc MCP server")


@app.command()
def serve(root: str = ROOT,
          host: str = typer.Option("127.0.0.1", "--host", help="Bind address; remote reach is the tunnel, never 0.0.0.0."),
          port: int = typer.Option(8787, "--port"),
          static_dir: str = typer.Option(None, "--static-dir", help="The built editor bundle (default: auto-detected)."),
          tunnel: bool = typer.Option(False, "--tunnel", help="Expose over a cloudflared tunnel (needs GitHub auth configured)."),
          insecure_public: bool = typer.Option(False, "--i-understand-unauthenticated", hidden=True)):
    """Serve the tree to GitHub-authorized people from this machine, supervising the daemon."""
    from codoc.serve.github_auth import GithubAuthConfig, build_auth_context
    cd = _require(root)
    gh = GithubAuthConfig.from_env()
    exposed = tunnel or host not in ("127.0.0.1", "localhost", "::1")
    if exposed and gh is None and not insecure_public:
        typer.echo("Refusing to expose the hub without GitHub auth (CODOC_GITHUB_CLIENT_ID/SECRET, "
                   "CODOC_GITHUB_TOKEN, CODOC_SERVE_REPO); see docs/serve-deployment.md.", err=True)
        raise typer.Exit(2)
    import uvicorn
    from codoc.serve.app import build_app
    from codoc.serve.ratelimit import RateLimiter
    from codoc.serve.static import resolve_static_dir
    from codoc.serve.supervise import DaemonSupervisor, OwnershipError
    auth = build_auth_context(gh) if gh else None
    application = build_app(str(cd), static_dir=resolve_static_dir(static_dir), auth=auth,
                            rate_limiter=RateLimiter() if auth else None)
    tun, worker = None, None
    try:
        with DaemonSupervisor(root, str(cd)):
            if tunnel:
                from codoc.serve.tunnel import launch_tunnel
                tun = launch_tunnel(port, config=os.environ.get("CODOC_TUNNEL_CONFIG"))
            if gh and os.environ.get("CODOC_SERVE_PR", "1") != "0":
                # With GitHub configured the server decides what is built: each
                # hand-off becomes a PR from a sandboxed agent on a worktree.
                from codoc.serve.worker import Worker
                worker = Worker(root, str(cd), base=os.environ.get("CODOC_SERVE_BASE", "main"),
                                printer=typer.echo).start()
            typer.echo(f"codoc hub on http://{host}:{port}" + (" (GitHub auth on)" if auth else " (localhost, no auth)"))
            uvicorn.run(application, host=host, port=port, log_level="warning")
    except OwnershipError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(3)
    finally:
        if worker is not None:
            worker.stop()
        if tun is not None:
            tun.terminate()


if __name__ == "__main__":
    app()
