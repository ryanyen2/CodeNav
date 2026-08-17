"""`.codoc/ask.json` — the ephemeral walkthrough overlay.

Covers the two things the file has to get right on its own: the numbering (which
is computed here precisely so an LLM cannot emit a broken sequence) and the
read/write/expiry contract.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from codoc.loop.ask import (
    ASK_TTL_SECONDS,
    MAX_NOTE_CHARS,
    MAX_STEPS,
    AskStep,
    ask_path,
    build_walkthrough,
    clear_walkthrough,
    label_steps,
    read_walkthrough,
    write_walkthrough,
)


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _steps(*specs: tuple[str, str]) -> list[AskStep]:
    return [AskStep(feature_id=fid, group=group) for fid, group in specs]


# ─── numbering ────────────────────────────────────────────────────────────────

def test_ungrouped_steps_are_plain_ordinals():
    got = label_steps([AskStep(feature_id=f"f-{i}") for i in range(3)])
    assert [s.label for s in got] == ["1", "2", "3"]


def test_grouped_steps_number_by_group_and_letter_within_it():
    got = label_steps(_steps(
        ("f-1", "parsing"), ("f-2", "parsing"),
        ("f-3", "converting"), ("f-4", "converting"), ("f-5", "converting"),
    ))
    assert [s.label for s in got] == ["1a", "1b", "2a", "2b", "2c"]


def test_a_group_that_recurs_starts_a_new_number():
    # A procedure that returns to a stage VISITS it again — it does not rejoin
    # the first visit, so the reader gets 3a, not another 1x.
    got = label_steps(_steps(("f-1", "strip"), ("f-2", "convert"), ("f-3", "strip")))
    assert [s.label for s in got] == ["1a", "2a", "3a"]


def test_a_single_ungrouped_step_among_grouped_ones_still_gets_a_number():
    got = label_steps(_steps(("f-1", "parsing"), ("f-2", ""), ("f-3", "parsing")))
    assert [s.label for s in got] == ["1a", "2a", "3a"]


# ─── build ────────────────────────────────────────────────────────────────────

def test_build_clips_a_long_note_to_one_line():
    long = "word " * 200
    walk = build_walkthrough("q", "a", [AskStep(feature_id="f-1", note=long)])
    note = walk.steps[0].note
    assert len(note) <= MAX_NOTE_CHARS + 1  # +1 for the ellipsis
    assert note.endswith("…")
    assert "\n" not in note


def test_build_caps_the_step_count():
    walk = build_walkthrough("q", "", [AskStep(feature_id=f"f-{i}") for i in range(40)])
    assert len(walk.steps) == MAX_STEPS


def test_build_keeps_a_quote_verbatim():
    # The quote is matched character-for-character against the description, so
    # collapsing its whitespace the way notes are collapsed would break the match.
    quote = "furniture  is   stripped"
    walk = build_walkthrough("q", "", [AskStep(feature_id="f-1", quote=quote)])
    assert walk.steps[0].quote == quote


def test_build_stamps_an_id_and_a_time():
    walk = build_walkthrough("q", "", [AskStep(feature_id="f-1")])
    assert walk.id.startswith("ask-")
    assert walk.at


# ─── file contract ────────────────────────────────────────────────────────────

def test_write_then_read_round_trips(codoc_dir):
    walk = build_walkthrough("why?", "because.", [
        AskStep(feature_id="f-1", note="here", group="g", quote="q"),
    ])
    write_walkthrough(codoc_dir, walk)
    got = read_walkthrough(codoc_dir)
    assert got is not None
    assert got["question"] == "why?"
    assert got["answer"] == "because."
    assert got["steps"][0]["label"] == "1a"
    assert got["steps"][0]["feature_id"] == "f-1"


def test_read_is_none_when_absent(codoc_dir):
    assert read_walkthrough(codoc_dir) is None


def test_read_is_none_when_there_are_no_steps(codoc_dir):
    # An overlay with nothing to point at is not an overlay.
    ask_path(codoc_dir).write_text(json.dumps({"version": 1, "steps": []}))
    assert read_walkthrough(codoc_dir) is None


def test_read_is_none_when_corrupt(codoc_dir):
    ask_path(codoc_dir).write_text("{not json")
    assert read_walkthrough(codoc_dir) is None


def test_read_is_none_once_expired(codoc_dir):
    walk = build_walkthrough("q", "", [AskStep(feature_id="f-1")])
    write_walkthrough(codoc_dir, walk)
    assert read_walkthrough(codoc_dir) is not None
    # Yesterday's question must not greet a new session.
    stale = time.time() + ASK_TTL_SECONDS + 60
    assert read_walkthrough(codoc_dir, now=stale) is None


def test_expiry_is_keyed_on_mtime_not_the_recorded_time(codoc_dir):
    walk = build_walkthrough("q", "", [AskStep(feature_id="f-1")])
    write_walkthrough(codoc_dir, walk)
    old = time.time() - (ASK_TTL_SECONDS + 60)
    os.utime(ask_path(codoc_dir), (old, old))
    assert read_walkthrough(codoc_dir) is None


def test_a_second_write_replaces_the_first(codoc_dir):
    write_walkthrough(codoc_dir, build_walkthrough("first", "", [AskStep(feature_id="f-1")]))
    write_walkthrough(codoc_dir, build_walkthrough("second", "", [AskStep(feature_id="f-2")]))
    got = read_walkthrough(codoc_dir)
    assert got["question"] == "second"
    assert len(got["steps"]) == 1


def test_clear_is_idempotent(codoc_dir):
    write_walkthrough(codoc_dir, build_walkthrough("q", "", [AskStep(feature_id="f-1")]))
    assert clear_walkthrough(codoc_dir) is True
    assert clear_walkthrough(codoc_dir) is False
    assert read_walkthrough(codoc_dir) is None
