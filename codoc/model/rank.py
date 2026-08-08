"""Sibling order keys — fractional indexing over a base-62 alphabet.

Siblings used to be ordered by ``created_at``, which meant the tree had no
representation of order at all: a reorder emitted a ``move`` whose ``parent_id``
was unchanged, ``apply_op`` wrote the parent it already had, and the next render
put the node back where it started. The gesture animated and then silently
reverted.

**Why a fractional key and not an integer column.** The obvious fix — a dense
``ord`` per sibling, renumbered on every reorder — writes every sibling row to
move one node. That is wrong here specifically: Loop B's base check
(``loop_b._resolve_content``) and the writer lineage (``feature_writers``) treat
a write as evidence that somebody touched a feature, so renumbering would mark
every sibling as freshly written by whoever dragged one node, and the author's
next edit to any of them would read as a conflict with a stranger. Moving one
node must touch exactly one row, which is what a fractional key gives.

A key is read as a base-62 fraction: ``"V"`` is roughly ``0.5``, ``"AV"`` sits
just above ``"A"``. The alphabet is in ASCII order, so Python's ``<`` and
SQLite's ``ORDER BY`` agree without a collation.

Keys never end in the zero digit, so every value has exactly one spelling and
equal keys mean equal positions rather than an encoding accident.

**Growth.** Repeatedly inserting between the same two neighbours lengthens the
key by about one character per 62 insertions, and pure appends by one per 31.
That is inherent to fractional indexing and bounded in practice: growth is per
*sibling list*, and a feature tree's parents hold tens of children, not
thousands. :func:`ordinal_keys` spaces a whole list evenly with headroom, which
is what the backfill and any bulk assignment use.
"""
from __future__ import annotations

# ASCII-ordered so string comparison IS numeric comparison, in Python and SQLite
# alike. Do not reorder: every persisted key is interpreted against these indices.
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_INDEX = {c: i for i, c in enumerate(ALPHABET)}

#: The key handed to the very first child of a parent. Mid-alphabet on purpose,
#: so the first prepend and the first append are both cheap.
START = ALPHABET[BASE // 2]


class RankError(ValueError):
    """A rank request that cannot be satisfied — the bounds are out of order."""


def _digit(key: str, i: int, default: int) -> int:
    return _INDEX[key[i]] if i < len(key) else default


def between(a: str = "", b: str = "") -> str:
    """A key strictly between ``a`` and ``b``.

    ``""`` means unbounded: ``between("", b)`` lands before everything, and
    ``between(a, "")`` after everything.
    """
    if a and b and a >= b:
        raise RankError(f"between() needs a < b, got {a!r} and {b!r}")

    out: list[str] = []
    i = 0
    bounded = bool(b)
    while True:
        lo = _digit(a, i, 0)
        hi = _digit(b, i, BASE) if bounded else BASE
        if hi - lo > 1:
            # Room for a digit strictly between the two — the key ends here, and
            # the midpoint is never the zero digit because hi > lo + 1 >= 1.
            out.append(ALPHABET[(lo + hi) // 2])
            return "".join(out)
        # No room at this position: keep the lower bound's digit and descend. Once
        # the upper bound is only one digit above, every deeper digit is already
        # below it, so the upper bound stops constraining us.
        out.append(ALPHABET[lo])
        if hi != lo:
            bounded = False
        i += 1


def append_after(last: str = "") -> str:
    """The next key after ``last``, where nothing sits above it.

    Distinct from ``between(last, "")`` on purpose. That halves the remaining
    space each time, so a list built by appending one node at a time — which is
    exactly how bootstrap and Loop A build one — would grow keys about six times
    faster than necessary. With no upper bound the cheap answer is available:
    step the final digit by one.
    """
    if not last:
        return START
    tail = _INDEX[last[-1]]
    if tail + 1 < BASE:
        return last[:-1] + ALPHABET[tail + 1]
    # The last digit is already the maximum, so no same-length key follows it;
    # descend a level instead. `last + START` sorts immediately after `last`.
    return last + START


def ordinal_keys(count: int) -> list[str]:
    """``count`` ascending, evenly spaced keys — for backfill and bulk assignment.

    Spacing leaves roughly eight free slots between neighbours so the first few
    reorders after a backfill do not lengthen anything.
    """
    if count <= 0:
        return []
    width = 1
    while BASE ** width < (count + 1) * 8:
        width += 1
    stride = (BASE ** width) // (count + 1)
    return [_encode(stride * (i + 1), width) for i in range(count)]


def _encode(value: int, width: int) -> str:
    """``value`` in base-62, zero-padded to ``width``.

    Fixed width is what makes padded keys compare correctly: with a shared
    length, digit-wise comparison is numeric comparison. The trailing zeros this
    can produce are harmless — the no-trailing-zero rule exists so that keys
    generated by :func:`between` have one spelling, and these are generated as a
    block that is internally consistent.
    """
    out: list[str] = []
    for _ in range(width):
        value, rem = divmod(value, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))
