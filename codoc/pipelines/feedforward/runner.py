"""Feedforward pipeline: placeholder features → LLM fills fields → FEEDFORWARD_FILL proposals.

Triggered from watch.py after sync_from_dir when placeholder features are detected.
Does NOT trigger realize — that waits for the user to accept the feedforward proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FeedforwardRunResult:
    proposals_emitted: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)


def _classify_feature(feature) -> str:
    """Return 'placeholder', 'partial', or 'complete'."""
    has_purpose = bool(feature.purpose or feature.intent)
    has_rationale = bool(feature.rationale)
    has_scenario = bool(feature.scenario)
    if not has_purpose:
        return "placeholder"
    if not has_rationale or not has_scenario:
        return "partial"
    return "complete"


def run_feedforward(
    codoc_dir: str,
    root_dir: str,
    *,
    target_uuids: list[str] | None = None,
    dry_run: bool = False,
) -> FeedforwardRunResult:
    """Run feedforward for placeholder/partial features in the store.

    Parameters
    ----------
    codoc_dir:
        Path to the .codoc directory.
    root_dir:
        Repository root.
    target_uuids:
        Limit feedforward to specific feature UUIDs.  If None, scans all
        placeholder/partial features.
    dry_run:
        If True, build prompts but do not write proposals or call LLM.
    """
    from codoc.pipelines.intentional.runner import open_stores
    from codoc.projection.tree_codoc import write_tree
    from codoc.model.transaction import Transaction, TransactionKind
    from codoc.model.hlc import HLC

    db_path = str(Path(codoc_dir) / "codoc.db")

    store, tx_log, jsonl_log = open_stores(codoc_dir)
    result = FeedforwardRunResult()

    try:
        features = store.list_features()
        repo_name = Path(root_dir).name

        # Build summary of existing features for LLM context
        existing_summaries = [
            {"slug": f.slug, "purpose": f.purpose, "intent": f.intent}
            for f in features
            if not f.retired
        ]

        targets = features if target_uuids is None else [
            f for f in features if f.uuid in target_uuids
        ]

        for feature in targets:
            if feature.retired:
                continue
            if feature.status not in ("placeholder", "feedforward_pending"):
                classification = _classify_feature(feature)
                if classification == "complete":
                    continue

            partial = feature.purpose or feature.intent or feature.rationale or feature.scenario
            if dry_run:
                result.proposals_emitted += 1
                continue

            from codoc.agents.feedforward import run_feedforward_agent
            ff = run_feedforward_agent(
                title=feature.title or feature.slug,
                partial_description=partial,
                existing_features=existing_summaries,
                repo_name=repo_name,
            )

            if ff.error:
                result.errors.append(f"{feature.slug}: {ff.error}")
                continue

            if not any([ff.purpose, ff.rationale, ff.scenario, ff.coding_directive]):
                continue

            hlc = tx_log._tick()
            tx = Transaction(
                hlc=hlc,
                parent_hlcs=[],
                kind=TransactionKind.FEEDFORWARD_FILL,
                payload={
                    "feature_uuid": feature.uuid,
                    "slug": feature.slug,
                    "new_purpose": ff.purpose,
                    "new_rationale": ff.rationale,
                    "new_scenario": ff.scenario,
                    "coding_directive": ff.coding_directive,
                    "target_files": ff.target_files,
                    "affected_feature_uuid": feature.uuid,
                },
                author="feedforward",
                proposal=True,
            )
            stamped = tx_log.append_proposal(tx)
            jsonl_log.append(stamped)

            # Mark feature as feedforward_pending so watch.py knows not to re-run
            updated = feature.model_copy(update={"status": "feedforward_pending", "updated_at_hlc": hlc})
            store.upsert_feature(updated)

            result.proposals_emitted += 1

        # Re-render so the proposals appear in .codoc/tree/
        if result.proposals_emitted > 0 and not dry_run:
            write_tree(codoc_dir, store, tx_log)

    finally:
        store.close()

    if result.proposals_emitted == 0 and not result.errors:
        result.skipped = True

    return result
