"""The prose gate, rule by rule.

Two halves. The first pins each rule against the pair it exists to separate: the
sample it must fire on, and the good sample nearest to it that it must stay quiet
on. A critic is only worth running if both hold, so a test that only shows the
defect proves half of nothing.

The second half is the gate's behaviour around a repair, where the risk is not a
missed defect but a lost write: a rewrite that dropped a node, a rewrite that got
worse, a rerun that raised. Every one of those has to end with prose in the tree.
"""
from __future__ import annotations

import pytest

from codoc.loop import prose
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store

# The worked example from prompts/style.txt, which is the one sample in the repo
# the author has explicitly blessed. If the gate ever fires on this, the gate is
# wrong and not the sample.
BLESSED_TITLE = "Page furniture removal"
BLESSED = (
    "Drops the running header and the page number, so the rules that follow are "
    "reading the document rather than the paper it was printed on. Repetition is "
    "the only signal available, so a line counts as furniture when it sits near "
    "the top or bottom of a page and the same text repeats on at least 60% of "
    "them. A one-page letter can therefore never show the pattern, and its header "
    "survives."
)
BLESSED_NAMES = ("extract.py _near_edge", "extract.py REPEAT_SHARE")


def codes(defects) -> set[str]:
    return {d.code for d in defects}


def check(title=None, description=None, **kw):
    kw.setdefault("names", BLESSED_NAMES)
    return prose.check(title, description, **kw)


# --------------------------------------------------------------------------
# the sample the whole module is calibrated against
# --------------------------------------------------------------------------

def test_the_blessed_example_is_clean():
    assert check(BLESSED_TITLE, BLESSED, files=1, has_children=False) == []


def test_the_style_guides_own_bad_example_is_not():
    bad = (
        "Robustly handles the header edge case. Uses REPEAT_SHARE. This is "
        "important for correctness, see `_near_edge()`."
    )
    found = codes(check("Header handling", bad, files=1, has_children=False))
    assert "machine-register" in found
    assert "demonstrative-opening" in found
    assert "clipped-sentences" in found


# --------------------------------------------------------------------------
# one pair per rule: what it catches, and the good prose next to it
# --------------------------------------------------------------------------

def test_opening_on_a_symbol_is_caught_and_citing_one_later_is_not():
    opens = "`_near_edge` decides whether a line is near the top of a page."
    assert "opens-on-a-mechanism" in codes(check("Page furniture", opens))
    later = (
        "Repetition is the only signal available, so a line counts as furniture "
        "when `_near_edge` puts it at the top or bottom of a page."
    )
    assert "opens-on-a-mechanism" not in codes(check("Page furniture", later))


def test_a_filename_is_a_place_and_not_a_mechanism():
    # `tree.codoc` is the artifact the reader opens, so naming it first orients
    # them; `store.db.Store` first is the failure the rule is about.
    place = "tree.codoc edits are applied first, then the code is reflected."
    assert "opens-on-a-mechanism" not in codes(check("Sync", place))
    path = "store.db.Store opens the file and the loop writes through it."
    assert "opens-on-a-mechanism" in codes(check("Sync", path))


def test_restating_the_title_is_caught_through_a_change_of_word_class():
    # "Renders" against "rendering" is the commonest way a title is restated, so
    # exact term matching would miss precisely the case worth catching.
    assert "restates-the-title" in codes(check(
        "Tree rendering",
        "Tree rendering renders each feature of the tree into the rendered tree.",
    ))
    assert "restates-the-title" not in codes(check(
        "Tree rendering",
        "Only the daemon writes the export, because two writers would interleave "
        "halves of one feature and the reader would see a torn node.",
    ))


def test_one_sentence_with_no_rule_in_it_is_caught():
    assert "no-rule-given" in codes(check(
        "Tree rendering", "Renders the tree to tree.codoc for the reader."))
    # An organizing node with no code under it is honestly one sentence: the rule
    # that shapes it lives in its children, so nothing is missing.
    assert "no-rule-given" not in codes(check(
        "Reading the tree", "Everything a reader can do without changing anything.",
        names=()))


def test_prose_recoverable_from_the_identifiers_is_caught():
    assert "nothing-beyond-the-names" in codes(check(
        "Near edge",
        "Handles the near edge functionality, providing the repeat share behaviour "
        "required by the extract module as appropriate.",
        names=("extract.py _near_edge", "extract.py REPEAT_SHARE"),
    ))
    assert "nothing-beyond-the-names" not in codes(check(BLESSED_TITLE, BLESSED))


