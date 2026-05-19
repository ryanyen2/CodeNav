"""codoc top-level commands — plain-English verbs that accept slug-paths.

These replace the old `codoc tx *` / `codoc feature *` namespaces.
The old commands remain as hidden aliases with a deprecation notice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

from codoc.cli._utils import require_codoc_dir

# ---------------------------------------------------------------------------
# Typer app (commands registered on the root app in main.py)
# ---------------------------------------------------------------------------

# Not a sub-app; each function is registered directly on `app` in main.py.


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _open_stores(codoc_dir: Path):
    from codoc.pipelines.intentional.runner import open_stores
    return open_stores(str(codoc_dir))


def _resolve_feature(ref: str, codoc_dir: Path):
    from codoc.core.refs import resolve_feature_ref, NotFoundRef, AmbiguousRef
    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        return resolve_feature_ref(ref, store), store
    except (NotFoundRef, AmbiguousRef) as exc:
        store.close()
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _resolve_proposal(ref: str, codoc_dir: Path):
    from codoc.core.refs import resolve_tx_ref
    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        tx = resolve_tx_ref(ref, store)
        return tx, store
    except ValueError as exc:
        store.close()
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _state_color(state: str) -> str:
    colors = {
        "Stable": "green",
        "Drafting": "yellow",
        "Strained": "bright_yellow",
        "Stub": "white",
        "Deprecated": "dim",
        "Severed": "red",
    }
    return colors.get(state, "white")


def _state_badge(feature, store) -> str:
    from codoc.core.state_derivation import compute_feature_state, BindingResolution
    bindings = store.list_bindings(feature.uuid)
    obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")
    state = compute_feature_state(feature, bindings, [], obligations)
    return state.value.capitalize()


def _binding_count(feature_uuid: str, store) -> int:
    return len(store.list_bindings(feature_uuid))


# ---------------------------------------------------------------------------
# codoc list
# ---------------------------------------------------------------------------


def cmd_list(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
    state: Optional[str] = typer.Option(None, "--state", "-s", help="Filter by state (stable, drafting, strained, stub, deprecated, severed)"),
    flat: bool = typer.Option(False, "--flat", help="Show as flat list instead of tree"),
    bindings_file: Optional[str] = typer.Option(None, "--bindings", "-b", help="Show only features that bind to this file"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, tsv"),
) -> None:
    """Browse all features in the tree."""
    codoc_dir = require_codoc_dir(root_dir)

    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        pairs = store.list_features_with_slug_paths()
        if not pairs:
            typer.echo("No features yet. Run `codoc bootstrap` to seed the tree.")
            return

        # Preload all bindings once to avoid N+1 queries.
        all_bindings = store.get_all_bindings()
        binding_counts: dict[str, int] = {}
        for b in all_bindings:
            binding_counts[b.feature_uuid] = binding_counts.get(b.feature_uuid, 0) + 1

        # Apply filters.
        if state:
            pairs = [(f, p) for f, p in pairs if _state_badge(f, store).lower() == state.lower()]
        if bindings_file:
            bound_uuids = {b.feature_uuid for b in all_bindings if b.anchor.file == bindings_file}
            pairs = [(f, p) for f, p in pairs if f.uuid in bound_uuids]

        if format == "json":
            out = [
                {
                    "uuid": feat.uuid,
                    "slug_path": slug_path,
                    "slug": feat.slug,
                    "state": _state_badge(feat, store),
                    "retired": feat.retired,
                    "intent": feat.intent,
                    "binding_count": binding_counts.get(feat.uuid, 0),
                }
                for feat, slug_path in pairs
            ]
            typer.echo(json.dumps(out, indent=2))
            return

        if format == "tsv":
            typer.echo("slug_path\tstate\tbindings\tintent")
            for feat, slug_path in pairs:
                badge = _state_badge(feat, store)
                count = binding_counts.get(feat.uuid, 0)
                intent_short = (feat.intent or "")[:60].replace("\t", " ")
                typer.echo(f"{slug_path}\t{badge}\t{count}\t{intent_short}")
            return

        # Default: Rich table or plain fallback.
        _print_feature_table(pairs, store, flat=flat, binding_counts=binding_counts)

    finally:
        store.close()


def _print_feature_table(pairs, store, flat: bool = False, binding_counts: dict | None = None) -> None:
    def _count(uuid: str) -> int:
        return binding_counts.get(uuid, 0) if binding_counts is not None else _binding_count(uuid, store)

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        from codoc.core.prefix import unambiguous_uuid_prefix

        console = Console()
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Feature", style="bold", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Bind", justify="right")
        table.add_column("Intent", overflow="fold", max_width=60)

        all_uuids = [f.uuid for f, _ in pairs]

        for feat, slug_path in pairs:
            badge = _state_badge(feat, store)
            count = _count(feat.uuid)
            prefix = unambiguous_uuid_prefix(feat.uuid, all_uuids)

            if flat:
                display = slug_path
            else:
                indent = "  " * slug_path.count("/")
                slug_display = f"[dim]{feat.slug}[/dim]" if feat.retired else feat.slug
                display = f"{indent}[dim]{prefix}[/dim]  {slug_display}"

            color = _state_color(badge)
            table.add_row(display, f"[{color}]{badge}[/{color}]", str(count), (feat.intent or "")[:80])

        console.print(table)
    except ImportError:
        typer.echo(f"{'Feature':<45}  {'State':<12}  {'Bind':>4}  {'Intent'}")
        typer.echo("-" * 100)
        for feat, slug_path in pairs:
            badge = _state_badge(feat, store)
            typer.echo(f"{slug_path:<45}  {badge:<12}  {_count(feat.uuid):>4}  {(feat.intent or '')[:60]}")


# ---------------------------------------------------------------------------
# codoc show REF
# ---------------------------------------------------------------------------


def cmd_show(
    ref: str = typer.Argument(..., help="Feature slug-path, slug, or UUID prefix"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json, plain"),
) -> None:
    """Show a feature's details, state, and bindings."""
    codoc_dir = require_codoc_dir(root_dir)
    feature, store = _resolve_feature(ref, codoc_dir)

    try:
        from codoc.core.refs import slug_path_for
        slug_path = slug_path_for(feature.uuid, store)
        bindings = store.list_bindings(feature.uuid)
        badge = _state_badge(feature, store)
        recent_txs = store.list_transactions(proposal=False, limit=5)
        feat_txs = [t for t in recent_txs
                    if t.payload.get("feature_uuid") == feature.uuid
                    or t.payload.get("affected_feature_uuid") == feature.uuid]

        if format == "json":
            typer.echo(json.dumps({
                "uuid": feature.uuid,
                "slug_path": slug_path,
                "slug": feature.slug,
                "state": badge,
                "retired": feature.retired,
                "intent": feature.intent,
                "created_at": feature.created_at_hlc.to_str(),
                "updated_at": feature.updated_at_hlc.to_str(),
                "bindings": [
                    {
                        "id": b.uuid[:8],
                        "file": b.anchor.file,
                        "symbol": b.anchor.symbol_path or b.anchor.ts_query,
                    }
                    for b in bindings
                ],
            }, indent=2))
            return

        _print_feature_detail(feature, slug_path, badge, bindings, feat_txs, plain=(format == "plain"))
    finally:
        store.close()


