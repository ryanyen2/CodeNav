"""Compute smallest unambiguous string prefixes for display.

Instead of hardcoded [:8] / [:30] truncations, every table that shows IDs
should use these helpers so the output is always copy-paste-safe.
"""

from __future__ import annotations


def smallest_unambiguous_prefix(
    value: str,
    all_values: list[str],
    min_len: int = 4,
) -> str:
    """Return the shortest prefix of *value* (≥ min_len) that uniquely
    identifies it among *all_values*.  Falls back to the full value."""
    for length in range(min_len, len(value) + 1):
        prefix = value[:length]
        count = sum(1 for v in all_values if v.startswith(prefix))
        if count == 1:
            return prefix
    return value


def unambiguous_uuid_prefix(uuid: str, all_uuids: list[str], min_len: int = 6) -> str:
    """Shortest hex prefix of *uuid* that is unambiguous within *all_uuids*.

    Operates on dashes-stripped hex to avoid returning a prefix that ends
    mid-dash (e.g. '4b5b-' looks ugly).
    """
    clean = uuid.replace("-", "")
    all_clean = [u.replace("-", "") for u in all_uuids]
    return smallest_unambiguous_prefix(clean, all_clean, min_len)


def unambiguous_hlc_prefix(hlc: str, all_hlcs: list[str], min_len: int = 12) -> str:
    """Shortest prefix of *hlc* that is unambiguous within *all_hlcs*."""
    return smallest_unambiguous_prefix(hlc, all_hlcs, min_len)
