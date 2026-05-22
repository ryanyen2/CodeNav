"""Directory-and-class-prefix grouping for bootstrap.

Groups chunks by directory, then by class prefix within directories, to build
a structural feature tree without LLM calls. Zero LLM calls by default.
Use ``propose_structural(with_intent=True)`` to optionally batch-generate
intent text (one call per feature).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StructuralGroup:
    """A node in the structure-derived feature hierarchy."""

    slug: str
    title: str
    chunk_indices: list[int]
    children: list["StructuralGroup"] = field(default_factory=list)
    level: int = 0
    # 0=root, 1=top-dir/package, 2=class-or-module, 3=file-scoped


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "unnamed"


def _human_title(name: str) -> str:
    """Convert a snake_case or CamelCase name to a readable title."""
    # Insert space before uppercase runs in CamelCase.
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    spaced = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", spaced)
    return spaced.replace("_", " ").replace("-", " ").strip()


def _top_dir(file: str) -> str:
    """Return the top-level directory component of *file*, or '(root)' for root files."""
    parts = Path(file).parts
    if len(parts) >= 2:
        return parts[0]
    return "(root)"


def _class_prefix(symbol_path: str) -> str:
    """Return the class/module prefix of *symbol_path* (part before first '.').

    Returns '' for top-level symbols (no dot in path).
    """
    if "." in symbol_path:
        return symbol_path.split(".")[0]
    return ""


def build_structural_tree(
    chunks,
    *,
    min_leaf_size: int = 1,
    merge_small_files: bool = True,
) -> StructuralGroup:
    """Build a StructuralGroup tree from directory + class structure.

    Parameters
    ----------
    chunks:
        List of Chunk objects (must have .file and .symbol_path attributes).
    min_leaf_size:
        Groups smaller than this are merged up into their parent.
    merge_small_files:
        If True, file-scoped groups with a single chunk are merged into the
        parent directory group rather than becoming lonely one-chunk features.

    Returns
    -------
    StructuralGroup
        Root node (level=0).  Its children are directory nodes (level=1),
        each of which may have class nodes (level=2) or file nodes (level=3).
    """
    root = StructuralGroup(
        slug="root", title="root",
        chunk_indices=list(range(len(chunks))),
        level=0,
    )

    # --- Level 1: group by top-level directory ---
    by_dir: dict[str, list[int]] = defaultdict(list)
    for i, chunk in enumerate(chunks):
        by_dir[_top_dir(chunk.file)].append(i)

    for dir_name in sorted(by_dir):
        dir_indices = by_dir[dir_name]
        dir_slug = _slugify(dir_name)
        dir_group = StructuralGroup(
            slug=dir_slug,
            title=_human_title(dir_name),
            chunk_indices=dir_indices,
            level=1,
        )

        # --- Level 2: within dir, group by class prefix ---
        by_class: dict[str, list[int]] = defaultdict(list)
        by_file_toplevel: dict[str, list[int]] = defaultdict(list)

        for i in dir_indices:
            prefix = _class_prefix(chunks[i].symbol_path)
            if prefix:
                by_class[prefix].append(i)
            else:
                # Top-level symbol (function, constant) — group by file
                by_file_toplevel[chunks[i].file].append(i)

        # Class-scoped groups.
        for class_name in sorted(by_class):
            c_indices = by_class[class_name]
            if merge_small_files and len(c_indices) < min_leaf_size:
                # Too small — keep in parent's chunk_indices but don't recurse.
                continue
            dir_group.children.append(StructuralGroup(
                slug=f"{dir_slug}/{_slugify(class_name)}",
                title=_human_title(class_name),
                chunk_indices=c_indices,
                level=2,
            ))

        # File-scoped groups for top-level functions.
        for file_path in sorted(by_file_toplevel):
            f_indices = by_file_toplevel[file_path]
            if merge_small_files and len(f_indices) <= 1:
                # Single-chunk files fold into parent.
                continue
            file_stem = Path(file_path).stem
            dir_group.children.append(StructuralGroup(
                slug=f"{dir_slug}/{_slugify(file_stem)}",
                title=_human_title(file_stem),
                chunk_indices=f_indices,
                level=3,
            ))

        root.children.append(dir_group)

    return root


def iter_leaves(group: StructuralGroup):
    """Yield all leaf StructuralGroups (those with no children)."""
    if not group.children:
        yield group
    else:
        for child in group.children:
            yield from iter_leaves(child)


def count_groups(group: StructuralGroup) -> int:
    return 1 + sum(count_groups(c) for c in group.children)