def _print_feature_detail(feature, slug_path, badge, bindings, recent_txs, plain: bool = False) -> None:
    color = _state_color(badge)
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich import box

        if plain:
            raise ImportError

        console = Console()
        header = f"[bold]{slug_path}[/bold]  [{color}]{badge}[/{color}]"
        if feature.retired:
            header += "  [dim](retired)[/dim]"
        console.print(Panel(header, expand=False))
        console.print(f"[dim]UUID:[/dim] {feature.uuid}")
        console.print()
        if feature.intent:
            console.print(f"[bold]Intent[/bold]\n{feature.intent}")
        else:
            console.print("[dim]No intent set.[/dim]")
        console.print()

        if bindings:
            btable = Table(box=box.SIMPLE, show_header=True, header_style="dim")
            btable.add_column("id", style="dim", no_wrap=True)
            btable.add_column("file")
            btable.add_column("symbol")
            for b in bindings:
                sym = b.anchor.symbol_path or b.anchor.ts_query or ""
                btable.add_row(b.uuid[:8], b.anchor.file, sym)
            console.print(f"[bold]Bindings[/bold] ({len(bindings)})")
            console.print(btable)
        else:
            console.print("[dim]No bindings.[/dim]")

    except ImportError:
        typer.echo(f"Feature : {slug_path}  [{badge}]")
        typer.echo(f"UUID    : {feature.uuid}")
        typer.echo(f"Retired : {feature.retired}")
        typer.echo(f"Intent  :\n  {feature.intent or '(none)'}")
        typer.echo(f"\nBindings ({len(bindings)}):")
        for b in bindings:
            sym = b.anchor.symbol_path or b.anchor.ts_query or ""
            typer.echo(f"  [{b.uuid[:8]}]  {b.anchor.file}  {sym}")


