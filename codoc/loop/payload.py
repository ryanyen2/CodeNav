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
  when the budget binds, members are compressed first. A settings section is the
  exception and gets the top-level share however deeply its name nests, because
  `[tool.pytest.ini_options]` is a member of a document rather than of a
  definition: nothing above it states its values, and its values are the whole
  reason it is in the prompt.
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

Conceding is the floor, not the goal. A pass that has spent down to 60 characters of
each method is still being asked to name a feature set it can barely see, so
`passes` splits such a set across SEVERAL calls instead — and the criterion is the
budget itself, which is why there is no threshold to pick. A set that fits at full
allowance is one pass and one call, exactly as before; a set that does not is one
the model was going to be shown a fraction of. Measured over the 813 files in this
repo and its corpora, that is 810 files unchanged and three split — `test/altair`'s
`core.py` (923 definitions, 4 passes), `channels.py` (786, 4) and `api.py` (219, 2)
— and after the split not one pass has to concede at all.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from codoc import settings_files

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

    **A settings section is never a member**, however dotted it is. The concession
    it would buy is justified for a method — the class states what the method is
    for, so a shortened body still lands the reader in the right place — and a
    settings section has no such parent: `[tool.pytest.ini_options]` names a path
    through a document, and the keys under it are the decision itself. Compressing
    those first would spend the budget on exactly the lines the tree cannot
    otherwise say.

    The TypeScript adapter addresses only top-level declarations today, so a `.ts`
    file has no members and the ladder's first concession simply never fires there
    — it falls straight to shrinking the top-level share, which is correct.
    """
    if settings_files.detect_format(symbol_path.rsplit("::", 1)[0]):
        return False
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


def passes(
    sources: Mapping[str, str],
    *,
    budget: int = BUDGET,
    per_chunk: int = PER_CHUNK,
) -> list[dict[str, str]]:
    """What each call gets to see, when one call cannot hold the whole set.

    Returns one mapping per pass, in order. **One pass whenever the set fits at
    full allowance**, which is the criterion and also the reason there is no
    threshold here to argue about: the point at which a set stops fitting is the
    point at which the model stops being shown the definitions it is being asked to
    name, and splitting is strictly better than conceding past it — each group then
    fits, so each definition is shown whole again.

    Splitting is by TOP-LEVEL OWNER, so a class and its methods are never separated.
    The prompt binds a method to the feature its class owns; a pass that saw half a
    class would be asked to name a feature over evidence somebody else is holding.
    An owner too large to share a pass with anyone gets one to itself and the budget
    concedes within it, which is the honest answer for a 200-method class: it is one
    feature, and no split makes it two.

    Owners are packed in the order *sources* presents them, so a caller that hands
    over its symbols in the order they appear in the file gets passes that are
    contiguous regions of it — which is how a person reads one.
    """
    if not sources:
        return []
    full = {path: head(source, per_chunk) for path, source in sources.items()}
    if sum(map(len, full.values())) <= budget:
        return [full]
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for block in _owner_blocks(sources):
        cost = sum(len(full[path]) for path in block)
        if current and used + cost > budget:
            groups.append(current)
            current, used = [], 0
        current.extend(block)
        used += cost
    if current:
        groups.append(current)
    return [
        shown_sources({path: sources[path] for path in group},
                      budget=budget, per_chunk=per_chunk)
        for group in groups
    ]


def _owner_blocks(sources: Mapping[str, str]) -> list[list[str]]:
    """*sources* keys grouped by the top-level name that owns them, in order."""
    blocks: dict[str, list[str]] = {}
    for path in sources:
        blocks.setdefault(_owner(path), []).append(path)
    return list(blocks.values())


def _owner(symbol_path: str) -> str:
    """The top-level definition a symbol belongs to — itself, if it is one."""
    module, separator, name = symbol_path.rpartition("::")
    top = name.split(".", 1)[0]
    return f"{module}{separator}{top}" if separator else top


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
