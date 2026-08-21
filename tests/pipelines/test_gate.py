"""What a large file has to be for codoc to look at it.

The point of these tests is that the judgment is about SHAPE. A byte cap answered a
question about intent with a measurement of size, and the two come apart on exactly
the file that matters most: `test/altair` ships two modules from one generator, 1.20
MB and 1.60 MB, and the cap indexed the first and made the second — the Vega-Lite
schema surface — invisible.

The numbers below are measured, not chosen. Across the 826 Python and TypeScript
files in this repo and its corpora the largest median chunk any file has is 16,097
bytes; the blob shapes the cap was written for have medians of 105,792 and
1,248,897. The threshold lives in that gap, so these tests pin the gap rather than
the constant.
"""
from __future__ import annotations

from codoc.pipelines.indexing.gate import (
    HEARING_BYTES,
    ORDINARY_DEFINITION_BYTES,
    READ_CEILING_BYTES,
    holds_definitions,
    needs_hearing,
    too_large_to_read,
)


# ------------------------------------------------------------- what size decides

def test_an_ordinary_file_is_never_questioned():
    # Nothing is consulted below the threshold, so a small module that happens to be
    # one enormous dict is indexed exactly as it was before the gate existed.
    assert not too_large_to_read(74_202)      # codoc/store/db.py
    assert not needs_hearing(74_202)


def test_a_generated_module_gets_a_hearing_rather_than_a_drop():
    for size in (1_204_114, 1_600_448):       # altair channels.py, core.py
        assert not too_large_to_read(size)
    # The smaller of the two was under the old cap and the larger was over it, which
    # is the whole complaint: same generator, same shape, 400 KB apart.
    assert not needs_hearing(1_204_114)
    assert needs_hearing(1_600_448)


def test_past_the_read_ceiling_nothing_is_read_at_all():
    # A bound on memory and on what the describing pass can carry — not a claim
    # about whether somebody meant to write the file.
    assert too_large_to_read(READ_CEILING_BYTES + 1)
    assert not too_large_to_read(READ_CEILING_BYTES)


def test_the_ceiling_leaves_room_above_the_hearing():
    # A file can only be turned away on its shape if it is first allowed to be read.
    assert HEARING_BYTES < READ_CEILING_BYTES


# ------------------------------------------------------------ what shape decides

def test_a_module_of_ordinary_definitions_is_addressable():
    # altair core.py: 923 chunks, median 263 bytes.
    assert holds_definitions([263] * 923)


def test_one_enormous_data_literal_is_not():
    # A one-line data module parses to a single chunk the size of the file.
    assert not holds_definitions([1_248_897])


def test_a_handful_of_enormous_generated_functions_is_not():
    # The vendored-blob shape: four definitions of 105,792 bytes each. Named, so a
    # count-based test would admit it; not readable one at a time, so it is refused.
    assert not holds_definitions([105_792] * 4)


def test_a_parse_that_found_nothing_is_not_addressable():
    # Indexing it would be a no-op that reads as coverage, so the honest answer is
    # that there was nothing here for a feature to bind to.
    assert not holds_definitions([])


def test_one_outlier_does_not_disqualify_a_readable_file():
    # store/db.py carries a 55 KB chunk among 95. Real code has a long __init__ or a
    # big dispatch table without ceasing to be code, which is why the test is the
    # median and not the largest.
    assert holds_definitions([395] * 94 + [55_474])


def test_the_count_of_definitions_is_not_the_question():
    # Many pieces do not make a blob readable, and few do not make a module a blob.
    assert not holds_definitions([200_000] * 500)
    assert holds_definitions([400, 900, 1_500])


def test_the_threshold_sits_between_the_shapes_it_separates():
    # The gap is the argument; the constant is only allowed to live inside it.
    assert 16_097 < ORDINARY_DEFINITION_BYTES < 105_792
