"""``ingest``: the one boundary where a diff decides (spec §4).

A person types ops. Everyone else — the loop's LLM pass, a translator, a
Markdown import, an agent's ``codoc_write_block`` — hands in whole new text for
one Text field. This module turns that into ops, once, under the rules:

1. The caller states the text it was shown (``base_text``) and the frontier it
   was shown at (``base``). The field's text at ``base`` must equal
   ``base_text``; otherwise the call is refused and the caller re-reads. That is
   the ledger's ``base_text`` arbitration, kept.
2. Text is NFC. A paragraph break inside the new text is refused: the caller
   addresses blocks, not descriptions (the LLM prompt is block-keyed).
3. Tokens are script-aware (words and whitespace for spaced scripts, one
   character per token for unspaced ones). A longest-common-subsequence diff
   over tokens yields hunks; each hunk is one ``text.delete`` over the base
   characters it drops and one ``text.insert`` anchored after the last
   surviving base character before it (or START).
4. If anyone inserted into the field after ``base`` (live characters beyond the
   frontier), a trunk write is demoted to a plan: the document wins.
5. Marks survive by identity. A mark in the new text with the same kind and
   attrs as an existing mark that still covers a surviving character is the
   existing mark; nothing is emitted for it. A new-text mark with no such
   counterpart is minted by this call. A mark whose characters all die dies
   with them. A loop or translator call that would kill every live character of
   a human-placed bold, link or cite refuses the block.
6. The result carries ``deleted_fraction``; the caller's gate decides trunk or
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
from codoc.calculus.ids import START, CharRef
from codoc.calculus.inline_md import Span, parse
from codoc.calculus.ops import in_frontier
from codoc.calculus.text import BOLD, CITE, LINK, Mark, Text, nfc

_HUMAN_MARKS = frozenset({BOLD, LINK, CITE})
_MACHINE_ACTORS = ("loop", "translator:")


@dataclass
class Ingest:
    bodies: list[dict] = field(default_factory=list)   # {kind, target, payload}
    deleted_fraction: float = 0.0
    inserted: int = 0
    deleted: int = 0
    refused: str | None = None
    demote_to_plan: str | None = None                    # reason, when a trunk write must be a plan


class Refused(Exception):
    pass


def tokenize(text: str) -> list[str]:
    """Lossless script-aware tokens: ``"".join(tokenize(t)) == t``. A token is
    a word with its punctuation and trailing whitespace (so whitespace never
    matches on its own and a rewritten sentence is one hunk, not a lattice of
    surviving spaces); each character of an unspaced script (Han, Kana, Thai …)
    is its own token, so a rewrite of one character does not re-author a
    sentence."""
    out: list[str] = []
    for m in re.finditer(r"\s+|\S+\s*", text):
        tok = m.group(0)
        if tok.isspace():
            out.append(tok)
            continue
        if _unspaced(tok):
            body = tok.rstrip()
            trail = tok[len(body):]
            chars = list(body)
            if trail:
                chars[-1] += trail
            out.extend(chars)
        else:
            out.append(tok)
    return out


def _unspaced(word: str) -> bool:
    return any(unicodedata.east_asian_width(c) in ("W", "F") and c.isalpha() for c in word)


def ingest(text: Text, *, bid: str, path: str, base_text: str, new_md: str, actor: str,
           base: dict, plan: str | None = None) -> Ingest:
    """Compute the ops that turn ``text`` (live: ``base_text``) into the prose
    and marks of ``new_md``. See the module docstring for the rules."""
    res = Ingest()
    new_plain, new_spans = parse(nfc(new_md))
    if "\n" in new_plain:
        res.refused = "multi_paragraph"
        return res
    live = text.live_text()
    if nfc(base_text) != live:
        # Was the field edited after base? Then the caller's text is stale by
        # construction; either way it must re-read.
        res.refused = "stale_base"
        return res
    refs = list(text.live_refs())
    assert len(refs) == len(live)
    if plan is None and any(not in_frontier(r[0], base) for r in refs):
        res.demote_to_plan = "edited_since_base"
    a, b = tokenize(live), tokenize(new_plain)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    # Offsets of each token in its text.
    a_off = _offsets(a)
    b_off = _offsets(b)
    survivors: dict[int, CharRef] = {}      # new offset -> base char that survives there
    deletes: list[tuple[CharRef, CharRef]] = []
    inserts: list[tuple[CharRef | str, int, int]] = []   # (anchor, new_start, new_end)
    last_survivor: CharRef | str = START
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                for c in range(len(a[k])):
                    survivors[b_off[j1 + (k - i1)] + c] = refs[a_off[k] + c]
            last_survivor = refs[a_off[i2 - 1] + len(a[i2 - 1]) - 1] if i2 > i1 else last_survivor
            continue
        if i2 > i1:
            deletes.append((refs[a_off[i1]], refs[a_off[i2 - 1] + len(a[i2 - 1]) - 1]))
        if j2 > j1:
            inserts.append((last_survivor, b_off[j1], b_off[j2 - 1] + len(b[j2 - 1])))
    deleted_chars = sum(text.offset_of(e) - text.offset_of(s) + 1 for s, e in deletes)  # type: ignore[operator]
    res.deleted = deleted_chars
    res.inserted = sum(e - s for _, s, e in inserts)
    res.deleted_fraction = (deleted_chars / len(live)) if live else 0.0
    # Human marks that would die entirely under a machine write: refuse.
    if actor.startswith(_MACHINE_ACTORS):
        dying = _dying_refs(text, deletes)
        for m in text.marks:
            if m.kind in _HUMAN_MARKS and m.actor.startswith("human:"):
                covered = [r for r in _mark_live_refs(text, m)]
                if covered and all(r in dying for r in covered):
                    res.refused = f"would_drop_human_{m.kind}"
                    return res
    target = {"bid": bid, "path": path}
    for s, e in deletes:
        res.bodies.append({"kind": K.TEXT_DELETE, "target": target, "payload": {"from": list(s), "to": list(e)}})
    for anchor, s, e in inserts:
        res.bodies.append({"kind": K.TEXT_INSERT, "target": target,
                           "payload": {"after": anchor if isinstance(anchor, str) else list(anchor),
                                       "text": new_plain[s:e]}})
    # Marks: identity, never content. Emitted as ops referring to base chars
    # where the span covers survivors; spans over freshly inserted text refer
    # to the insert by ordinal ("$ins", index, offset) which the substrate
    # resolves once the insert's op id exists.
    for sp in new_spans:
        if sp.end <= sp.start:
            continue
        span_survivors = [survivors[o] for o in range(sp.start, sp.end) if o in survivors]
        if span_survivors and any(_mark_covers(text, m, span_survivors) and m.kind == sp.kind and m.attrs == sp.attrs
                                  for m in text.marks):
            continue  # an existing mark survives by identity
        start_ref = _ref_for(sp.start, survivors, inserts)
        end_ref = _ref_for(sp.end - 1, survivors, inserts)
        res.bodies.append({"kind": K.TEXT_MARK, "target": target,
                           "payload": {"kind": sp.kind, "attrs": sp.attrs, "start": start_ref, "end": end_ref, "on": True}})
    return res


def _offsets(tokens: list[str]) -> list[int]:
    out, pos = [], 0
    for t in tokens:
        out.append(pos)
        pos += len(t)
    return out


def _dying_refs(text: Text, deletes: list[tuple[CharRef, CharRef]]) -> set[CharRef]:
    dying: set[CharRef] = set()
    refs = list(text.live_refs())
    index = {r: i for i, r in enumerate(refs)}
    for s, e in deletes:
        for i in range(index[s], index[e] + 1):
            dying.add(refs[i])
    return dying


def _mark_live_refs(text: Text, m: Mark):
    si = text.locate(m.start)
    ei = text.locate(m.end)
    for i in range(si[0], ei[0] + 1):
        seg = text.segs[i]
        if not seg.live:
            continue
        lo = si[1] if i == si[0] else 0
        hi = ei[1] if i == ei[0] else len(seg.chars) - 1
        for k in range(lo, hi + 1):
            yield seg.ref(k)


def _mark_covers(text: Text, m: Mark, refs: list[CharRef]) -> bool:
    live = set(_mark_live_refs(text, m))
    return any(r in live for r in refs)


def _ref_for(offset: int, survivors: dict[int, CharRef], inserts: list[tuple[CharRef | str, int, int]]):
    if offset in survivors:
        return list(survivors[offset])
    for idx, (_, s, e) in enumerate(inserts):
        if s <= offset < e:
            return ["$ins", idx, offset - s]
    raise Refused(f"offset {offset} maps to neither a survivor nor an insert")


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
