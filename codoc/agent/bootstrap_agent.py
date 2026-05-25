"""Bootstrap LLM passes — per-file feature proposal + top-level organization.

Two scoped calls replace the old flat, attach-biased, global batching that
produced cross-file junk-drawer nodes:

* :func:`propose_file_features` — one call per source file. The model only ever
  sees one file's chunks, so it cannot dump unrelated symbols from other files
  into the same node. It returns a small, coherent set of ``add_node`` ops,
  optionally nested via temporary local ids.
* :func:`propose_organization` — one call after every file is processed. Given
  the file-level features + their call/import coupling, it groups them under a
  few broad theme parents (``add_node`` themes + ``move_node`` of existing
  features), giving the tree real depth.

New nodes carry a temporary local id in the ``id`` field; a child or a moved
feature references it via ``parent_id``. :mod:`codoc.loop.bootstrap_hier`
resolves those local ids to freshly-minted feature ids before applying, which is
what lets a single call nest a new node under another new node — impossible with
the old apply path that minted ids only at write time.
"""
from __future__ import annotations

import json

from codoc.agent.base import format_prompt, load_prompt, run_agent
from codoc.config import LLMConfig
from codoc.model.event import NodeOp, NodeOpKind


def _coerce_op(raw: dict) -> NodeOp:
    """Coerce a raw op dict to a NodeOp, carrying a temporary local id.

    For ``add_node`` the model assigns a temporary id (``"id": "n1"``) so other
    ops in the same call can reference it as a ``parent_id``. We stash that
    temporary id in ``feature_id`` (which is otherwise None for a new node); the
    apply step in bootstrap_hier mints the real id and remaps references.
    """
    kind = NodeOpKind(raw["kind"])
    fid = raw.get("feature_id")
    if kind is NodeOpKind.ADD_NODE and not fid:
        fid = raw.get("id")  # temporary local id (e.g. "n1", "t1")
    bindings = [tuple(b) for b in raw.get("bindings", []) if len(b) == 2]
    return NodeOp(
        kind=kind,
        feature_id=fid,
        parent_id=raw.get("parent_id"),
        title=raw.get("title"),
        description=raw.get("description"),
        bindings=bindings,
        rationale=raw.get("rationale", ""),
    )


def _ops_from(raw: dict | list) -> list[NodeOp]:
    ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
    return [_coerce_op(o) for o in ops_raw]


def propose_file_features(
    file: str,
    chunks: list[dict],
    edges: list[dict],
    existing_titles: list[str],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> list[NodeOp]:
    """One LLM call: propose a small coherent feature set for a single file."""
    template = load_prompt("bootstrap_file")
    prompt = format_prompt(
        template,
        repo_name=repo_name,
        file=file,
        chunks=json.dumps(chunks, indent=2),
        edges=json.dumps(edges, indent=2),
        existing_titles=json.dumps(existing_titles, indent=2),
    )
    return _ops_from(run_agent(prompt, config))


def propose_organization(
    features: list[dict],
    edges: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> list[NodeOp]:
    """One LLM call: group file-level features under broad theme parents."""
    template = load_prompt("bootstrap_org")
    prompt = format_prompt(
        template,
        repo_name=repo_name,
        features=json.dumps(features, indent=2),
        edges=json.dumps(edges, indent=2),
    )
    return _ops_from(run_agent(prompt, config))
