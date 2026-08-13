"""Export the live feature tree as plain, tool-free markdown.

For workflows without the codoc extension — most concretely a ``CLAUDE.md`` an
agent reads on its own: same titles, same prose, same recorded rationale, with
code cited as ``file.py::symbol`` paths instead of live bindings. Feature ids,
pending proposals, and retired nodes are omitted — this is the *account*, not
the control surface. The transform is one-way; nothing parses it back.
"""
from __future__ import annotations

import re

from codoc.codoc_file.tree_order import children_map
from codoc.store.db import Store

# [label](codoc:file.py#symbol) → a plain code citation. The live binding is a
# codoc affordance; the exported document keeps only the address.
_CODOC_LINK = re.compile(r"\[([^\]]*)\]\(codoc:([^)#]+)#([^)]+)\)")


def _plain_citation(m: re.Match) -> str:
    label, file, symbol = m.group(1), m.group(2), m.group(3)
    address = f"`{file}::{symbol}`"
    # When the label is just the symbol (the common case), the address alone
    # reads better than "foo() (`x.py::foo`)".
    bare = label.strip().rstrip("()")
    if bare and bare != symbol and not symbol.endswith(bare):
        return f"{label} ({address})"
    return address


def export_markdown(store: Store, *, title: str = "Codebase feature guide") -> str:
    """The live tree as markdown: headings by depth, prose verbatim (codoc links
    flattened), and a ``Code:`` line naming each feature's bindings."""
    lines: list[str] = [f"# {title}", ""]
    children = children_map(store.list_features())

    def walk(parent_id: str | None, depth: int) -> None:
        for f in children.get(parent_id, []):
            if f.retired:
                continue
            lines.append(f"{'#' * min(depth + 2, 6)} {f.title}")
            lines.append("")
            if f.description:
                prose = _CODOC_LINK.sub(_plain_citation, f.description)
                lines.append(prose)
                lines.append("")
            binds = [b.symbol_path for b in store.bindings_for_feature(f.id)]
            if binds:
                lines.append("Code: " + ", ".join(f"`{b}`" for b in sorted(binds)))
                lines.append("")
            walk(f.id, depth + 1)

    walk(None, 0)
    return "\n".join(lines).rstrip() + "\n"
