"""Planning pipeline runner — reads feature tree, calls planning agent, emits proposals."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.pipelines.intentional.runner import open_stores


def run_plan(
    prompt: str,
    root_dir: str,
    codoc_dir: str,
    repo_name: str = "codebase",
    node_id: str = "default",
) -> dict:
    """Read feature tree, call planning agent, emit proposals, return session summary.

    Returns:
        {
            "session_id": str,
            "proposals_emitted": int,
            "proposals": [{"kind": ..., "slug": ..., "title": ..., "hlc": ...}],
            "files_changed": [str],
        }
    """
    store, jsonl_log, _ = open_stores(codoc_dir)
    session_id = str(_uuid.uuid4())

    try:
        # Build feature summaries for the planning agent.
        features = store.list_features()
        feature_summaries = [
            {
                "slug": f.slug,
                "title": f.title or f.slug,
                "intent": f.intent,
                "uuid": f.uuid,
            }
            for f in features
            if not f.retired
        ]

        # Build a slug -> uuid map for resolving parent_slug references.
        slug_to_uuid = {f.slug: f.uuid for f in features}

        from codoc.agents.planning import plan_agent
        ops = plan_agent(prompt, feature_summaries, repo_name=repo_name)

        proposals_info: list[dict] = []
        for op in ops:
            try:
                provisional_uuid = str(_uuid.uuid4())
                hlc = HLC.now(node_id=node_id)

                if op.kind == "introduce":
                    parent_uuid = slug_to_uuid.get(op.parent_slug) if op.parent_slug else None
                    tx = Transaction(
                        hlc=hlc,
                        parent_hlcs=[],
                        kind=TransactionKind.INTRODUCE,
                        payload={
                            "slug": op.slug,
                            "title": op.title,
                            "parent_uuid": parent_uuid,
                            "intent": op.intent,
                            "provisional_uuid": provisional_uuid,
                            "coding_directive": op.coding_directive,
                            "rationale": op.rationale,
                            "plan_session_id": session_id,
                            "source": "plan",
                        },
                        author="plan",
                        proposal=True,
                    )
                elif op.kind == "amend":
                    feature_uuid = slug_to_uuid.get(op.slug)
                    if feature_uuid is None:
                        continue
                    feature = store.get_feature(feature_uuid)
                    tx = Transaction(
                        hlc=hlc,
                        parent_hlcs=[],
                        kind=TransactionKind.AMEND,
                        payload={
                            "feature_uuid": feature_uuid,
                            "slug": op.slug,
                            "old_intent": feature.intent if feature else "",
                            "new_intent": op.intent,
                            "new_title": op.title,
                            "coding_directive": op.coding_directive,
                            "rationale": op.rationale,
                            "plan_session_id": session_id,
                            "source": "plan",
                        },
                        author="plan",
                        proposal=True,
                    )
                elif op.kind == "rename":
                    feature_uuid = slug_to_uuid.get(op.slug)
                    if feature_uuid is None:
                        continue
                    tx = Transaction(
                        hlc=hlc,
                        parent_hlcs=[],
                        kind=TransactionKind.RENAME,
                        payload={
                            "feature_uuid": feature_uuid,
                            "old_slug": op.slug,
                            "new_slug": op.slug,
                            "new_title": op.title,
                            "coding_directive": op.coding_directive,
                            "rationale": op.rationale,
                            "plan_session_id": session_id,
                            "source": "plan",
                        },
                        author="plan",
                        proposal=True,
                    )
                elif op.kind == "retire":
                    feature_uuid = slug_to_uuid.get(op.slug)
                    if feature_uuid is None:
                        continue
                    tx = Transaction(
                        hlc=hlc,
                        parent_hlcs=[],
                        kind=TransactionKind.RETIRE,
                        payload={
                            "feature_uuid": feature_uuid,
                            "slug": op.slug,
                            "rationale": op.rationale,
                            "plan_session_id": session_id,
                            "source": "plan",
                        },
                        author="plan",
                        proposal=True,
                    )
                else:
                    continue

                store.write_transaction(tx)
                jsonl_log.append(tx)
                proposals_info.append({
                    "kind": op.kind,
                    "slug": op.slug,
                    "title": op.title,
                    "hlc": hlc.to_str(),
                })

                if op.kind == "introduce" and op.slug:
                    slug_to_uuid[op.slug] = provisional_uuid

            except Exception:
                continue

    finally:
        store.close()

    # Re-render .codoc files so proposals are visible.
    store2, _, tx_log2 = open_stores(codoc_dir)
    try:
        from codoc.projection.tree_codoc import write_tree
        write_tree(codoc_dir, store2, tx_log2)
    finally:
        store2.close()

    return {
        "session_id": session_id,
        "proposals_emitted": len(proposals_info),
        "proposals": proposals_info,
        "files_changed": [],
    }
