from pydantic import BaseModel

from codoc.model.anchor import Anchor
from codoc.model.hlc import HLC


class Binding(BaseModel):
    uuid: str  # UUIDv7; serves as the OR-set identity tag for CRDT merge
    feature_uuid: str
    anchor: Anchor
    fingerprint: str  # SHA-256 hex of the tree-sitter token stream (comments stripped, whitespace normalised)
    fingerprint_at_hlc: HLC  # HLC when this fingerprint was last computed; staleness check uses this
    parent_symbol: str | None = None  # enclosing entity path for sub-entity anchors (ts_query inside a method)
    ast_skeleton_hash: str | None = None  # SHA-256 of the AST node-type sequence (rename-invariant structural identity)
    minhash_sketch: bytes | None = None  # 16-byte MinHash sketch over k=5 token n-grams (move detection)