def test_banned_register_and_the_words_deliberately_left_alone():
    assert "machine-register" in codes(check(None, "Ensures a robust, seamless sync."))
    # `key` and `clean` are nouns and verbs this domain uses constantly. Banning
    # them outright would fire several times per tree on correct prose.
    assert codes(check(
        None,
        "The command's key is the feature id, so a replayed command is a no-op "
        "and the daemon can clean the queue without asking what it held.",
        names=("edits.py drain",),
    )) == set()


def test_decoration_is_caught_and_a_full_stop_is_not():
    assert "decorated" in codes(check(None, "The loop applies edits — then reflects."))
    assert "decorated" in codes(check(
        None, "The order matters here: edits land before the reflection."))
    assert "decorated" not in codes(check(
        None, "The loop applies edits and then reflects the code."))


def test_a_quoted_question_is_a_statement():
    # The rule is about asking the reader something, not about the word "question".
    quoted = (
        "The question an amend gate has to answer is \"did this keep what was "
        "there?\", so the gate measures the share of the original that survives "
        "rather than whether the text changed."
    )
    assert "rhetorical-question" not in codes(check("Amend gate", quoted))
    assert "rhetorical-question" in codes(check(
        "Amend gate",
        "Does this edit mint a directive? The gate decides by asking whether the "
        "change implies code, which is why a retitling never queues one."))


def test_a_regex_in_backticks_cannot_ask_a_question():
    # Masking has to cover double backticks too, or the contents leak out as prose
    # and the sample is reported for punctuation it does not contain.
    sample = (
        "The pitch is the first sentence, split on a ``[.!?]`` boundary so a "
        "symbol path's full stops cannot end it early."
    )
    assert codes(check("Pitch", sample)) == set()


def test_a_list_of_steps_is_not_clipped_prose():
    listed = (
        "Resolution runs in order, because an earlier rule's answer is always the "
        "more specific one.\n"
        "- strip the self prefix\n"
        "- look the symbol path up exactly\n"
        "- fall back to the file's own module\n"
    )
    assert "clipped-sentences" not in codes(check("Resolution order", listed))
    stacked = "Strips the prefix. Looks up the path. Falls back to the module."
    assert "clipped-sentences" in codes(check("Resolution order", stacked))


def test_a_sentence_that_starts_with_a_citation_is_still_its_own_sentence():
    # Without this, the citation joins the sentence before it and the pair is
    # reported as one overlong sentence.
    two = (
        "The epoch opens when a turn starts and closes when it ends. "
        "`SessionEnd` is the backstop for an exit that skips the close, so a "
        "session killed mid-turn still leaves the epoch shut."
    )
    assert "overlong-sentence" not in codes(check("Epoch", two))
    assert len(prose.sentences(two)) == 2


def test_a_broad_node_needs_one_sentence_a_newcomer_can_read():
    every_sentence_cites = (
        "`loop_a` reads the code and corrects `tree.codoc`. `loop_b` reads "
        "`tree.codoc` and queues `realize.md`. Both write through `apply_op`."
    )
    assert "altitude-too-low" in codes(check(
        "The two loops", every_sentence_cites, files=4, has_children=True))
    opens_plainly = (
        "Codoc keeps two directions of change in step. One reads the code and "
        "corrects what the tree says about it, while the other reads the tree and "
        "asks a session to change the code, and both write through `apply_op` so "
        "a concurrent pass cannot interleave two halves of one edit."
    )
    assert "altitude-too-low" not in codes(check(
        "The two loops", opens_plainly, files=4, has_children=True))


def test_a_leaf_written_abstractly_is_not_reported_for_its_characters():
    # The direction that was dropped: good leaf prose often names no symbol and no
    # number, because the BINDINGS already tie the node to its code.
    abstract = (
        "Turns a comment on a sentence into a request an agent can act on, "
        "scoping it to the code that sentence cites so a note about one paragraph "
        "cannot rewrite the whole feature."
    )
    assert codes(check("Comments as directives", abstract,
                       files=1, has_children=False)) == set()


def test_title_rules():
    assert "title-is-an-identifier" in codes(check("codoc_realize", None))
    assert "title-is-a-sentence" in codes(check(
        "The daemon writes the export so that the reader always sees one whole "
        "feature at a time", None))
    assert "title-says-nothing" in codes(check("Helpers", None))
    assert codes(check(BLESSED_TITLE, None)) == set()


# --------------------------------------------------------------------------
# non-Latin prose: the lexical rules do not apply, the structural ones do
# --------------------------------------------------------------------------

