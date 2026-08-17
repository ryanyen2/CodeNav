"""``.codoc/ask.json`` — the ephemeral ``/codoc:ask`` walkthrough overlay.

A walkthrough is a **reading order over features that already exist**: the agent
answers a question by numbering the nodes a reader should visit, in procedure
order, with one short note per node saying what that node contributes to the
answer. It draws a path through the tree; it never adds to it.

That is why this is a file and not a store write. Nothing here enters the change
ledger, mints an event, or touches ``edits.json`` — so an ask is safe at any
moment, including mid-edit, mid-proposal, mid-realization. Dismissing it leaves
the tree byte-identical. The precedent is :mod:`codoc.loop.activity`: a single
slot, written under a lock, atomic, TTL'd, and safe to delete at any time.

Schema (version 1)::

    {
      "version": 1,
      "id": "ask-<ns>",
      "question": "why is de-hyphenation skipped inside a quote?",
      "answer": "It is not skipped — quotes are de-hyphenated with everything else, …",
      "at": "<iso>",
      "steps": [
        {
          "label": "1a",                        # COMPUTED here, never supplied
          "group": "reading the lines",         # optional procedure heading
          "feature_id": "f-1a2b",
          "note": "where the page break is dropped",
          "quote": "furniture is stripped first",   # verbatim span of the description
          "file": "scribe/furniture.py",            # optional code citation
          "symbol": "scribe/furniture.py::strip",
          "line": 42
        }
      ]
    }

``label`` is computed from ``group`` rather than supplied, so an LLM cannot emit
a walkthrough numbered ``1a, 1b, 1b, 2``. Grouped runs read ``1a 1b 1c / 2a 2b``;
an ungrouped walkthrough reads ``1 2 3``.

No loop reads this file. The IDE watches it, and the only writer is the MCP tool
behind ``/codoc:ask`` (the IDE deletes it to dismiss).
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from codoc.loop.filenames import ASK_FILENAME
from codoc.loop.fsio import atomic_write_json, read_json

ASK_VERSION = 1

# A walkthrough is a reading path, not a report. Past ~a dozen stops nobody
# follows it in order, and the numbering stops being the point — so the tool
# truncates and says so rather than rendering a wall of chips.
MAX_STEPS = 12

# Prose budgets. These are what keep the overlay subtle: a note that does not fit
# on one line stops being an annotation and becomes a second description.
MAX_NOTE_CHARS = 120
MAX_ANSWER_CHARS = 400
MAX_QUESTION_CHARS = 300
MAX_GROUP_CHARS = 60

# How long a walkthrough is honoured without being refreshed. Unlike the epoch
# lease this is not about liveness — it is about not resurrecting yesterday's
# question when the IDE reopens. Long enough to survive lunch, short enough that
# a stale overlay never greets a new session.
ASK_TTL_SECONDS = 8 * 3600

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


@dataclass
class AskStep:
    """One stop on the walkthrough. ``label`` is filled in by :func:`label_steps`."""

    feature_id: str
    note: str = ""
    quote: str = ""
    group: str = ""
    file: str = ""
    symbol: str = ""
    line: int | None = None
    label: str = ""

    def to_dict(self) -> dict:
        out: dict = {"label": self.label, "feature_id": self.feature_id}
        if self.group:
            out["group"] = self.group
        if self.note:
            out["note"] = self.note
        if self.quote:
            out["quote"] = self.quote
        if self.file:
            out["file"] = self.file
        if self.symbol:
            out["symbol"] = self.symbol
        if self.line is not None:
            out["line"] = self.line
        return out


@dataclass
class Walkthrough:
    question: str
    answer: str = ""
    steps: list[AskStep] = field(default_factory=list)
    id: str = ""
    at: str = ""

    def to_dict(self) -> dict:
        return {
            "version": ASK_VERSION,
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "at": self.at,
            "steps": [s.to_dict() for s in self.steps],
        }


def ask_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / ASK_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut to *limit* on a word boundary where possible.

    Prose arrives from an LLM, so it may be a paragraph where a phrase was asked
    for. Truncating in the writer (rather than in CSS) keeps the file honest
    about what the overlay will actually show.
    """
    s = " ".join((text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    space = cut.rfind(" ")
    if space > limit * 0.6:  # only prefer a word boundary if it isn't a savage cut
        cut = cut[:space]
    return cut.rstrip(" ,;:.—-") + "…"


def label_steps(steps: list[AskStep]) -> list[AskStep]:
    """Assign ``label`` in place and return *steps*.

    Grouped: a new group STRING starts a new number, and each step inside it gets
    the next letter — ``1a 1b / 2a``. The group changing back to an earlier name
    starts a fresh number, because a procedure that returns to a stage visits it
    again rather than rejoining the first visit.

    Ungrouped (no step carries a group): plain ordinals — ``1 2 3``.
    """
    grouped = any(s.group for s in steps)
    if not grouped:
        for i, s in enumerate(steps):
            s.label = str(i + 1)
        return steps
    group_no = 0
    letter_i = 0
    prev: str | None = None
    for s in steps:
        if prev is None or s.group != prev:
            group_no += 1
            letter_i = 0
            prev = s.group
        # Past 26 stops in one group the letters would wrap; the step cap makes
        # that unreachable, but clamp rather than IndexError if MAX_STEPS moves.
        s.label = f"{group_no}{_LETTERS[min(letter_i, len(_LETTERS) - 1)]}"
        letter_i += 1
    return steps


def build_walkthrough(question: str, answer: str, steps: list[AskStep]) -> Walkthrough:
    """Clip prose, cap the step count, and number the steps. Pure."""
    kept = steps[:MAX_STEPS]
    for s in kept:
        s.note = _clip(s.note, MAX_NOTE_CHARS)
        s.group = _clip(s.group, MAX_GROUP_CHARS)
        # The quote is matched verbatim against the description, so it is NOT
        # whitespace-collapsed here — only bounded, and only at a length no real
        # highlight needs.
        s.quote = (s.quote or "").strip()[:280]
    label_steps(kept)
    return Walkthrough(
        question=_clip(question, MAX_QUESTION_CHARS),
        answer=_clip(answer, MAX_ANSWER_CHARS),
        steps=kept,
        id=f"ask-{_time.time_ns()}",
        at=_now_iso(),
    )


def write_walkthrough(codoc_dir: str | Path, walk: Walkthrough) -> None:
    """Replace the single walkthrough slot, atomically and under the lock.

    Single slot on purpose: two overlays at once would put two numbers on one
    feature, and the reader would have no way to tell which question they answer.
    A new ask replaces the old one.
    """
    from filelock import FileLock

    codoc_dir = Path(codoc_dir)
    dest = ask_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(codoc_dir / (ASK_FILENAME + ".lock")), timeout=5):
        atomic_write_json(dest, walk.to_dict())


def read_walkthrough(codoc_dir: str | Path, *, now: float | None = None,
                     ttl: float = ASK_TTL_SECONDS) -> dict | None:
    """Return the current walkthrough, or None when absent / corrupt / expired.

    Expiry is keyed on file mtime rather than the recorded ``at`` so a clock
    change cannot resurrect one, matching how ``activity.epoch_alive`` leases.
    """
    path = ask_path(codoc_dir)
    data = read_json(path, default={})
    if not isinstance(data, dict) or not data.get("steps"):
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if (now if now is not None else _time.time()) - mtime > ttl:
        return None
    return data


def clear_walkthrough(codoc_dir: str | Path) -> bool:
    """Delete the overlay. True iff a file was removed. Idempotent."""
    try:
        ask_path(codoc_dir).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