# ---------------------------------------------------------------------------
# codoc proposals
# ---------------------------------------------------------------------------


def cmd_proposals(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
    all_: bool = typer.Option(False, "--all", help="Show all (not just pending)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows"),
    format: str = typer.Option("table", "--format", "-f", help="Output: table, json, tsv"),
) -> None:
    """List pending proposals from the reflective pipeline."""
    codoc_dir = require_codoc_dir(root_dir)
    store, _, _ = _open_stores(codoc_dir)

    try:
        txs = store.list_transactions(proposal=(not all_), limit=limit)
    finally:
        store.close()

    if not txs:
        typer.echo("No pending proposals. Your tree is up to date.")
        return

    if format == "json":
        typer.echo(json.dumps([{
            "hlc": t.hlc.to_str(),
            "kind": t.kind.value,
            "payload": t.payload,
            "label": t.label,
        } for t in txs], indent=2))
        return

    # Build display rows with unambiguous HLC prefixes.
    from codoc.core.prefix import unambiguous_hlc_prefix
    all_hlcs = [t.hlc.to_str() for t in txs]

    _print_proposals_table(txs, all_hlcs, format)


def _print_proposals_table(txs, all_hlcs, format: str = "table") -> None:
    from codoc.core.prefix import unambiguous_hlc_prefix

    rows = []
    for tx in txs:
        hlc_prefix = unambiguous_hlc_prefix(tx.hlc.to_str(), all_hlcs)
        kind = _friendly_kind(tx.kind.value)
        payload = tx.payload
        slug = payload.get("slug") or payload.get("new_slug") or ""
        # Fall back to feature_uuid prefix when no slug (e.g. REATTRIBUTE)
        if not slug:
            slug = payload.get("feature_uuid", "")[:12]
        label = tx.label or ""
        rows.append((slug, kind, hlc_prefix, label))

    if format == "tsv":
        typer.echo("slug\tkind\thlc_prefix\tlabel")
        for row in rows:
            typer.echo("\t".join(row))
        return

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        table = Table(box=box.SIMPLE_HEAD, show_header=True)
        table.add_column("Slug", no_wrap=True)
        table.add_column("Kind", no_wrap=True, style="dim")
        table.add_column("HLC prefix", style="dim", no_wrap=True)
        table.add_column("Label", style="dim")
        for row in rows:
            table.add_row(*row)
        console.print(table)
        console.print(
            f"[dim]{len(txs)} proposal(s). "
            "Use [bold]`codoc accept <slug>`[/bold] or [bold]`codoc accept --all-pending`[/bold].[/dim]"
        )
    except ImportError:
        header = f"{'Slug':<45}  {'Kind':<22}  {'HLC prefix':<24}  Label"
        typer.echo(header)
        typer.echo("-" * len(header))
        for row in rows:
            typer.echo(f"{row[0]:<45}  {row[1]:<22}  {row[2]:<24}  {row[3]}")