def test_chinese_prose_is_not_measured_with_an_english_wordlist():
    zh = "下面的规则面对的是文档本身" \
         "，而不是打印它的纸张。"
    assert prose.check(None, zh, names=("extract.py _near_edge",),
                       doc_language="zh-Hans") == []


# --------------------------------------------------------------------------
# severity: a repair must not be able to win by deleting content
# --------------------------------------------------------------------------

def test_deleting_the_content_costs_more_than_the_dash_it_fixed():
    dashed = prose.check(
        BLESSED_TITLE, BLESSED.replace("so the rules", "— so the rules"),
        names=BLESSED_NAMES)
    gutted = prose.check(
        BLESSED_TITLE, "Handles page furniture removal.", names=BLESSED_NAMES)
    assert prose.severity(gutted) > prose.severity(dashed)


# --------------------------------------------------------------------------
# the gate around a repair
# --------------------------------------------------------------------------

def _op(title, description, bindings=(("extract.py", "_near_edge"),)):
    return NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                  bindings=[tuple(b) for b in bindings])


def test_a_clean_answer_is_returned_without_a_rerun():
    ops = [_op(BLESSED_TITLE, BLESSED)]
    calls = []

    def rerun(text):
        calls.append(text)
        return ops

    kept, findings = prose.gate(ops, rerun=rerun)
    assert kept is ops and findings == {} and calls == []


def test_a_repair_that_reads_better_is_kept():
    ops = [_op("Header handling", "Robustly handles the header edge case.")]
    fixed = [_op(BLESSED_TITLE, BLESSED)]
    kept, findings = prose.gate(ops, rerun=lambda text: fixed)
    assert kept is fixed and findings == {}


def test_the_critique_names_the_node_and_quotes_the_author():
    ops = [_op("Header handling", "Robustly handles the header edge case.")]
    text = prose.critique(ops, prose.review_ops(ops))
    assert "Header handling" in text
    assert "robustly" in text.lower()
    assert "SAME ops" in text


def test_a_repair_that_dropped_a_node_is_discarded():
    ops = [_op("Header handling", "Robustly handles it."), _op("Pages", "Handles pages.")]
    kept, findings = prose.gate(ops, rerun=lambda text: [_op(BLESSED_TITLE, BLESSED)])
    assert kept is ops and findings


def test_a_repair_that_re_attributed_code_is_discarded():
    ops = [_op("Header handling", "Robustly handles it.")]
    moved = [_op(BLESSED_TITLE, BLESSED, bindings=(("other.py", "thing"),))]
    kept, _ = prose.gate(ops, rerun=lambda text: moved)
    assert kept is ops


def test_a_repair_that_reads_worse_is_discarded():
    ops = [_op("Header handling", "Robustly handles the header edge case.")]
    worse = [_op("Helpers", "Simply ensures a robust, seamless header edge case.")]
    kept, _ = prose.gate(ops, rerun=lambda text: worse)
    assert kept is ops


def test_a_rerun_that_raises_keeps_the_first_draft():
    ops = [_op("Header handling", "Robustly handles the header edge case.")]

    def rerun(text):
        raise RuntimeError("the model timed out")

    kept, findings = prose.gate(ops, rerun=rerun)
    assert kept is ops and findings


def test_with_no_rerun_the_defects_are_reported_and_the_ops_stand():
    ops = [_op("Header handling", "Robustly handles the header edge case.")]
    kept, findings = prose.gate(ops)
    assert kept is ops and 0 in findings


def test_an_op_that_writes_no_prose_is_not_checked():
    detach = NodeOp(kind=NodeOpKind.DETACH, feature_id="f-1",
                    bindings=[("extract.py", "_gone")])
    assert prose.review_ops([detach]) == {}


# --------------------------------------------------------------------------
# the rate, as a number
# --------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "codoc.db")
    s.open()
    yield s
    s.close()


def test_the_rate_accumulates_across_passes(store):
    assert prose.defect_rate(store)["rate"] is None
    prose.record(store, checked=1)
    prose.record(store, checked=1, defects=prose.check(
        None, "Ensures a robust sync.", names=("a.py b",)))
    stats = prose.defect_rate(store)
    assert stats["checked"] == 2 and stats["defective"] == 1
    assert stats["rate"] == pytest.approx(0.5)
    assert "machine-register" in dict(stats["top"])
    assert "1/2" in prose.render_rate(stats)


def test_nothing_checked_is_said_plainly(store):
    assert "nothing checked" in prose.render_rate(prose.defect_rate(store))


def test_a_broken_statistic_never_sinks_the_write(store):
    store.set_meta(prose.STATS_KEY, "{not json")
    prose.record(store, checked=1)
    assert prose.defect_rate(store)["checked"] == 1
