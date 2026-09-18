"""``ingest``: the one boundary where a diff decides (spec §4).

A person types ops. Everyone else — the loop's LLM pass, a translator, a
Markdown import, an agent's ``codoc_write_block`` — hands in whole new text for
one Text field. This module turns that into ops, once, under the rules:

1. The caller states the text it was shown (``base_text``). It must equal the
   field's current live text; otherwise the call is refused as ``stale_base``
   and the caller re-reads. (The substrate records the frontier the call ran
   at on every op it mints.)
2. Text is NFC. A paragraph break inside the new text is refused: the caller
   addresses blocks, not descriptions (the LLM prompt is block-keyed).
3. Tokens are script-aware: words, punctuation runs and whitespace runs for
   spaced scripts, one character per token for unspaced ones. The diff is a
   longest-common-subsequence over tokens in which whitespace never anchors a
   match on its own (it survives only beside a surviving word), so a rewritten
   sentence is one hunk, an appended word re-authors nothing, and a changed
   comma leaves the word before it with its author. Each hunk is one
   ``text.delete`` over the base characters it drops and one ``text.insert``
   anchored after the last surviving base character before it (or START).
4. Marks survive by identity and the result is a fixed point: after the ops
   apply, ``markdown(field) == new_md``. An existing mark whose surviving
   characters the new text still marks the same way is kept; where the new
   extent differs, the mark is widened by an ``on`` over the new span (bold
   merges; a link or cite displaces and the inverse restores) and shrunk by an
   ``off`` over the surviving characters the new text no longer marks. A bold
   whose end an insert lands on would grow over the insert (I2's one growing
   kind), and any mark swallows an insert anchored inside it; when the new
   text does not mark those words, an ``off`` over them follows the insert. A
   loop or translator call that would remove a human-placed bold, link or cite
   from every character it covers refuses the block when it writes the trunk
   (``trunk=True``); under a plan the person decides, so nothing is refused.
5. The result carries ``deleted_fraction``; the caller's gate decides trunk or
   plan from it before minting.

Pure: this module mints no ids and applies nothing. It returns op *bodies* in
order plus a decision; the substrate mints ids and applies them as one batch.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from codoc.calculus import ops as K
from codoc.calculus.ids import START, CharRef, actor_kind, is_human
from codoc.calculus.inline_md import Span, parse
from codoc.calculus.text import BOLD, CITE, GROWS_AFTER, LINK, Mark, Text, nfc

VISIBLE_MARKS = frozenset({BOLD, LINK, CITE})     # the kinds Markdown can express
REFUSING_KINDS = frozenset({"loop", "translator"})  # actors that may not drop a person's mark on the trunk


@dataclass
class Ingest:
    bodies: list[dict] = field(default_factory=list)   # {kind, target, payload}, in apply order
    deleted_fraction: float = 0.0
    inserted: int = 0
    deleted: int = 0
    refused: str | None = None
    demote_to_plan: str | None = None                    # set by the substrate when its gate opened a plan

    @property
    def unchanged(self) -> bool:
        return not self.bodies and self.refused is None


def tokenize(text: str) -> list[str]:
    """Lossless script-aware tokens: ``"".join(tokenize(t)) == t``. Words,
    punctuation runs and whitespace runs are separate tokens; each character
    of an unspaced script (Han, Kana, Thai …) is its own token, so a rewrite of
    one character does not re-author a sentence."""
    out: list[str] = []
    for m in re.finditer(r"\s+|\w+|[^\w\s]+", text):
        tok = m.group(0)
        if not tok.isspace() and _unspaced(tok):
            out.extend(tok)
        else:
            out.append(tok)
    return out


def _unspaced(word: str) -> bool:
    return any(unicodedata.east_asian_width(c) in ("W", "F") and c.isalpha() for c in word)


def ingest(text: Text, *, bid: str, path: str, base_text: str, new_md: str, actor: str,
           trunk: bool = True, multiline: bool = False) -> Ingest:
    """Compute the ops that turn ``text`` (live: ``base_text``) into the prose
    and marks of ``new_md``. See the module docstring for the rules.
    ``trunk`` says the ops will land on the trunk (the human-mark refusal
    applies); ``multiline`` is set for a kind's Text field whose newlines are
    content (a diagram's source); prose refuses them."""
    res = Ingest()
    new_plain, new_spans = parse(nfc(new_md))
    if "\n" in new_plain and not multiline:
        res.refused = "multi_paragraph"
        return res
    live = text.live_text()
    if nfc(base_text) != live:
        res.refused = "stale_base"
        return res
    refs = list(text.live_refs())
    a, b = tokenize(live), tokenize(new_plain)
    sm = SequenceMatcher(str.isspace, a, b, autojunk=False)
    a_off, b_off = _offsets(a), _offsets(b)
    survivors: dict[int, CharRef] = {}                    # new offset → base char that survives there
    deletes: list[tuple[CharRef, CharRef]] = []
    inserts: list[tuple[CharRef | str, int, int]] = []    # (anchor, new_start, new_end)
    last_survivor: CharRef | str = START
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                for c in range(len(a[k])):
                    survivors[b_off[j1 + (k - i1)] + c] = refs[a_off[k] + c]
            last_survivor = refs[a_off[i2 - 1] + len(a[i2 - 1]) - 1]
            continue
        if i2 > i1:
            deletes.append((refs[a_off[i1]], refs[a_off[i2 - 1] + len(a[i2 - 1]) - 1]))
        if j2 > j1:
            inserts.append((last_survivor, b_off[j1], b_off[j2 - 1] + len(b[j2 - 1])))
    index = {r: i for i, r in enumerate(refs)}
    res.deleted = sum(index[e] - index[s] + 1 for s, e in deletes)
    res.inserted = sum(e - s for _, s, e in inserts)
    res.deleted_fraction = (res.deleted / len(live)) if live else 0.0

    # Marks: where each existing mark's surviving characters sit in the new
    # text, and which new offsets each kind of span covers.
    to_new = {r: o for o, r in survivors.items()}
    cover: dict[tuple, set[int]] = {}
    for sp in new_spans:
        if sp.end > sp.start:
            cover.setdefault(_key(sp.kind, sp.attrs), set()).update(range(sp.start, sp.end))
    existing: list[tuple[Mark, list[int]]] = []
    for m in text.marks:
        if m.kind not in VISIBLE_MARKS:
            continue
        offs = sorted(to_new[r] for r in text.mark_refs(m) if r in to_new)
        existing.append((m, offs))
        kept = any(o in cover.get(_key(m.kind, m.attrs), ()) for o in offs)
        if trunk and actor_kind(actor) in REFUSING_KINDS and is_human(m.actor) and not kept and text.mark_refs(m):
            res.refused = f"would_drop_human_{m.kind}"
            return res

    target = {"bid": bid, "path": path}
    for s, e in deletes:
        res.bodies.append({"kind": K.TEXT_DELETE, "target": target, "payload": {"from": list(s), "to": list(e)}})
    for anchor, s, e in inserts:
        res.bodies.append({"kind": K.TEXT_INSERT, "target": target,
                           "payload": {"after": anchor if isinstance(anchor, str) else list(anchor),
                                       "text": new_plain[s:e]}})

    def mark(kind: str, attrs: dict, start, end, on: bool) -> None:
        res.bodies.append({"kind": K.TEXT_MARK, "target": target,
                           "payload": {"kind": kind, "attrs": dict(attrs), "start": start, "end": end, "on": on}})

    # Shrink: surviving characters the new text no longer marks this way.
    for m, offs in existing:
        covered = cover.get(_key(m.kind, m.attrs), set())
        for lo, hi in _runs(o for o in offs if o not in covered):
            mark(m.kind, m.attrs, list(survivors[lo]), list(survivors[hi]), False)
    # An insert anchored inside a mark (in sequence order, dead characters
    # included) or at the end of a growing one is covered by that mark once it
    # lands: take the mark off the inserted words the new text does not mark.
    for idx, (anchor, s, e) in enumerate(inserts):
        for m, _ in existing:
            if anchor == START or not _swallows(text, m, anchor):
                continue
            covered = cover.get(_key(m.kind, m.attrs), set())
            for lo, hi in _runs(o for o in range(s, e) if o not in covered):
                mark(m.kind, m.attrs, ["$ins", idx, lo - s], ["$ins", idx, hi - s], False)
    # Widen or mint: every new span not already wholly under a same mark.
    for sp in new_spans:
        if sp.end <= sp.start:
            continue
        want = set(range(sp.start, sp.end))
        if any(m.kind == sp.kind and m.attrs == sp.attrs and want <= set(offs) for m, offs in existing):
            continue
        mark(sp.kind, sp.attrs, _ref_for(sp.start, survivors, inserts), _ref_for(sp.end - 1, survivors, inserts), True)
    return res


def _key(kind: str, attrs: dict) -> tuple:
    return (kind, tuple(sorted(attrs.items())))


def _swallows(text: Text, m: Mark, anchor: CharRef) -> bool:
    """Would a segment inserted right after ``anchor`` sit under ``m``? Yes
    when the anchor is inside the mark's range in sequence order (its end
    character may be dead), or is the end of a mark that grows after."""
    a = text.locate(anchor)
    lo, hi = text.locate(m.start), text.locate(m.end)
    return lo <= a < hi or (m.kind in GROWS_AFTER and a == hi)


def _runs(offsets) -> list[tuple[int, int]]:
    """Maximal runs of consecutive integers, as inclusive ``(lo, hi)`` pairs."""
    out: list[tuple[int, int]] = []
    for o in sorted(offsets):
        if out and out[-1][1] + 1 == o:
            out[-1] = (out[-1][0], o)
        else:
            out.append((o, o))
    return out


def _offsets(tokens: list[str]) -> list[int]:
    out, pos = [], 0
    for t in tokens:
        out.append(pos)
        pos += len(t)
    return out


def _ref_for(offset: int, survivors: dict[int, CharRef], inserts: list[tuple[CharRef | str, int, int]]) -> list:
    """A new-text offset as a wire anchor: the surviving base character, or the
    insert that minted it as ``["$ins", index, k]`` (every new offset is one or
    the other by construction of the opcodes)."""
    if offset in survivors:
        return list(survivors[offset])
    for idx, (_, s, e) in enumerate(inserts):
        if s <= offset < e:
            return ["$ins", idx, offset - s]
    raise AssertionError(f"offset {offset} maps to neither a survivor nor an insert")


def as_plan_bodies(bodies: list[dict], plan: str) -> list[dict]:
    """A plan proposes; it never kills. Every ``text.delete`` an ingest produced
    becomes a ``suggest_del`` mark tagged with the plan, so the words stay live
    and struck until a verdict (spec §3, ``plan.open``)."""
    out = []
    for b in bodies:
        if b["kind"] == K.TEXT_DELETE:
            p = b["payload"]
            out.append({"kind": K.TEXT_MARK, "target": b["target"],
                        "payload": {"kind": "suggest_del", "attrs": {"plan": plan},
                                    "start": p["from"], "end": p["to"], "on": True}})
        else:
            out.append(b)
    return out


def resolve_pending_refs(bodies: list[dict], insert_ids: list[str]) -> None:
    """Replace ``["$ins", i, k]`` anchors with real char refs once the i-th
    insert body has been minted as ``insert_ids[i]``. Mutates in place."""
    for b in bodies:
        p = b["payload"]
        for key in ("start", "end", "after"):
            v = p.get(key)
            if isinstance(v, list) and len(v) == 3 and v[0] == "$ins":
                p[key] = [insert_ids[v[1]], v[2]]
