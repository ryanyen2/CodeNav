"""The voice-inference LLM pass — read a person's rewrites, name the preference.

CIPHER's first step (see :mod:`codoc.model.voice`): given the gap between what
codoc wrote and what the author left, say what that gap teaches about how they
write. Like :mod:`codoc.agent.translate` this module is deliberately dumb about
policy — it classifies and phrases, and every decision about what to KEEP (which
rewrites are worth sending, whether two lessons are the same one, when a lesson may
start shaping prose) lives in :mod:`codoc.loop.voice` where it is testable without
an LLM.

Batched, because a batch is what makes a weak signal readable: one rewrite that
shortened a paragraph could be anything, while three that all shortened one are a
preference, and only a model that sees them together can say so.
"""
from __future__ import annotations

import json
import logging

from codoc.agent.base import format_prompt, load_prompt, run_agent, split_prompt
from codoc.config import LLMConfig
from codoc.doclang import DocLanguage
from codoc.model.voice import EditKind, LessonAxis

_log = logging.getLogger(__name__)


class InferredLesson:
    """One classified rewrite, and the lesson drawn from it (or none).

    A plain class rather than a pydantic model: this crosses exactly one function
    boundary before :mod:`codoc.loop.voice` turns it into a
    :class:`~codoc.model.voice.StyleLesson`, and a second validated shape for the
    same data would only be a place for the two to disagree.
    """

    __slots__ = ("event_id", "kind", "axis", "instruction", "axis_detail")

    def __init__(self, event_id: str, kind: EditKind, axis: LessonAxis | None = None,
                 instruction: str = "", axis_detail: str = "") -> None:
        self.event_id = event_id
        self.kind = kind
        self.axis = axis
        self.instruction = instruction
        self.axis_detail = axis_detail

    @property
    def has_lesson(self) -> bool:
        return self.axis is not None and bool(self.instruction.strip())

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (f"InferredLesson({self.event_id}, {self.kind.value}, "
                f"{self.axis.value if self.axis else None}, {self.instruction!r})")


def infer_lessons(
    rewrites: list[dict],
    *,
    config: LLMConfig | None = None,
    doc_language: DocLanguage | None = None,
) -> list[InferredLesson]:
    """Classify each rewrite and name its lesson.

    ``rewrites`` is a list of ``{event_id, feature_title, field, before, after,
    tree_path}`` dicts, built by :func:`codoc.loop.voice.harvest`. Returns one entry
    per rewrite the model answered about usably; a rewrite it dropped simply does
    not appear, and the caller treats that as "no lesson here" rather than as an
    error, because the watermark advances either way and a missing answer costs
    nothing but that one edit's signal.

    NOT the fast tier, for the reason :mod:`codoc.agent.translate` gives: the
    judgment asked for here is about register, and a model that cannot hear the
    difference between two paragraphs cannot report it. A wrong answer is worse than
    no answer, because it becomes an instruction.
    """
    if not rewrites:
        return []

    prefix_tpls, volatile_tpl = split_prompt(
        load_prompt("voice_infer", doc_language=doc_language))
    kwargs = dict(rewrites=json.dumps(rewrites, indent=2, ensure_ascii=False))
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)

    try:
        raw = run_agent(volatile, config, prefix_parts=prefix_parts)
    except Exception as exc:  # noqa: BLE001 — learning is optional; never sink a pass
        _log.warning("codoc voice: unparseable inference response (%s); no lessons", exc)
        return []

    rows = raw.get("rewrites", []) if isinstance(raw, dict) else raw
    out: list[InferredLesson] = []
    for row in rows if isinstance(rows, list) else []:
        parsed = _coerce(row)
        if parsed is not None:
            out.append(parsed)
    return out


def _coerce(row: object) -> InferredLesson | None:
    """One response row as an :class:`InferredLesson`, or None if it is unusable.

    Per-row tolerance, the same bargain :mod:`codoc.agent.tree_update` makes: one
    malformed entry must not discard the model's answers about the other rewrites in
    the batch. An unrecognized ``kind`` or ``axis`` is dropped rather than guessed
    at — a lesson filed under the wrong axis would be retrieved for the wrong
    question, which is worse than not having it.
    """
    if not isinstance(row, dict):
        return None
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        return None
    try:
        kind = EditKind(str(row.get("kind") or "").strip())
    except ValueError:
        _log.warning("codoc voice: dropping rewrite with unknown kind %r", row.get("kind"))
        return None

    lesson = row.get("lesson")
    if not isinstance(lesson, dict):
        return InferredLesson(event_id, kind)  # classified, no lesson — a normal answer
    instruction = str(lesson.get("instruction") or "").strip()
    try:
        axis = LessonAxis(str(lesson.get("axis") or "").strip())
    except ValueError:
        _log.warning("codoc voice: dropping lesson with unknown axis %r", lesson.get("axis"))
        return InferredLesson(event_id, kind)
    if not instruction:
        return InferredLesson(event_id, kind)
    return InferredLesson(
        event_id, kind, axis, instruction,
        str(lesson.get("axis_detail") or "").strip(),
    )
