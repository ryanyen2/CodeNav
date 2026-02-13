"""Pydantic models for semantic tree pipeline (mirrors src/types.ts)."""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field

# --- Extraction (codebase snapshot) ---

Sigil = Literal["/", "%", "$", "^", "~"]
ArtifactClass = Literal["concrete-dir", "concrete-file", "concrete-leaf", "abstract"]
NodeStatus = Literal["resolved", "draft", "unresolved", "planned"]
DepRelationType = Literal["imports", "invokes", "inherits", "type-refs"]


class CodeEntity(BaseModel):
    """One function, class, or method in a file."""
    name: str
    entity_type: Literal["function", "class", "method"]
    signature: Optional[str] = None
    fpath: str
    line_range: Optional[tuple[int, int]] = None
    body_text: Optional[str] = None
    docstring: Optional[str] = None


class ImportEdge(BaseModel):
    """Dependency edge from AST: imports, invokes, inherits."""
    source_entity: Optional[str] = None  # entity name or fpath::entity
    target_entity: str
    relation: DepRelationType = "imports"
    is_external: bool = False
    source_fpath: Optional[str] = None
    target_fpath: Optional[str] = None


class FileInfo(BaseModel):
    """Single file with entities and its import edges."""
    fpath: str
    language: str = "python"
    entities: List[CodeEntity] = Field(default_factory=list)
    imports: List[ImportEdge] = Field(default_factory=list)


class CodebaseSnapshot(BaseModel):
    """Full codebase: root dir and per-file entities + imports."""
    root_dir: str
    files: List[FileInfo] = Field(default_factory=list)

    @property
    def all_entities(self) -> List[CodeEntity]:
        return [e for f in self.files for e in f.entities]

    @property
    def all_imports(self) -> List[ImportEdge]:
        return [e for f in self.files for e in f.imports]


# --- LLM pipeline outputs ---

class SemanticFeature(BaseModel):
    """Per-entity semantic features from semantic_parsing prompt."""
    entity_name: str
    features: List[str] = Field(default_factory=list)


class FunctionalArea(BaseModel):
    """High-level area from domain_discovery prompt."""
    name: str


class HierarchyMapping(BaseModel):
    """One path -> entity groups from hierarchical_construction (prompt returns dict of these)."""
    path: str  # "functional_area/category/subcategory"
    entity_groups: List[str] = Field(default_factory=list)


# --- Semantic tree (output, matches TS SemanticTree) ---

class Contract(BaseModel):
    """Interface specification (plan §2.1)."""
    sig: Optional[str] = None
    inv: Optional[str] = None
    cls: Optional[str] = None
    exp: Optional[str] = None


class NodeMetadata(BaseModel):
    """Structural anchoring to artifacts."""
    type: Optional[Literal["directory", "file", "function", "class", "method"]] = None
    fpath: Optional[str] = None
    entity_name: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None


class SemanticNode(BaseModel):
    """Single node: (f, m, c) + sigil, status, children."""
    id: str = ""
    sigil: Sigil = "~"
    artifact_class: ArtifactClass = "abstract"
    feature: str = ""
    metadata: NodeMetadata = Field(default_factory=NodeMetadata)
    contract: Contract = Field(default_factory=Contract)
    status: NodeStatus = "resolved"
    children: List["SemanticNode"] = Field(default_factory=list)
    parent: Optional["SemanticNode"] = None

    class Config:
        arbitrary_types_allowed = True


SemanticNode.model_rebuild()


class DepEdge(BaseModel):
    """E_dep: dependency edge (TS: from, to)."""
    from_entity: str = Field(alias="from")
    to: str
    relation: DepRelationType = "imports"
    from_external: Optional[bool] = None
    to_external: Optional[bool] = None

    class Config:
        populate_by_name = True


class SemanticTree(BaseModel):
    """Full tree: root node + dependency edges."""
    root: SemanticNode
    deps: List[DepEdge] = Field(default_factory=list)
