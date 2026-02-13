"""Semantic tree pipeline: domain discovery → semantic parsing → hierarchy → assembly."""

from api.semantic_tree.pipeline.domain_discovery import run_domain_discovery
from api.semantic_tree.pipeline.semantic_parsing import run_semantic_parsing
from api.semantic_tree.pipeline.hierarchical_construction import run_hierarchical_construction
from api.semantic_tree.pipeline.tree_assembly import assemble_tree
from api.semantic_tree.models import (
    CodebaseSnapshot,
    SemanticTree,
    FunctionalArea,
    SemanticFeature,
    HierarchyMapping,
)

__all__ = [
    "run_domain_discovery",
    "run_semantic_parsing",
    "run_hierarchical_construction",
    "assemble_tree",
    "CodebaseSnapshot",
    "SemanticTree",
    "FunctionalArea",
    "SemanticFeature",
    "HierarchyMapping",
]
