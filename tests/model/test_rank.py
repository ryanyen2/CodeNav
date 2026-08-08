"""Sibling order keys — the ordering the tree never had.

The invariant everything else rests on: string comparison of two keys is their
order, in Python and in SQLite, with no collation involved.
"""
from __future__ import annotations

import pytest

from codoc.model.rank import (
    ALPHABET, BASE, RankError, append_after, between, ordinal_keys,
)


def test_the_alphabet_is_in_ascii_order():
    """Load-bearing. Every persisted key is compared as a plain string by
    SQLite's ORDER BY; an alphabet out of ASCII order would sort differently
    there than in Python, and the two would disagree about the tree."""
    assert list(ALPHABET) == sorted(ALPHABET)
    assert len(set(ALPHABET)) == BASE


class TestBetween:
    def test_lands_strictly_between_two_keys(self):
        a, b = 'A', 'B'
        mid = between(a, b)
        assert a < mid < b

    def test_unbounded_below_lands_before_everything_given(self):
        assert between('', 'A') < 'A'

    def test_unbounded_above_lands_after_everything_given(self):
        assert between('A', '') > 'A'

    def test_unbounded_both_ways_is_a_valid_first_key(self):
        k = between()
        assert '' < k

    def test_descends_when_neighbours_are_adjacent_digits(self):
        # No single digit fits between 'A' and 'B', so the key must lengthen.
        mid = between('A', 'B')
        assert 'A' < mid < 'B'
        assert len(mid) > 1

    def test_descends_through_a_shared_prefix(self):
        mid = between('AV', 'AW')
        assert 'AV' < mid < 'AW'

    def test_handles_a_bound_that_is_a_prefix_of_the_other(self):
        mid = between('A', 'AB')
        assert 'A' < mid < 'AB'

    def test_never_ends_in_the_zero_digit(self):
        # One spelling per position: a trailing zero would make two distinct
        # strings mean the same place, and equality would stop meaning equality.
        for a, b in [('', ''), ('', 'A'), ('A', ''), ('A', 'B'), ('AV', 'AW'), ('A', 'AB')]:
            assert not between(a, b).endswith(ALPHABET[0]), (a, b)

    def test_refuses_bounds_that_are_out_of_order(self):
        with pytest.raises(RankError):
            between('B', 'A')
        with pytest.raises(RankError):
            between('A', 'A')

    def test_survives_repeated_insertion_at_the_same_spot(self):
        # The adversarial case: always insert just after the same node. Keys
        # lengthen, but order must never break.
        lo, hi = 'A', 'B'
        seen = [lo, hi]
        for _ in range(200):
            mid = between(lo, hi)
            assert lo < mid < hi
            seen.append(mid)
            hi = mid
        assert len(set(seen)) == len(seen)          # every key distinct
        assert sorted(seen) == sorted(set(seen))


class TestAppendAfter:
    def test_produces_an_increasing_sequence(self):
        keys = []
        k = ''
        for _ in range(300):
            k = append_after(k)
            keys.append(k)
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    def test_stays_short_where_between_would_not(self):
        # Why append_after exists. Bootstrap and Loop A build a sibling list by
        # appending one node at a time; between(last, '') halves the remaining
        # space every time and lengthens keys far faster than stepping a digit.
        stepped = ''
        halved = ''
        for _ in range(120):
            stepped = append_after(stepped)
            halved = between(halved, '')
        assert len(stepped) < len(halved)

    def test_descends_when_the_last_digit_is_exhausted(self):
        top = ALPHABET[-1]
        nxt = append_after(top)
        assert nxt > top
        assert nxt.startswith(top)

    def test_the_first_key_leaves_room_on_both_sides(self):
        first = append_after('')
        assert between('', first) < first < between(first, '')


class TestOrdinalKeys:
    @pytest.mark.parametrize('n', [1, 2, 5, 63, 100, 1000])
    def test_returns_ascending_distinct_keys(self, n):
        keys = ordinal_keys(n)
        assert len(keys) == n
        assert keys == sorted(keys)
        assert len(set(keys)) == n

    def test_returns_nothing_for_an_empty_list(self):
        assert ordinal_keys(0) == []

    def test_leaves_room_to_insert_between_any_pair(self):
        keys = ordinal_keys(50)
        for a, b in zip(keys, keys[1:]):
            mid = between(a, b)
            assert a < mid < b

    def test_leaves_room_at_both_ends(self):
        keys = ordinal_keys(10)
        assert between('', keys[0]) < keys[0]
        assert between(keys[-1], '') > keys[-1]

    def test_stays_short_for_a_realistic_sibling_list(self):
        # Trees hold tens of children per parent, not thousands. Backfill must
        # not hand them long keys for no reason.
        assert all(len(k) <= 2 for k in ordinal_keys(50))
