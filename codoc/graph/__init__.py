"""The code dependency graph: extracted from the index, held in memory,
cached as JSON. Derived, never part of the calculus, safe to rebuild."""
from codoc.graph.graph import Graph

__all__ = ["Graph"]
