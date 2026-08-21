"""Warrant — resolving a described why back to the evidence that licensed it.

``loop/why.py`` finds the prose a repo actually recorded about its own decisions
and hands it to the describing pass. That closed one gap and opened another: the
prompt now carries three sources at once, the model writes one paragraph, and
nothing records WHICH source the paragraph rests on. A description grounded in a
commit message and a description that outran every source in the block read
identically on the page — and the second is precisely the failure ``why.py``
exists to prevent. The timeline could already draw a chain (sentence → event →
directive → prompt → commit → diff), but a chain says what *happened before* a
claim, not what the claim is *warranted by*.

So the describing pass cites, and this module resolves. The contract is short:

* The model returns ``warrant: ["c1", "d2"]`` — ids from the evidence block it was
  given (``why.py.stamp_ids``) plus ``a1``… for the author's own live prompts.
* :func:`resolve` looks each id up and records what that entry ACTUALLY said.
  The quote never comes from the model. Asking it to repeat the evidence back
  would let a paraphrase drift toward the claim it is supposed to check, which
  is the one direction an error must not be free to travel.
* An id that resolves to nothing is **dropped**, and an op with a fabricated
  citation ends up unwarranted rather than warranted by a source that does not
  exist. There is no error and no retry: a missing warrant is a normal state
  (most descriptions state what code achieves and make no why claim at all), so
  failing the pass over one would spend a user's tree update on a formality.

The ids are positional within a single prompt and are deliberately not stored.
What is stored is the resolved :class:`~codoc.model.event.Warrant` — kind, a
reference a reader can go check, and the quote — because that survives the pass
it was minted in.

Ordering follows ``why.py``'s own hierarchy of directness rather than the order
the model happened to cite in: the author's live prompt first, then the directive
they queued, then a commit message, then a reason an earlier pass recorded. A
reader scanning "Rests on" wants the strongest ground first, and the model's
citation order carries no information worth preserving.
"""
from __future__ import annotations

from codoc.doclang import char_budget
from codoc.model.event import Warrant

# One entry per source, so a citation cannot silently spend the whole budget.
MAX_WARRANTS = 4
_QUOTE_CHARS = 240   # rescaled per script; a warrant is a pointer, not a copy

# The author's own prompts get their own prefix because they are not part of the
# why_evidence block: `intent.relevant_intent` reads the live session, which is
# the strongest rationale source there is (a person saying what they want, now,
# rather than a commit message written after the fact — see papers/03). A warrant
# system that could not cite it would warrant only the weaker three.
INTENT_PREFIX = "a"

# Rank by how directly a source speaks to a decision, not by citation order.
_RANK = {"intent": 0, "directive": 1, "commit": 2, "prior": 3}


def _clip(text: str) -> str:
    t = " ".join((text or "").split())
    cap = char_budget(_QUOTE_CHARS, t)
    return (t[:cap].rstrip() + "…") if len(t) > cap else t


def _commit_quote(entry: dict) -> str:
    subject = str(entry.get("subject") or "")
    why = str(entry.get("why") or "")
    # Subject and body gist together: the subject names the change and the body
    # states the reason, and a warrant showing only one of them makes the reader
    # go open the commit to learn which.
    return _clip(f"{subject} — {why}" if subject and why else subject or why)


def index_evidence(changes: dict | None) -> dict[str, Warrant]:
    """Map every citable id in one pass's inputs to its resolved warrant.

    Reads the two keys ``loop_a`` puts on the changeset: ``why_evidence`` (the
    id-stamped block from :mod:`codoc.loop.why`) and ``author_intent`` (the live
    session prompts). Built from the same object that was serialized into the
    prompt, so an id the model could see is an id this can resolve — the index and
    the prompt cannot drift apart.
    """
    out: dict[str, Warrant] = {}
    if not isinstance(changes, dict):
        return out

    block = changes.get("why_evidence")
    if isinstance(block, dict):
        for entry in block.get("commits") or ():
            if isinstance(entry, dict) and entry.get("id"):
                out[str(entry["id"])] = Warrant(
                    kind="commit", ref=str(entry.get("sha") or ""),
                    quote=_commit_quote(entry),
                )
        for entry in block.get("directives") or ():
            if isinstance(entry, dict) and entry.get("id"):
                out[str(entry["id"])] = Warrant(
                    kind="directive", ref=str(entry.get("feature_id") or ""),
                    quote=_clip(str(entry.get("asked") or "")),
                )
        for entry in block.get("prior") or ():
            if isinstance(entry, dict) and entry.get("id"):
                notes = entry.get("recorded") or []
                out[str(entry["id"])] = Warrant(
                    kind="prior", ref=str(entry.get("feature_id") or ""),
                    quote=_clip(str(notes[0]) if notes else ""),
                )

    for entry in changes.get("author_intent") or ():
        # A str here is the pre-warrant shape (`relevant_intent` returns bare
        # strings and `loop_b` still consumes them that way). Uncitable, but it
        # must not raise: an older caller sending strings should lose the ability
        # to cite intent, not the ability to run.
        if isinstance(entry, dict) and entry.get("id"):
            out[str(entry["id"])] = Warrant(
                kind="intent", ref="", quote=_clip(str(entry.get("asked") or "")),
            )
    return {k: w for k, w in out.items() if w.quote}


def resolve(index: dict[str, Warrant], cited) -> list[Warrant]:
    """The warrants for ``cited``, unknown ids dropped, strongest source first.

    ``cited`` is whatever the model returned: a list of ids, a single id, or a
    comma-separated string. Being liberal here costs nothing — the ids are checked
    against the index either way, so a lenient parse can only recover a real
    citation that arrived in an unexpected wrapper, never admit an invented one.
    """
    if not index or not cited:
        return []
    if isinstance(cited, str):
        cited = [part for part in cited.replace(";", ",").split(",")]
    if not isinstance(cited, (list, tuple, set)):
        return []

    picked: list[Warrant] = []
    seen: set[str] = set()
    for raw in cited:
        if isinstance(raw, dict):  # a model that returned objects instead of ids
            raw = raw.get("id") or raw.get("ref") or ""
        key = str(raw).strip().strip("`\"'").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        hit = index.get(key)
        if hit is not None:
            picked.append(hit)
    picked.sort(key=lambda w: _RANK.get(w.kind, 9))
    return picked[:MAX_WARRANTS]


def as_rows(warrants) -> list[dict]:
    """The wire shape for the sidecar and the timeline: presence-keyed dicts.

    Every ``.codoc`` projection omits a key it has no value for rather than
    sending an empty one, so the reader can treat absence as the answer instead of
    checking two things.
    """
    rows: list[dict] = []
    for w in warrants or ():
        kind = getattr(w, "kind", "") or (w.get("kind", "") if isinstance(w, dict) else "")
        ref = getattr(w, "ref", "") or (w.get("ref", "") if isinstance(w, dict) else "")
        quote = getattr(w, "quote", "") or (w.get("quote", "") if isinstance(w, dict) else "")
        if not quote:
            continue
        row: dict = {"kind": kind or "", "quote": quote}
        if ref:
            row["ref"] = ref
        rows.append(row)
    return rows
