"""Diff a ParsedTree against the SQLite store and produce IntentOps.

IntentOps are small, mappable to IntentionalRunner methods or proposal accept/reject calls.
The differ is pure: it does not mutate the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from codoc.pipelines.intentional.restructure import _would_create_cycle
from codoc.projection.parser import ParsedTree
from codoc.storage.sqlite_store import SQLiteStore


@dataclass
class AmendOp:
    uuid: str
    new_intent: str


@dataclass
class RenameOp:
    uuid: str
    new_slug: str


@dataclass
class RetireOp:
    uuid: str


@dataclass
class RestructureOp:
    uuid: str
    new_parent_uuid: str | None


@dataclass
class AcceptOp:
    hlc: str
    edits: dict | None = None


@dataclass
class RejectOp:
    hlc: str


@dataclass
class IntroduceOp:
    title: str
    parent_uuid: str | None
    intent: str
    source_file: str
    line: int


@dataclass
class DiffError:
    kind: str
    message: str
    file: str | None = None
    line: int | None = None


IntentOp = Union[AmendOp, RenameOp, RetireOp, RestructureOp, AcceptOp, RejectOp, IntroduceOp]


def _depth_of(uuid: str, store: SQLiteStore) -> int:
    """Return the depth of a feature in the tree (root = 0). Returns 0 if missing."""
    cur = store.get_feature(uuid)
    depth = 0
    seen: set[str] = set()
    while cur is not None and cur.parent_uuid is not None and cur.uuid not in seen:
        seen.add(cur.uuid)
        cur = store.get_feature(cur.parent_uuid)
        depth += 1
    return depth


def diff_tree(parsed: ParsedTree, store: SQLiteStore) -> tuple[list[IntentOp], list[DiffError]]:
    """Compute IntentOps from parsed tree vs. store. Returns (ops, errors).

    Ops are returned in topological order:
    1. RenameOp (deepest first to avoid transient slug collisions)
    2. RestructureOp
    3. AmendOp
    4. RetireOp
    5. AcceptOp / RejectOp
    """
    errors: list[DiffError] = []
    introduce_ops: list[IntroduceOp] = []

    # --- Unresolved features → IntroduceOp (proposals) ---
    for fname, lineno, title, parent_uuid, intent in parsed.feature_lines_without_uuid:
        introduce_ops.append(
            IntroduceOp(
                title=title,
                parent_uuid=parent_uuid,
                intent=intent,
                source_file=fname,
                line=lineno,
            )
        )
    for uuid, fname, lineno in parsed.duplicate_uuids:
        errors.append(
            DiffError(
                kind="duplicate_uuid",
                message=f"Duplicate UUID {uuid!r} appears more than once in the buffer.",
                file=fname,
                line=lineno,
            )
        )
    for msg in parsed.parse_errors:
        errors.append(DiffError(kind="parse_error", message=msg))

    # --- Build maps ---
    parsed_by_uuid: dict[str, "ParsedFeature"] = {pf.uuid: pf for pf in parsed.features}
    store_features = {f.uuid: f for f in store.list_features()}

    rename_ops: list[RenameOp] = []
    restructure_ops: list[RestructureOp] = []
    amend_ops: list[AmendOp] = []
    retire_ops: list[RetireOp] = []
    accept_ops: list[AcceptOp] = []
    reject_ops: list[RejectOp] = []

    # --- Per-UUID classification ---
    # 1) UUIDs in parsed files
    for uuid, pf in parsed_by_uuid.items():
        existing = store_features.get(uuid)
        if existing is None:
            errors.append(
                DiffError(
                    kind="unknown_uuid",
                    message=f"Feature UUID {uuid!r} not found in store.",
                    file=pf.source_file,
                    line=pf.line_number,
                )
            )
            continue

        # Slug differs?
        if pf.slug != existing.slug:
            rename_ops.append(RenameOp(uuid=uuid, new_slug=pf.slug))

        # Intent differs? Compare normalized whitespace; skip if both empty.
        existing_intent_norm = " ".join(existing.intent.split())
        parsed_intent_norm = " ".join(pf.intent.split())
        if parsed_intent_norm != existing_intent_norm and parsed_intent_norm:
            amend_ops.append(AmendOp(uuid=uuid, new_intent=parsed_intent_norm))

        # Parent differs?
        if pf.parent_uuid != existing.parent_uuid:
            restructure_ops.append(
                RestructureOp(uuid=uuid, new_parent_uuid=pf.parent_uuid)
            )

        # Retired flipped True?
        if pf.retired and not existing.retired:
            retire_ops.append(RetireOp(uuid=uuid))
        elif not pf.retired and existing.retired:
            errors.append(
                DiffError(
                    kind="cannot_unretire",
                    message=(
                        f"Feature {uuid!r} is retired in the store; un-prefixing the "
                        "tilde does not un-retire it. Edit ignored."
                    ),
                    file=pf.source_file,
                    line=pf.line_number,
                )
            )

    # 2) UUIDs missing from parsed files but present in store
    #    → user deleted the line → RetireOp (only for non-retired features).
    for uuid, existing in store_features.items():
        if uuid in parsed_by_uuid:
            continue
        if existing.retired:
            continue  # already retired and absent — no-op
        retire_ops.append(RetireOp(uuid=uuid))

    # --- Proposal ops ---
    for pp in parsed.proposals:
        if pp.action == "reject":
            reject_ops.append(RejectOp(hlc=pp.hlc))
        elif pp.action == "accept":
            edits = None
            if pp.edited_slug is not None or pp.edited_intent is not None:
                edits = {}
                if pp.edited_slug is not None:
                    edits["slug"] = pp.edited_slug
                if pp.edited_intent is not None:
                    edits["intent"] = pp.edited_intent
            accept_ops.append(AcceptOp(hlc=pp.hlc, edits=edits))
        elif pp.action == "accept-with-edits":
            edits = {}
            if pp.edited_slug is not None:
                edits["slug"] = pp.edited_slug
            if pp.edited_intent is not None:
                edits["intent"] = pp.edited_intent
            accept_ops.append(AcceptOp(hlc=pp.hlc, edits=edits or None))

    # --- Pre-validation ---
    # Slug collision: target slugs from RenameOps + parsed feature slugs (excluding renamed) must be unique
    # against existing non-retired slugs in store (excluding features being renamed away).
    rename_targets = {op.uuid: op.new_slug for op in rename_ops}
    # Build the set of slugs that will exist after renames.
    final_slugs: dict[str, str] = {}
    for uuid, f in store_features.items():
        if f.retired:
            continue
        if uuid in rename_targets:
            final_slugs[uuid] = rename_targets[uuid]
        else:
            final_slugs[uuid] = f.slug
    # Apply retire ops (retired features don't conflict).
    retired_uuids = {op.uuid for op in retire_ops}
    for u in list(final_slugs.keys()):
        if u in retired_uuids:
            final_slugs.pop(u, None)

    inverted: dict[str, list[str]] = {}
    for u, s in final_slugs.items():
        inverted.setdefault(s, []).append(u)
    for slug, uuids in inverted.items():
        if len(uuids) > 1:
            for u in uuids:
                if u in rename_targets:
                    pf = parsed_by_uuid.get(u)
                    errors.append(
                        DiffError(
                            kind="slug_collision",
                            message=(
                                f"Renaming {u!r} to {slug!r} collides with another "
                                f"non-retired feature using the same slug."
                            ),
                            file=pf.source_file if pf else None,
                            line=pf.line_number if pf else None,
                        )
                    )

    # Cycle detection on RestructureOps.
    valid_restructures: list[RestructureOp] = []
    for op in restructure_ops:
        if op.new_parent_uuid is not None:
            if _would_create_cycle(op.uuid, op.new_parent_uuid, store):
                pf = parsed_by_uuid.get(op.uuid)
                errors.append(
                    DiffError(
                        kind="cycle_detected",
                        message=(
                            f"Restructuring {op.uuid!r} under {op.new_parent_uuid!r} "
                            "would create a cycle."
                        ),
                        file=pf.source_file if pf else None,
                        line=pf.line_number if pf else None,
                    )
                )
                continue
        valid_restructures.append(op)
    restructure_ops = valid_restructures

    # --- Topological ordering ---
    # Renames deepest-first to avoid transient slug collisions.
    rename_ops_sorted = sorted(
        rename_ops, key=lambda op: -_depth_of(op.uuid, store)
    )

    ops: list[IntentOp] = []
    ops.extend(rename_ops_sorted)
    ops.extend(restructure_ops)
    ops.extend(amend_ops)
    ops.extend(retire_ops)
    ops.extend(introduce_ops)
    ops.extend(accept_ops)
    ops.extend(reject_ops)

    return ops, errors
