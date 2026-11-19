"""Order keys: fractional indexing over a base-62 alphabet.

A key is read as a base-62 fraction: ``"V"`` is roughly ``0.5``, ``"AV"`` sits
slightly above ``"A"``. The alphabet is in ASCII order, so Python's ``<`` and
SQLite's ``ORDER BY`` agree without a collation. Keys never end in the zero
digit, so every value has exactly one spelling.

Moving one block touches exactly one key. Inserting between the same two
neighbours lengthens the key by about one character per 62 insertions; pure
appends use ``append_after`` and grow far slower.
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
    """A rank request that cannot be satisfied because the bounds are out of order."""


def valid(key) -> bool:
    """Whether ``key`` is a rank this module could have produced: a non-empty
    string of alphabet digits that does not end in the zero digit."""
    return isinstance(key, str) and bool(key) and all(c in _INDEX for c in key) and key[-1] != ALPHABET[0]


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
            # Room for a digit strictly between the two: the key ends here, and
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
    space each time, so a list built by appending one node at a time (how
    bootstrap and the reflect pass build one) would grow keys about six times faster than
    necessary. With no upper bound the cheap answer is available:
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