def _friendly_kind(kind: str) -> str:
    mapping = {
        "INTRODUCE": "introduce",
        "ABSORB": "absorb",
        "EVICT": "evict",
        "RETIRE_REFLECTIVE": "retire (auto)",
        "REATTRIBUTE": "reattribute",
        "FRACTURE": "fracture",
        "COALESCE": "coalesce",
        "RENAME_INFER": "rename (auto)",
        "AMEND": "edit",
        "RENAME": "rename",
        "RETIRE": "retire",
        "SPLIT": "split",
        "MERGE": "merge",
        "RESTRUCTURE": "move",
        "REWIND": "undo",
    }
    return mapping.get(kind, kind.lower())


# ---------------------------------------------------------------------------
# codoc accept REF
# ---------------------------------------------------------------------------


def cmd_accept(
    ref: str = typer.Argument(default="", help="Proposal slug, HLC prefix, feature slug-path, or 'all'"),
    all_pending: bool = typer.Option(False, "--all-pending", help="Accept all pending proposals"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Label for the gate: accept-verbatim | accept-light-edit | accept-heavy-edit"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Accept a pending proposal and apply its changes."""
    if not ref and not all_pending:
        typer.echo("Error: provide a proposal ref or use --all-pending.", err=True)
        raise typer.Exit(code=1)

    codoc_dir = require_codoc_dir(root_dir)
    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    try:
        if all_pending or ref == "all":
            txs = store.list_transactions(proposal=True, limit=0)
            if not txs:
                typer.echo("No pending proposals.")
                return
            _accept_many(txs, store, jsonl_log, tx_log, codoc_dir, label=label)
            return

        tx, _ = _resolve_proposal(ref, codoc_dir)
        # Re-open via tx_log since _resolve_proposal opens its own store.
        _accept_one(tx.hlc.to_str(), store, jsonl_log, tx_log, codoc_dir, label=label)
    finally:
        store.close()


def _accept_one(hlc: str, store, jsonl_log, tx_log, codoc_dir: Path, label=None) -> None:
    from codoc.cli.tx import _apply_accepted_transaction

    tx = store.get_transaction(hlc)
    if tx is None:
        typer.echo(f"Error: proposal {hlc!r} not found.", err=True)
        raise typer.Exit(code=1)
    if not tx.proposal:
        typer.echo(f"Error: {hlc[:16]}… is already accepted.", err=True)
        raise typer.Exit(code=1)
    try:
        _apply_accepted_transaction(tx, store)
        accepted = tx_log.accept_proposal(hlc)
        if label:
            _valid_labels = {"accept-verbatim", "accept-light-edit", "accept-heavy-edit", "reject"}
            if label not in _valid_labels:
                typer.echo(f"Warning: unknown label {label!r}. Valid: {', '.join(sorted(_valid_labels))}", err=True)
            else:
                store.set_label(accepted.hlc.to_str(), label)
        jsonl_log.append(accepted)
        kind = _friendly_kind(tx.kind.value)
        typer.echo(f"Accepted  {kind}  {tx.hlc.to_str()[:16]}…")
    except Exception as exc:
        typer.echo(f"Error accepting proposal: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _accept_many(txs, store, jsonl_log, tx_log, codoc_dir: Path, label=None) -> None:
    ok, failed = 0, 0
    for tx in txs:
        try:
            _accept_one(tx.hlc.to_str(), store, jsonl_log, tx_log, codoc_dir, label=label)
            ok += 1
        except typer.Exit:
            failed += 1
    typer.echo(f"\nAccepted {ok} proposal(s). {failed} failed.")


# ---------------------------------------------------------------------------
# codoc reject REF
# ---------------------------------------------------------------------------


def cmd_reject(
    ref: str = typer.Argument(default="", help="Proposal ref (HLC prefix) or 'all'"),
    all_pending: bool = typer.Option(False, "--all-pending", help="Reject ALL pending proposals"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Reject (delete) a pending proposal."""
    codoc_dir = require_codoc_dir(root_dir)

    if all_pending or ref == "all":
        store, _, tx_log = _open_stores(codoc_dir)
        try:
            txs = store.list_transactions(proposal=True, limit=0)
        finally:
            store.close()
        if not txs:
            typer.echo("No pending proposals.")
            return
        if not yes:
            typer.confirm(f"Reject ALL {len(txs)} pending proposal(s)?", abort=True)
        _, _, tx_log2 = _open_stores(codoc_dir)
        ok = 0
        for tx in txs:
            try:
                tx_log2.reject_proposal(tx.hlc.to_str())
                kind = _friendly_kind(tx.kind.value)
                typer.echo(f"Rejected  {kind}  {tx.hlc.to_str()[:16]}…")
                ok += 1
            except Exception as exc:
                typer.echo(f"Error rejecting {tx.hlc.to_str()[:16]}: {exc}", err=True)
        typer.echo(f"\nRejected {ok} proposal(s).")
        return

    if not ref:
        typer.echo("Error: provide a proposal ref or use --all-pending.", err=True)
        raise typer.Exit(code=1)

    tx, store = _resolve_proposal(ref, codoc_dir)

    try:
        if not tx.proposal:
            typer.echo(f"Error: {ref!r} is already accepted, cannot reject.", err=True)
            raise typer.Exit(code=1)

        kind = _friendly_kind(tx.kind.value)
        if not yes:
            typer.confirm(
                f"Reject proposal [{kind}] {tx.hlc.to_str()[:16]}…?",
                abort=True,
            )

        _, _, tx_log = _open_stores(codoc_dir)
        tx_log.reject_proposal(tx.hlc.to_str())
        typer.echo(f"Rejected  {kind}  {tx.hlc.to_str()[:16]}…")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# codoc edit REF
# ---------------------------------------------------------------------------


def cmd_edit(
    ref: str = typer.Argument(..., help="Feature slug-path, slug, or UUID prefix"),
    intent: Optional[str] = typer.Option(None, "--intent", "-i", help="New intent (skips editor)"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Edit a feature's intent prose."""
    codoc_dir = require_codoc_dir(root_dir)
    feature, store = _resolve_feature(ref, codoc_dir)
    store.close()

    current_intent = feature.intent
    new_intent: str | None = intent

    if new_intent is None:
        try:
            import click
            edited = click.edit(current_intent or "", require_save=True)
            if edited is not None:
                new_intent = edited.strip()
        except Exception:
            pass

    if new_intent is None:
        typer.echo(f"Current intent: {current_intent!r}")
        new_intent = typer.prompt("New intent", default=current_intent or "")
        new_intent = new_intent.strip()

    if not new_intent:
        typer.echo("No changes made (empty intent).")
        return

    if new_intent == (current_intent or "").strip():
        typer.echo("No changes made (intent unchanged).")
        return

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner
        author = os.environ.get("CODOC_AUTHOR", "user")
        with IntentionalRunner(str(codoc_dir), author=author) as runner:
            tx = runner.amend(feature.uuid, new_intent)
    except Exception as exc:
        typer.echo(f"Error: could not edit feature — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    from codoc.core.refs import slug_path_for
    s, store2 = _resolve_feature(feature.uuid, codoc_dir)
    try:
        slug_path = slug_path_for(feature.uuid, store2)
    finally:
        store2.close()
    typer.echo(f"Updated intent for {slug_path}.")


# ---------------------------------------------------------------------------
# codoc rename REF NEW_SLUG
# ---------------------------------------------------------------------------


def cmd_rename(
    ref: str = typer.Argument(..., help="Feature slug-path, slug, or UUID prefix"),
    new_slug: str = typer.Argument(..., help="New slug"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Rename a feature (change its slug)."""
    codoc_dir = require_codoc_dir(root_dir)
    feature, store = _resolve_feature(ref, codoc_dir)
    store.close()

    new_slug = new_slug.strip()
    if not new_slug:
        typer.echo("Error: new slug cannot be empty.", err=True)
        raise typer.Exit(code=1)

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner
        author = os.environ.get("CODOC_AUTHOR", "user")
        with IntentionalRunner(str(codoc_dir), author=author) as runner:
            runner.rename(feature.uuid, new_slug)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"Error: rename failed — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Renamed '{feature.slug}' → '{new_slug}'.")


# ---------------------------------------------------------------------------
# codoc retire REF
# ---------------------------------------------------------------------------


def cmd_retire(
    ref: str = typer.Argument(..., help="Feature slug-path, slug, or UUID prefix"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Retire a feature (marks it inactive; bindings are preserved)."""
    codoc_dir = require_codoc_dir(root_dir)
    feature, store = _resolve_feature(ref, codoc_dir)

    try:
        from codoc.core.refs import slug_path_for
        slug_path = slug_path_for(feature.uuid, store)
    finally:
        store.close()

    if feature.retired:
        typer.echo(f"Feature '{slug_path}' is already retired.")
        return

    if not yes:
        typer.confirm(f"Retire '{slug_path}'?", abort=True)

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner
        author = os.environ.get("CODOC_AUTHOR", "user")
        with IntentionalRunner(str(codoc_dir), author=author) as runner:
            runner.retire(feature.uuid)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"Error: retire failed — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Retired '{slug_path}'.")


# ---------------------------------------------------------------------------
# codoc search TERM
# ---------------------------------------------------------------------------


def cmd_search(
    term: str = typer.Argument(..., help="Search term (matches slug and intent)"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
    format: str = typer.Option("table", "--format", "-f", help="Output: table, json"),
) -> None:
    """Search features by slug or intent prose."""
    codoc_dir = require_codoc_dir(root_dir)

    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        pairs = store.list_features_with_slug_paths()
    finally:
        store.close()

    term_lower = term.lower()
    results = [
        (f, p) for f, p in pairs
        if term_lower in f.slug.lower() or term_lower in (f.intent or "").lower()
    ]

    if not results:
        typer.echo(f"No features matching {term!r}.")
        return

    if format == "json":
        typer.echo(json.dumps([{"slug_path": p, "uuid": f.uuid, "intent": f.intent} for f, p in results], indent=2))
        return

    # Re-open for state computation.
    store2 = SQLiteStore(str(codoc_dir / "codoc.db"))
    store2.open()
    try:
        _print_feature_table(results, store2)
    finally:
        store2.close()


# ---------------------------------------------------------------------------
# codoc status
# ---------------------------------------------------------------------------


def cmd_status(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory"),
) -> None:
    """Show a quick summary: feature count, pending proposals, last sync."""
    codoc_dir = require_codoc_dir(root_dir)
    store, _, _ = _open_stores(codoc_dir)

    try:
        all_features = store.list_features(parent_uuid=None)
        active = [f for f in all_features if not f.retired]
        retired = [f for f in all_features if f.retired]
        pending = store.list_transactions(proposal=True, limit=0)
        accepted = store.list_transactions(proposal=False, limit=1)
        last_hlc = accepted[0].hlc.to_str()[:20] if accepted else "—"
    finally:
        store.close()

    meta_path = codoc_dir / "tree" / "tree.meta.json"
    render_info = ""
    if meta_path.exists():
        try:
            import json as _json
            meta = _json.loads(meta_path.read_text())
            render_info = f"  Tree rendered at: {meta.get('rendered_at', '?')[:19]}"
        except Exception:
            pass

    typer.echo(f"Features   : {len(active)} active, {len(retired)} retired")
    typer.echo(f"Proposals  : {len(pending)} pending")
    typer.echo(f"Last change: {last_hlc}")
    if render_info:
        typer.echo(render_info)
    if pending:
        typer.echo(f"\nRun `codoc proposals` to review.")
        typer.echo(f"  Accept all: `codoc accept --all-pending`")
        typer.echo(f"  Reject all: `codoc reject --all-pending --yes`")
