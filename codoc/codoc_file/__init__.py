"""The single human-facing surface: ``.codoc/tree.codoc``.

``render`` (store → text), ``parse`` (text → ParsedTree), ``diff`` (ParsedTree vs
store → user ops + proposal verdicts). Node identity travels in the file as a
``⟨f-id⟩`` marker, so there is no sidecar and no line-range alignment to go stale.
"""
from codoc.codoc_file.diff import CodocDiff, Verdict, diff_codoc
from codoc.codoc_file.parse import ParsedNode, ParsedTree, parse_text, parse_tree_file
from codoc.codoc_file.render import TREE_FILENAME, render_tree, tree_path, write_tree

__all__ = [
    "CodocDiff",
    "ParsedNode",
    "ParsedTree",
    "TREE_FILENAME",
    "Verdict",
    "diff_codoc",
    "parse_text",
    "parse_tree_file",
    "render_tree",
    "tree_path",
    "write_tree",
]
