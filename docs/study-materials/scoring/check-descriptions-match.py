#!/usr/bin/env python3
"""Check that the two conditions carry the same written description.

    python3 check-descriptions-match.py <codoc-workspace> <baseline-workspace>

The design requires the baseline's CLAUDE.md to hold the same features, the same
prose, and the same recorded rationale as the codoc workspace's feature document.
If they drift apart, the study stops comparing two ways of working and starts
comparing two documents.

Addresses are expected to differ, and that is the point. The codoc document cites
code with live links like `[name()](codoc:file.py#name)`, and the exported
markdown cites the same code as `file.py::name`. Both forms are reduced to the
symbol they name before comparing, so a difference reported here is a difference
in what the document says.

Run it after any change to either document, and before every session.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

FEATURE_ID = re.compile(r"\s*⟨f-[a-f0-9]+⟩\s*$")
CODOC_LINK = re.compile(r"\[[^\]]*\]\(codoc:([^)#]+)#([^)]+)\)")
MD_CITE = re.compile(r"`([^`]+\.py)::([^`]+)`")


def normalize(text: str) -> str:
    """Reduce either citation form to the plain symbol it names."""
    text = CODOC_LINK.sub(lambda m: f"{m.group(1)}::{m.group(2)}", text)
    return MD_CITE.sub(lambda m: f"{m.group(1)}::{m.group(2)}", text)


def tree_blocks(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("- "):
            out.append(normalize(FEATURE_ID.sub("", s[2:].strip())))
        else:
            out.append(normalize(s))
    return out


def markdown_blocks(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("Code:"):
            continue
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if title.endswith("feature guide"):
                continue
            out.append(normalize(title))
        else:
            out.append(normalize(s))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    codoc_ws, baseline_ws = (Path(a).expanduser() for a in sys.argv[1:3])
    tree = codoc_ws / ".codoc" / "tree.codoc"
    claude = baseline_ws / "CLAUDE.md"
    for p in (tree, claude):
        if not p.exists():
            print(f"missing: {p}")
            return 2

    a, b = tree_blocks(tree), markdown_blocks(claude)
    print(f"codoc description: {len(a)} paragraphs   ({tree})")
    print(f"CLAUDE.md:         {len(b)} paragraphs   ({claude})")

    if a == b:
        print("\nPASS  both conditions say exactly the same thing.")
        return 0

    print("\nFAIL  the two descriptions say different things.\n")
    for line in difflib.unified_diff(a, b, "codoc description", "CLAUDE.md", lineterm="", n=1):
        print("  " + line[:400])
    return 1


if __name__ == "__main__":
    sys.exit(main())
