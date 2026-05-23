"""Write a .codoc/tree/_index.bindings.json sidecar with binding details per feature.

Bindings are authoritative in SQLite; this sidecar lets the VSCode extension render
binding summaries (count, primary file, state) without hitting the API on every render.
Not parsed back — the DB is canonical.
"""
from __future__ import annotations

import json
from pathlib import Path

_SIDECAR_FILENAME = "_index.bindings.json"


def write_bindings_sidecar(codoc_dir: str, store) -> None:
    """Write per-feature binding metadata to .codoc/tree/_index.bindings.json."""
    tree_dir = Path(codoc_dir) / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, list[dict]] = {}

    try:
        features = store.list_features()
    except Exception:
        return

    for feature in features:
        if feature.retired:
            continue
        try:
            bindings = store.list_bindings(feature.uuid)
        except Exception:
            continue
        if not bindings:
            continue

        entries = []
        for b in bindings:
            sym = b.anchor.symbol_path or ""
            entries.append({
                "uuid": b.uuid,
                "file": b.anchor.file or "",
                "symbol": sym,
            })
        result[feature.uuid] = entries

    content = json.dumps(result, indent=2)
    target = tree_dir / _SIDECAR_FILENAME
    if target.exists():
        try:
            if target.read_text(encoding="utf-8") == content:
                return
        except OSError:
            pass
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
