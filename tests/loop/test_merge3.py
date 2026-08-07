"""The three-way merge that separates "did these edits overlap" from "who wins".

The unit under test is pure text. Rank arbitration lives in ``model.event.outranks``
and the policy that combines the two lives in ``loop_b._resolve_content``; these
tests pin only the textual question, because that is the one that has to be right
before any precedence rule means anything.
"""
from __future__ import annotations

from codoc.loop.merge3 import merge3
from codoc.model.event import ACTOR_HUMAN, ACTOR_LOOP, DEFAULT_AGENT_ACTOR, outranks

BASE = "first paragraph\n\nsecond paragraph\n\nthird paragraph"


def test_edits_on_different_lines_both_land():
    current = BASE.replace("third paragraph", "third, by the agent")
    incoming = BASE.replace("first paragraph", "first, by the author")

    m = merge3(BASE, current, incoming)

    assert not m.contended
    assert m.text == "first, by the author\n\nsecond paragraph\n\nthird, by the agent"


def test_taking_the_incoming_text_wholesale_would_lose_the_other_edit():
    """Anti-vacuity floor. The previous behaviour was all-or-nothing: apply the
    author's slice (losing the agent's paragraph) or refuse it (losing the
    author's). This asserts the merge is doing real work — that neither naive
    answer contains what the merged one does."""
    current = BASE.replace("third paragraph", "third, by the agent")
    incoming = BASE.replace("first paragraph", "first, by the author")

    m = merge3(BASE, current, incoming)

    assert "third, by the agent" not in incoming      # what "apply it" would lose
    assert "first, by the author" not in current      # what "refuse it" would lose
    assert "third, by the agent" in m.text and "first, by the author" in m.text


def test_edits_to_the_same_line_contend():
    m = merge3(BASE,
               BASE.replace("second paragraph", "second, by the agent"),
               BASE.replace("second paragraph", "second, by the author"))

    assert m.contended
    assert "second, by the author" in m.text   # resolved to incoming; the caller
    assert "second, by the agent" not in m.text  # decides whether that was earned


def test_the_same_edit_from_both_sides_is_agreement_not_conflict():
    """A lagging projection racing an echo of the author's own edit lands here.
    Reporting it as contended would ask someone to arbitrate between two
    identical paragraphs."""
    same = BASE.replace("second paragraph", "second, rewritten")

    m = merge3(BASE, same, same)

    assert not m.contended
    assert m.text == same


def test_writing_the_same_line_the_same_way_within_a_bigger_edit_is_not_contention():
    """The two sides edited a shared line AND separate ones, but wrote that shared
    line identically. Only the disagreement should count."""
    current = "alpha\n\nshared rewrite\n\ngamma, theirs"
    incoming = "alpha\n\nshared rewrite\n\ngamma"
    base = "alpha\n\nbeta\n\ngamma"

    m = merge3(base, current, incoming)

    assert not m.contended
    assert m.text == "alpha\n\nshared rewrite\n\ngamma, theirs"


def test_an_unchanged_side_yields_to_the_other():
    unchanged = merge3(BASE, BASE, "the author rewrote everything")
    assert unchanged.text == "the author rewrote everything" and not unchanged.contended

    idle = merge3(BASE, "the agent rewrote everything", BASE)
    assert idle.text == "the agent rewrote everything" and not idle.contended


def test_an_insertion_before_a_rewritten_region_is_adjacent_not_overlapping():
    base = "a\nb\nc"
    m = merge3(base, "a\nB\nc", "a0\na\nb\nc")

    assert not m.contended
    assert m.text == "a0\na\nB\nc"


def test_appends_from_both_sides_both_survive():
    """Two insertions at the same point do not overlap in content — the merge
    keeps both rather than picking one, so nothing is lost to an ordering
    neither party chose."""
    base = "one"
    m = merge3(base, "one\ntheirs", "one\nmine")

    assert m.text.count("theirs") == 1 and m.text.count("mine") == 1


def test_overlapping_clusters_resolve_as_one_region():
    """Two of the author's edits straddle one of the agent's. The whole span is a
    single contended region, so the author's version of it is emitted intact
    instead of being interleaved into text neither side wrote."""
    base = "l1\nl2\nl3\nl4\nl5"
    current = "l1\nAGENT-2\nAGENT-3\nAGENT-4\nl5"
    incoming = "l1\nAUTH-2\nl3\nAUTH-4\nl5"

    m = merge3(base, current, incoming)

    assert m.contended
    assert m.text == "l1\nAUTH-2\nl3\nAUTH-4\nl5"


