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
    """Lifecycle of an inline comment thread.

    ``RESOLVED`` is reached two ways, and they mean the same thing to a reader: the
    author closed the thread, or the directive it produced landed. Distinguishing them
    would ask somebody to care about the difference between "I decided this is done" and
    "the agent finished it", which nobody does once the code is there.
    """

    OPEN = "open"          # authored, not yet handed to the loop
    SENT = "sent"          # handed to Loop B as a one-shot steer
    RESOLVED = "resolved"  # closed by the author, or its directive landed


class CommentScope(str, Enum):
    """What a comment asks to change.

    A steer has always been a note about the feature's CODE that deliberately leaves the
    prose alone — useful mid-generation, when editing the description is the wrong tool.
    ``BOTH`` is the other request an author actually makes: *do this, and say so* — when
    the change alters what the feature is for, and a description that still describes the
    old behaviour is the next reader's bug.

    ``CODE`` stays the default because it is the conservative one: it changes exactly
    what the author pointed at.
    """

    CODE = "code"   # change the code; leave the description as written
    BOTH = "both"   # change the code AND update the description to match


class CommentReply(BaseModel):
    """One answer on a comment thread.

    A comment asks for work; until now nothing ever came back on the same surface. The
    author had to go and find out elsewhere whether their note had been acted on, which
    is the whole reason a request feels like it went into a void. A reply is how the
    thread reports what happened to it — written by the agent that did the work, or by
    codoc when a directive lands.
    """

    author: str = "claude-code"   # who is answering ("claude-code" | "loop" | "human")
    body: str = ""
    at: HLC = Field(default_factory=HLC.now)


class CommentThread(BaseModel):
    """An inline comment thread anchored to a feature's description span.

    A comment is the smallest unit of REQUESTED WORK in codoc: it names a place in the
    prose, says what should be different, optionally names the code it means, and can be
    handed to an agent to build. The fields past ``media_ref`` are what make that
    possible rather than it being a sticky note:

    * ``anchor_text`` — the quoted snippet. Offsets alone identify a span in a
      description that may since have been rewritten; the words are what let the agent
      (and the reader) find what was actually being talked about.
    * ``code_refs`` — ``file::symbol`` (or bare ``file``) targets, which become the
      directive's ``Edit only:`` scope. Without them a steer inherits every file the
      feature touches, so "fix the retry in the uploader" licenses edits across the whole
      subsystem. This is the difference between commenting ON something and commenting
      NEAR it.
    * ``scope`` — code only, or code and prose (see :class:`CommentScope`).
    * ``directive_id`` — the realize directive this comment produced, stamped when Loop B
      mints it. It is what closes the loop: the thread can then say "this landed", and
      join to the events (and the commit) its request actually caused.

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
    anchor_text: str = ""
    code_refs: list[str] = Field(default_factory=list)
    scope: CommentScope = CommentScope.CODE
    directive_id: str = ""
    # What came back. Appended when the thread's directive lands (see
    # loop_b._close_landed_comments); the author reads the outcome where they asked.
    replies: list[CommentReply] = Field(default_factory=list)
    media_ref: str = ""
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)


# How long a RESOLVED thread keeps its place in the margin.
#
# Not forever, and not zero. Forever is what shipped, and it made "resolve" a button that
# could not do its job: the card came back on every projection, so a thread could be
# closed and never leave, and the margin accumulated finished conversations. Zero throws
# away the one moment the thread is most useful — the reader wants to see that their
# request landed, and what code it produced, exactly once.
#
# So a closed thread lingers long enough to be noticed and then goes. The RECORD is not
# deleted; it stays in the store as the durable answer to "why does this code look like
# this", reachable from history. It just stops being a live conversation on the page.
RESOLVED_LINGER_S = 3600.0


def in_margin(thread: "CommentThread", now_ms: float) -> bool:
    """Should this thread still be drawn beside the prose?"""
    if thread.status is not CommentStatus.RESOLVED:
        return True
    age_s = (now_ms - thread.updated_at.wall_clock) / 1000.0
    return 0 <= age_s <= RESOLVED_LINGER_S
