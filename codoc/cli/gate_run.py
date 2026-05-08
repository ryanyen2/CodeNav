"""codoc gate-run — compute validation gate metrics from labeled proposals."""

from __future__ import annotations

from pathlib import Path

import typer

_VERBATIM = "accept-verbatim"
_LIGHT = "accept-light-edit"
_HEAVY = "accept-heavy-edit"
_REJECT = "reject"

# Pass thresholds (Q12 of design doc):
_THRESHOLD_VERBATIM = 0.60       # accept-verbatim >= 60% of labeled
_THRESHOLD_VERBATIM_LIGHT = 0.80  # (accept-verbatim + accept-light-edit) >= 80% of labeled


def gate_run(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    report: bool = typer.Option(False, "--report", is_flag=True, help="Print per-transaction breakdown"),
) -> None:
    """Compute validation gate metrics from labeled proposal transactions.

    Pass thresholds:
    - accept-verbatim >= 60% of labeled transactions
    - (accept-verbatim + accept-light-edit) >= 80% of labeled transactions
    - Median Levenshtein distance of light edits <= 80 chars (Phase 1: N/A)

    Prints PASS or FAIL with metric breakdown.
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"
    if not codoc_dir.exists():
        typer.echo(
            f"Error: .codoc/ not found at {codoc_dir}. Run 'codoc init' first.", err=True
        )
        raise typer.Exit(code=1)

    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        # Load all transactions that have a non-null label.
        all_txs = store.list_transactions(proposal=None, limit=0)
    finally:
        store.close()

    labeled = [tx for tx in all_txs if tx.label is not None and tx.label != ""]

    if not labeled:
        typer.echo("No labeled transactions found. Label proposals with 'codoc tx label' first.")
        raise typer.Exit(code=0)

    total = len(labeled)
    buckets: dict[str, list] = {
        _VERBATIM: [],
        _LIGHT: [],
        _HEAVY: [],
        _REJECT: [],
    }
    for tx in labeled:
        bucket_key = tx.label if tx.label in buckets else _REJECT
        buckets[bucket_key].append(tx)

    verbatim_count = len(buckets[_VERBATIM])
    light_count = len(buckets[_LIGHT])
    heavy_count = len(buckets[_HEAVY])
    reject_count = len(buckets[_REJECT])

    ratio_verbatim = verbatim_count / total
    ratio_verbatim_light = (verbatim_count + light_count) / total

    passes_verbatim = ratio_verbatim >= _THRESHOLD_VERBATIM
    passes_verbatim_light = ratio_verbatim_light >= _THRESHOLD_VERBATIM_LIGHT
    # Phase 1: edit-distance tracking not yet implemented.
    levenshtein_note = "N/A - edit distance tracking not yet implemented"

    overall_pass = passes_verbatim and passes_verbatim_light

    typer.echo("=" * 60)
    typer.echo("Validation Gate Report")
    typer.echo("=" * 60)
    typer.echo(f"Labeled transactions: {total}")
    typer.echo(f"  accept-verbatim   : {verbatim_count}  ({ratio_verbatim:.1%})")
    typer.echo(f"  accept-light-edit : {light_count}  ({light_count / total:.1%})")
    typer.echo(f"  accept-heavy-edit : {heavy_count}  ({heavy_count / total:.1%})")
    typer.echo(f"  reject            : {reject_count}  ({reject_count / total:.1%})")
    typer.echo("")
    typer.echo("Thresholds:")
    _check_line(
        f"  accept-verbatim >= {_THRESHOLD_VERBATIM:.0%}",
        passes_verbatim,
        f"({ratio_verbatim:.1%})",
    )
    _check_line(
        f"  (verbatim + light) >= {_THRESHOLD_VERBATIM_LIGHT:.0%}",
        passes_verbatim_light,
        f"({ratio_verbatim_light:.1%})",
    )
    typer.echo(f"  Median Levenshtein <= 80 chars: {levenshtein_note}")

    typer.echo("")
    if overall_pass:
        typer.echo("Result: PASS")
    else:
        typer.echo("Result: FAIL")

    if report and labeled:
        typer.echo("")
        typer.echo("Per-transaction breakdown:")
        typer.echo(f"  {'HLC':<32}  {'Kind':<22}  Label")
        typer.echo(f"  {'-'*32}  {'-'*22}  -----")
        for tx in labeled:
            hlc_short = tx.hlc.to_str()[:30]
            kind = tx.kind.value
            typer.echo(f"  {hlc_short:<32}  {kind:<22}  {tx.label}")

    if not overall_pass:
        raise typer.Exit(code=1)


def _check_line(description: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    typer.echo(f"{description}: {status} {detail}")