def test_untouched_text_round_trips_byte_for_byte():
    """The merge must not quietly renormalize prose. splitlines() would eat the
    trailing newline and split on \\x0b — rewriting text nobody edited."""
    base = "keep \x0b this\ntrailing\n"
    m = merge3(base, base + "theirs\n", base)

    assert m.text.startswith("keep \x0b this\ntrailing\n")


# ── the invariants, fuzzed ───────────────────────────────────────────────────

def _edited(rng, base_lines: list[str], tag: str) -> list[str]:
    """A side's version of the base: random replaces, deletes and insertions.

    Every introduced line is uniquely tagged, so the invariants below can be
    read straight off the TEXT rather than off a model of what the edits were
    supposed to do — a model that would just restate the implementation.
    """
    out: list[str] = []
    for i, line in enumerate(base_lines):
        roll = rng.random()
        if roll < 0.15:
            out.append(f"{tag}-repl-{i}")
        elif roll < 0.25:
            pass  # deleted
        else:
            out.append(line)
        if rng.random() < 0.10:
            out.append(f"{tag}-ins-{i}")
    return out


def test_the_authors_text_always_survives_the_merge():
    """The load-bearing property, over 400 random edit pairs.

    Whatever the other side did, every line the incoming edit introduced appears
    in the result. Contended regions resolve to incoming and disjoint ones keep
    both, so there is no path on which the author's words are dropped — which is
    the entire reason this module exists. The result also never contains a line
    neither side wrote: a merge that invents text is as bad as one that loses it.
    """
    import random

    contended_seen = disjoint_seen = 0
    for seed in range(400):
        rng = random.Random(seed)
        base_lines = [f"b{i}" for i in range(rng.randint(1, 12))]
        base = "\n".join(base_lines)
        current = "\n".join(_edited(rng, base_lines, "them"))
        incoming = "\n".join(_edited(rng, base_lines, "me"))

        m = merge3(base, current, incoming)
        merged_lines = m.text.split("\n")
        introduced_c = set(current.split("\n")) - set(base_lines)
        introduced_i = set(incoming.split("\n")) - set(base_lines)

        assert introduced_i <= set(merged_lines), f"seed {seed}: lost the author's text"
        assert set(merged_lines) <= set(base_lines) | introduced_c | introduced_i, (
            f"seed {seed}: invented text neither side wrote"
        )
        if m.contended:
            contended_seen += 1
        else:
            disjoint_seen += 1
            assert introduced_c <= set(merged_lines), f"seed {seed}: lost the other side"

    # Anti-vacuity: the corpus must actually exercise both branches, or the
    # assertions above are being proved against a stream of trivial cases.
    assert contended_seen > 40 and disjoint_seen > 40, (contended_seen, disjoint_seen)


def test_disjoint_merges_do_not_depend_on_which_side_is_asked_first():
    """When nobody contends, the merge is a fact about the two edits rather than
    about their arrival order — so swapping the roles must not change the text.
    (Insertions at the very same point are the one exception: they do not
    overlap, but they have no canonical order either, so they are excluded.)"""
    import random

    checked = 0
    for seed in range(400):
        rng = random.Random(seed)
        base_lines = [f"b{i}" for i in range(rng.randint(1, 12))]
        base = "\n".join(base_lines)
        # Deletes and replaces only: no insertions, hence no same-point ties.
        a = "\n".join(x for i, x in enumerate(base_lines)
                      if not (rng.random() < 0.2)) or base_lines[0]
        b = "\n".join(f"me-{i}" if rng.random() < 0.2 else x
                      for i, x in enumerate(base_lines))
        forward, backward = merge3(base, a, b), merge3(base, b, a)
        if forward.contended or backward.contended:
            continue
        assert forward.text == backward.text, f"seed {seed}"
        checked += 1

    assert checked > 40, checked


# ── the precedence rule the caller layers on top ─────────────────────────────

def test_a_person_outranks_agents_the_loop_and_the_unknown():
    assert outranks(ACTOR_HUMAN, ACTOR_LOOP)
    assert outranks(ACTOR_HUMAN, DEFAULT_AGENT_ACTOR)
    assert outranks(ACTOR_HUMAN, "")


def test_nothing_outranks_a_person_and_non_humans_tie():
    assert not outranks(DEFAULT_AGENT_ACTOR, ACTOR_HUMAN)
    assert not outranks(ACTOR_LOOP, ACTOR_HUMAN)
    assert not outranks(ACTOR_HUMAN, ACTOR_HUMAN)      # peers never overwrite peers
    assert not outranks(DEFAULT_AGENT_ACTOR, ACTOR_LOOP)
    assert not outranks(ACTOR_LOOP, DEFAULT_AGENT_ACTOR)
