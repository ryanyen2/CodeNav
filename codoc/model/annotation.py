"""Annotation models — durable rich-state on a feature that is NOT prose.

Promoted into the store (R8–R10) so the store→doc projection
(:func:`codoc.codoc_file.doc_render.build_doc_from_store`) carries them and the
webview stops being the authoritative holder of ``tree.doc.json``. Two kinds:

- :class:`Mark` — a tracked-change authorship span on a feature's description
  (who/what amended which range — agent ink vs human ink), re-projected onto the
  inline runs.
- :class:`CommentThread` — an inline steering note anchored to a description
  span; the durable home for what currently lives only in ``tree.doc.json``
  ``DocFile.comments``.

Anchors are character offsets into the feature's *normalized* description text
(the same normalization the round-trip uses), so the projection can map them onto
inline runs deterministically. Drafts are deliberately NOT modeled here — the
held-draft set already lives disk-persisted in ``edits.json`` ``drafts`` and
survives reloads (R10 is already met); a second source would only add drift.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from codoc.model.block import Provenance  # reused: who authored the span
from codoc.model.hlc import HLC
from codoc.model.ids import new_comment_id, new_mark_id


class MarkKind(str, Enum):
    """The tracked-change flavor of an authorship span. Mirrors the webview's
    ProseMirror authorship marks so the projection round-trips them."""

    INSERTION = "insertion"   # newly added text not yet part of the baseline
    DELETION = "deletion"     # text struck through, kept as plain text in the baseline
    AMEND = "amend"           # an in-place edit (the pencil-ink default)


class Mark(BaseModel):
    """One tracked-change authorship span on a feature's description.

    ``anchor_start``/``anchor_end`` are character offsets into the normalized
    description. A zero-width span (start == end) is an insertion caret.
    """

    id: str = Field(default_factory=new_mark_id)
    feature_id: str
    kind: MarkKind = MarkKind.AMEND
    provenance: Provenance = Provenance.HUMAN
    anchor_start: int = 0
    anchor_end: int = 0
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)


class CommentStatus(str, Enum):
    """Lifecycle of an inline comment thread."""

    OPEN = "open"          # authored, not yet handed to the loop
    SENT = "sent"          # handed to Loop B as a one-shot steer
    RESOLVED = "resolved"  # closed by the author


class CommentThread(BaseModel):
    """An inline comment thread anchored to a feature's description span.

    ``media_ref`` is an optional repo-relative path to a screenshot attachment
    (U6 of the notebook protocol); empty when the thread is text-only.
    """

    id: str = Field(default_factory=new_comment_id)
    feature_id: str
    body: str = ""
    author: Provenance = Provenance.HUMAN
    status: CommentStatus = CommentStatus.OPEN
    anchor_start: int = 0
    anchor_end: int = 0
    media_ref: str = ""
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)
