"""The single human-facing surface: ``.codoc/tree.codoc``.

``render`` (store → text), ``parse`` (text → ParsedTree), ``diff`` (ParsedTree vs
store → user ops). Node identity travels in the file as a hidden ``⟨f-id⟩`` marker
(the IDE collapses it), so there is no line-range alignment to go stale. Proposal
verdicts are not parsed from the text — they flow through ``.codoc/inbox.json``.
"""
from codoc.codoc_file.diff import CodocDiff, diff_codoc
from codoc.codoc_file.parse import ParsedNode, ParsedTree, Ref, extract_refs, parse_text, parse_tree_file
from codoc.codoc_file.render import TREE_FILENAME, render_tree, tree_path, write_tree

__all__ = [
    "CodocDiff",
    "ParsedNode",
    "ParsedTree",
    "Ref",
    "TREE_FILENAME",
    "diff_codoc",
    "extract_refs",
    "parse_text",
    "parse_tree_file",
    "render_tree",
    "tree_path",
    "write_tree",
]
