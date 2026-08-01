"""Extract code edges from indexed chunk rows.

Post-index pass: read_all_chunks → extract_edges → store.insert_edges.
Never runs concurrently with cocoindex (singleton constraint).

Edge kinds:
  contain  — synthesized from symbol_path nesting (MyClass.method → MyClass)
  call     — function/method call site
  import   — import statement
  inherit  — base class in class definition
  type_ref — type annotation reference

internal=1 means the dst_symbol was resolved to a known project symbol;
internal=0 keeps the edge for display but traversal follows internal=1 only.
"""
from __future__ import annotations

from codoc.lang import detect_language, get_adapter
from codoc.pipelines.indexing.reader import ChunkRow


def _file_to_module(file: str) -> str:
    """"requests/models.py" → "requests.models" """
    m = file.replace("\\", "/")
    for ext in (".py", ".ts", ".tsx", ".mts", ".cts"):
        if m.endswith(ext):
            m = m[: -len(ext)]
            break
    return m.replace("/", ".")


def _build_indices(
    rows: list[ChunkRow],
) -> tuple[dict[str, ChunkRow], dict[str, list[ChunkRow]], dict[str, str]]:
    """Build lookup indices for reference resolution.

    Returns:
        by_symbol      – {symbol_path → ChunkRow}
        by_leaf        – {leaf_name → [ChunkRow]}  (leaf = last dotted part after ::)
        file_to_module – {file → dotted_module_path}
    """
    by_symbol: dict[str, ChunkRow] = {}
    by_leaf: dict[str, list[ChunkRow]] = {}
    file_to_module: dict[str, str] = {}

    for r in rows:
        by_symbol[r.symbol_path] = r

        if "::" in r.symbol_path:
            qualified = r.symbol_path.split("::", 1)[1]
            leaf = qualified.rsplit(".", 1)[-1]
        else:
            leaf = r.symbol_path
        by_leaf.setdefault(leaf, []).append(r)

        if r.file not in file_to_module:
            file_to_module[r.file] = _file_to_module(r.file)

    return by_symbol, by_leaf, file_to_module


def _resolve(
    qualified_name: str,
    src_file: str,
    by_symbol: dict[str, ChunkRow],
    by_leaf: dict[str, list[ChunkRow]],
    file_to_module: dict[str, str],
) -> ChunkRow | None:
    """Resolve a qualified_name to a project ChunkRow, or None if external/unresolvable.

    Resolution order:
    1. Strip self/cls/super prefix (method calls).
    2. Exact symbol_path lookup.
    3. Leaf-name lookup — same-file preference first.
    4. Module-prefix match for dotted names.
    5. First project candidate (best guess).
    """
    parts = qualified_name.split(".")
    if parts and parts[0] in {"self", "cls", "super"}:
        parts = parts[1:]
    if not parts:
        return None

    leaf = parts[-1]
    # Skip dunder names, very short tokens, numeric literals
    if not leaf or leaf.startswith("__") or len(leaf) <= 1 or leaf.isdigit():
        return None

    candidates = by_leaf.get(leaf, [])
    if not candidates:
        return None

    # Same-file preference
    same_file = [c for c in candidates if c.file == src_file]
    if same_file:
        return same_file[0]

    if len(candidates) == 1:
        return candidates[0]

    # Module-prefix match for dotted names like "requests.models.Response"
    if len(parts) > 1:
        module_path = ".".join(parts[:-1]).lstrip(".")
        for c in candidates:
            mod = file_to_module.get(c.file, "")
            if mod == module_path or mod.endswith("." + module_path) or mod.endswith(module_path):
                return c

    return candidates[0]


def _contain_edges(rows: list[ChunkRow]) -> list[dict]:
    """Synthesize contain edges from symbol_path nesting structure.

    "auth.py::MyClass.login" → src=MyClass.login, dst=MyClass (contain)
    """
    edges = []
    for row in rows:
        if "::" not in row.symbol_path:
            continue
        file, qualified = row.symbol_path.split("::", 1)
        if "." not in qualified:
            continue
        parent_qualified = qualified.rsplit(".", 1)[0]
        parent_symbol = f"{file}::{parent_qualified}"
        edges.append(
            {
                "src_file": row.file,
                "src_symbol": row.symbol_path,
                "dst_name": parent_qualified,
                "dst_symbol": parent_symbol,
                "dst_file": row.file,
                "kind": "contain",
                "internal": 1,
            }
        )
    return edges


def _ref_edges_for_rows(
    target_rows: list[ChunkRow],
    by_symbol: dict[str, ChunkRow],
    by_leaf: dict[str, list[ChunkRow]],
    file_to_module: dict[str, str],
) -> list[dict]:
    edges = []
    for row in target_rows:
        lang = detect_language(row.file)
        if lang is None:
            continue
        try:
            adapter = get_adapter(lang)
            refs = adapter.references_in_chunk(row.source, row.file)
        except Exception:
            continue

        seen: set[tuple[str, str, str]] = set()
        for ref in refs:
            key = (row.symbol_path, ref.qualified_name, ref.ref_kind)
            if key in seen:
                continue
            seen.add(key)

            target = _resolve(ref.qualified_name, row.file, by_symbol, by_leaf, file_to_module)
            internal = 1 if target is not None else 0
            dst_symbol = target.symbol_path if target else None
            dst_file = target.file if target else None

            if dst_symbol == row.symbol_path:
                continue

            edges.append(
                {
                    "src_file": row.file,
                    "src_symbol": row.symbol_path,
                    "dst_name": ref.qualified_name,
                    "dst_symbol": dst_symbol,
                    "dst_file": dst_file,
                    "kind": ref.ref_kind,
                    "internal": internal,
                }
            )
    return edges


def extract_edges(rows: list[ChunkRow]) -> list[dict]:
    """Full extraction: contain edges + reference edges for all rows."""
    by_symbol, by_leaf, file_to_module = _build_indices(rows)
    edges = _contain_edges(rows)
    edges.extend(_ref_edges_for_rows(rows, by_symbol, by_leaf, file_to_module))
    return edges


def extract_edges_for_changed(
    changed_rows: list[ChunkRow],
    all_rows: list[ChunkRow],
) -> list[dict]:
    """Incremental: re-extract only changed rows, resolving against the full symbol table."""
    by_symbol, by_leaf, file_to_module = _build_indices(all_rows)
    edges = _contain_edges(changed_rows)
    edges.extend(_ref_edges_for_rows(changed_rows, by_symbol, by_leaf, file_to_module))
    return edges
