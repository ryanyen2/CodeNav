"""codoc.pipelines.intentional.rename — RENAME transaction handler.

Edits a feature's slug. Phase 1 intentional operation: validates slug
uniqueness among active (non-retired) features, then commits directly to the
log without a proposal/review step. No cascade.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from codoc.model.feature import Feature
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC
from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog

# Slug format: lowercase letters, digits, hyphens; no leading/trailing hyphens; 1-80 chars.
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$")
_SLUG_MAX_LEN = 80


def validate_slug(slug: str) -> str:
    """Validate and normalize a slug. Raises ValueError on invalid format.

    Rules: lowercase letters, digits, hyphens only; no leading/trailing
    hyphens; 1–80 characters.

    Returns the validated slug unchanged (no normalization is applied so the
    caller's intent is preserved exactly).
    """
    if not slug:
        raise ValueError("slug must not be empty")
    if len(slug) > _SLUG_MAX_LEN:
        raise ValueError(
            f"slug must be at most {_SLUG_MAX_LEN} characters, got {len(slug)}"
        )
    # Single-character slug: must be a letter or digit (not a hyphen).
    if len(slug) == 1:
        if not re.match(r"^[a-z0-9]$", slug):
            raise ValueError(
                f"slug {slug!r} is invalid: must contain only lowercase letters, "
                "digits, or hyphens, with no leading/trailing hyphens"
            )
    elif not _SLUG_RE.match(slug):
        raise ValueError(
            f"slug {slug!r} is invalid: must contain only lowercase letters, "
            "digits, or hyphens, with no leading/trailing hyphens"
        )
    return slug


def rename_feature(
    feature_uuid: str,
    new_slug: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "user",
) -> Transaction:
    """Edit a feature's slug.

    Validates:
    - feature_uuid must exist
    - new_slug must be non-empty, kebab-case (only lowercase letters, digits, hyphens)
    - new_slug must be unique among non-retired features

    Applies:
    - Updates feature.slug in store
    - Updates feature.updated_at_hlc
    - Writes RENAME transaction to log and JSONL

    Returns the committed Transaction.
    """
    # --- Validate inputs ---
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")

    # Validate slug format (raises ValueError on bad format).
    validate_slug(new_slug)

    # Check uniqueness among non-retired features (excluding the feature itself).
    all_features = store.list_features()
    for f in all_features:
        if f.uuid == feature_uuid:
            continue
        if not f.retired and f.slug == new_slug:
            raise ValueError(
                f"slug {new_slug!r} is already used by non-retired feature {f.uuid!r}"
            )

    # --- Tick HLC and build transaction ---
    hlc = tx_log._tick()
    parent_hlc = tx_log.head_hlc()
    parent_hlcs: list[HLC] = [parent_hlc] if parent_hlc is not None else []

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=parent_hlcs,
        kind=TransactionKind.RENAME,
        payload={
            "feature_uuid": feature_uuid,
            "old_slug": feature.slug,
            "new_slug": new_slug,
        },
        author=author,
        proposal=False,
        accepted_at=datetime.now(timezone.utc),
    )

    # --- Apply mutation to feature store ---
    # Always clear title so it falls back to the new slug on read.
    # An explicit custom title can be set afterward with an AMEND operation.
    updated_feature = feature.model_copy(
        update={
            "slug": new_slug,
            "title": "",
            "updated_at_hlc": hlc,
        }
    )
    store.upsert_feature(updated_feature)

    # --- Commit transaction to log ---
    committed = tx_log.append(tx)

    # --- Append to JSONL audit log ---
    jsonl_log.append(committed)

    return committed
