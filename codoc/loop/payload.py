"""How much of each chunk's source a prompt gets to see.

Every pass that shows the model code does it the same way: `symbol_path` plus the
first N characters of `source`. N was a per-chunk constant, which makes the prompt
a function of how many chunks the pass happens to be looking at — fine for a
20-symbol file, and 258,000 characters for one generated module in `test/altair`
(`channels.py`, 786 chunks). That is not merely expensive. A single call asked to
name a coherent feature set over 786 symbols returns a junk drawer, and the rule
the prompt itself states (at most ~12 bindings to a node) cannot be honored.

So the budget is **per pass, not per chunk**, and it is spent where it decides
something:

- **A member is governed by its class.** The prompt's own rule binds a method to
  the feature its class owns, and no amount of the method's body changes that. So
  when the budget binds, members are compressed first.
- **A truncated body reads as a complete short one.** Slicing at a byte boundary
  hands the model a function that appears to end where the slice did, so `head`
  keeps whole lines, guarantees the signature, and says that it elided.
- **Nothing drops to nothing.** The lowest rung still carries a signature, so
  every symbol in the pass arrives named and typed even in the worst case — a
  partition over all of them is still possible, which is what folding uncovered
  chunks into the largest node can never recover.

A file under the budget is passed through unchanged, so the ordinary case — every
file in `test/requests`, `codoc/store/db.py` at 95 chunks — sees exactly what it
saw before. This only ever takes effect where the alternative was a quarter of a
megabyte of prompt.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: chars of one chunk's source a pass shows when the whole set fits
PER_CHUNK = 600

#: total chars of source one pass may spend across every chunk it shows
BUDGET = 72_000

# The ladder of concessions, richest first: (member allowance, top-level
# allowance). Members give way before top-level definitions because the partition
# is decided at the top level; the last rung still holds a signature, so a symbol
# is never reduced to its name alone. Ordered and monotone, so the rung a set
# lands on is itself a readable statement of how crowded that set is.
_LADDER: tuple[tuple[int, int], ...] = (
    (PER_CHUNK, PER_CHUNK),
    (240, PER_CHUNK),
    (160, 480),
    (120, 320),
    (80, 200),
    (60, 120),
)

#: past this many leading lines, stop looking for a signature to end
_SIGNATURE_LINES = 12

# A line that opens a body: `:` (Python), `{` (TypeScript), or `: ...` — the stub
# body that generated and overloaded Python is largely made of. A `}` or a `]` at
# the end is a data literal closing, not a body opening, so it must not match.
_BODY_OPENS = re.compile(r"[:{]\s*(\.\.\.)?\s*$")

#: appended to a chunk whose source was cut, so an elision cannot read as an end
ELISION = "\n…"


def _is_member(symbol_path: str) -> bool:
    """True for a method or nested definition — a name owned by another name.

    `file.py::Store.insert` is a member; `file.py::open_store` and
    `file.py::__module__` are not. Split on the LAST `::` so a path is read the
    way the index writes it, then look for the dotted owner.

    The TypeScript adapter addresses only top-level declarations today, so a `.ts`
    file has no members and the ladder's first concession simply never fires there
    — it falls straight to shrinking the top-level share, which is correct.
    """
    return "." in symbol_path.rsplit("::", 1)[-1]


def shown_sources(
    sources: Mapping[str, str],
    *,
    budget: int = BUDGET,
    per_chunk: int = PER_CHUNK,
) -> dict[str, str]:
    """`symbol_path` → the source text a pass shows for it, within *budget*.

    The one entry point: a caller hands over what it has and gets back what to
    send, so the budget cannot be computed in one place and spent in another.
    """
    allowed = allowances(sources, budget=budget, per_chunk=per_chunk)
    return {path: head(source, allowed[path]) for path, source in sources.items()}


def allowances(
    sources: Mapping[str, str],
    *,
    budget: int = BUDGET,
    per_chunk: int = PER_CHUNK,
) -> dict[str, int]:
    """Chars of source each symbol may show, so the whole set fits in *budget*.

    The rung is chosen against what the set would ACTUALLY cost once cut — not
    against allowance times count. Most definitions are shorter than their
    allowance, and charging the set for source nobody is going to send concedes two
    rungs it can afford; on `test/altair`'s `channels.py` that is the difference
    between showing 60 characters of a method and showing 240.

    Returns an allowance for every symbol given (never zero). When the set fits at
    *per_chunk* every symbol gets *per_chunk* — the identity case, and most calls.

    **The floor is the set of signatures, and there the budget yields.** `head`
    keeps a signature whole even past its allowance, so a module of 923 wrapped
    generated signatures costs what those signatures cost and no rung can go lower.
    Cutting them instead would hand the model a list of names with no parameters,
    which is the one thing it cannot work from.
    """
    if not sources:
        return {}
    ladder = tuple((min(m, per_chunk), min(t, per_chunk)) for m, t in _LADDER)
    member_share, top_share = ladder[-1]
    for candidate_member, candidate_top in ladder:
        cost = sum(
            len(head(source, candidate_member if _is_member(path) else candidate_top))
            for path, source in sources.items()
        )
        if cost <= budget:
            member_share, top_share = candidate_member, candidate_top
            break
    return {p: (member_share if _is_member(p) else top_share) for p in sources}


def head(source: str, limit: int) -> str:
    """*source* cut to about *limit* chars, keeping whole lines and the signature.

    The signature is kept even when it alone exceeds *limit* — a name shown without
    its parameters is worse than a long line, because the parameters are what the
    reader is being asked to recognize. Anything cut is marked, so the model reads
    an elision as an elision.
    """
    if len(source) <= limit:
        return source
    lines = source.splitlines()
    kept = lines[: _signature_end(lines) + 1]
    used = sum(len(line) + 1 for line in kept)
    for line in lines[len(kept):]:
        cost = len(line) + 1
        if used + cost > limit:
            break
        kept.append(line)
        used += cost
    # The greedy fill respects the limit, so the result overruns it only when the
    # signature alone does — which is the one overrun this is willing to pay for.
    return "\n".join(kept).rstrip() + ELISION


def _signature_end(lines: list[str]) -> int:
    """Index of the line that closes the definition's signature, else 0.

    A signature can wrap over many lines, so it ends at the first line that closes
    every bracket it opened and then opens a body — `:` in Python, `{` in
    TypeScript, and `: ...` for the stub bodies that generated Python is made of.
    Decorators are carried along, since `@overload` is part of what identifies the
    definition that follows it.

    A chunk that is not a definition (`__module__` glue, a module-level assignment)
    has no such line; rather than scan a whole module for one, give up after a few
    lines and let the greedy line-wise fill do the work.
    """
    depth = 0
    for index, line in enumerate(lines[:_SIGNATURE_LINES]):
        code = line.split("#", 1)[0]
        depth += sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")
        if depth <= 0 and _BODY_OPENS.search(code):
            return index
    return 0
