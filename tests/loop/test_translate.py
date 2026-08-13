"""Migrating an existing tree into another authoring language.

This is the one operation that rewrites every description at once, so the tests are
mostly about what it REFUSES. Each refusal corresponds to something that would break
silently: a dropped citation is a dead binding reference, a dropped bold span is a
lost `Focus:` directive line, a dropped link is a `Consult:` line the realizing agent
stops reading, and a colliding title turns two distinct features into one as far as
the dedup pass is concerned.

The LLM is injected throughout (``propose=``), so everything here is deterministic.
"""
from __future__ import annotations

import pytest

from codoc.doclang import resolve
from codoc.loop.translate import (
    TRANSLATE_SOURCE, check_translation, translate_tree,
)
from codoc.model.event import ACTOR_HUMAN, ACTOR_LOOP
from codoc.model.feature import Feature
from codoc.store.db import open_store

ZH = resolve("zh-Hans")

CITED = ("Diffs two index snapshots via "
         "[compute_changeset()](codoc:codoc/loop/diff.py#compute_changeset) so the "
         "loop never **re-parses** a file it has seen. See [the plan](https://x.test/p).")
CITED_ZH = ("通过 [compute_changeset()](codoc:codoc/loop/diff.py#compute_changeset) "
            "比较两次索引快照，使循环不会**重新解析**已经看过的文件。"
            "参见 [方案](https://x.test/p)。")


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir(parents=True)
    return d


# ── the validator ───────────────────────────────────────────────────────────

def test_a_faithful_translation_passes():
    assert check_translation("Index snapshot diff", CITED,
                             "索引快照差异", CITED_ZH, ZH) is None


@pytest.mark.parametrize("new_title,new_desc,expect", [
    ("", CITED_ZH, "empty title"),
    ("索引快照差异", "", "empty description"),
])
def test_blank_output_is_refused(new_title, new_desc, expect):
    """Applying it would blank an authored node — worse than leaving it in English."""
    why = check_translation("Index snapshot diff", CITED, new_title, new_desc, ZH)
    assert why and expect in why


def test_a_dropped_code_citation_is_refused():
    """The text after `codoc:` names a real binding. Lose it and the ref registry
    marks it dead, so the reader loses the link to the code the claim is about."""
    stripped = "比较两次索引快照，使循环不会**重新解析**已经看过的文件。参见 [方案](https://x.test/p)。"
    why = check_translation("Index snapshot diff", CITED, "索引快照差异", stripped, ZH)
    assert why and "dropped code citation" in why
    assert "compute_changeset" in why


def test_a_translated_citation_target_is_refused():
    """The most likely single failure: the model helpfully translates the symbol
    inside a `codoc:` link, and the binding it names no longer exists."""
    bad = CITED_ZH.replace("compute_changeset", "计算变更集")
    why = check_translation("Index snapshot diff", CITED, "索引快照差异", bad, ZH)
    assert why and "dropped code citation" in why


def test_a_dropped_external_link_is_refused():
    """External links become `Consult:` lines — the realizing agent is instructed to
    fetch them, so losing one quietly changes what gets implemented."""
    no_link = ("通过 [compute_changeset()](codoc:codoc/loop/diff.py#compute_changeset) "
               "比较两次索引快照，使循环不会**重新解析**已经看过的文件。")
    why = check_translation("Index snapshot diff", CITED, "索引快照差异", no_link, ZH)
    assert why and "dropped external link" in why


def test_a_dropped_focus_span_is_refused():
    """Bold is not emphasis here: it becomes a `Focus:` line, the part of the intent
    the author marked as most important. Dropping it silently demotes it."""
    no_bold = CITED_ZH.replace("**重新解析**", "重新解析")
    why = check_translation("Index snapshot diff", CITED, "索引快照差异", no_bold, ZH)
    assert why and "focus span" in why


def test_untranslated_prose_is_refused():
    """An echo of the original must not count as done, or the node is marked
    translated and skipped forever on later runs."""
    why = check_translation("Index snapshot diff", CITED, "Index snapshot diff", CITED, ZH)
    assert why and "not Simplified Chinese" in why


def test_a_colliding_title_is_refused():
    """Two features translated into the same words become ONE to the soft
    (normalized_title, parent_id) identity key — and the dedup pass would then
    converge them, destroying a feature that was never a duplicate."""
    why = check_translation(
        "Snapshot comparison", "Compares the previous snapshot with the current one.",
        "索引快照差异", "把上一次的快照与当前快照进行比较。", ZH,
        taken_titles=frozenset({"索引快照差异"}))
    assert why and "collides" in why


def test_a_node_does_not_collide_with_its_own_current_title():
    """Re-running after a partial translation must not refuse the node it already
    translated — the caller excludes the node's own key, and this pins that."""
    assert check_translation("索引快照差异", "比较两次索引快照。", "索引快照差异",
                             "比较两次索引快照，找出变化。", ZH,
                             taken_titles=frozenset()) is None


def test_a_short_description_is_not_language_checked():
    """Below a few words there is no evidence, and refusing on a guess would strand
    nodes that are perfectly fine."""
    assert check_translation("X", "ok", "X", "ok", ZH) is None


# ── the run ─────────────────────────────────────────────────────────────────

def _seed(codoc_dir):
    with open_store(codoc_dir) as s:
        en = Feature(title="Index snapshot diff", description=CITED)
        s.upsert_feature(en)
        s.set_feature_writer(en.id, "someone", ACTOR_HUMAN)
        zh = Feature(title="特征树渲染", description="把存储导出成 tree.codoc，供作者阅读。")
        s.upsert_feature(zh)
        return en.id, zh.id


def _translator(mapping):
    def _p(features, language, **_kw):
        return {f["id"]: mapping[f["id"]] for f in features if f["id"] in mapping}
    return _p


def test_translates_only_what_is_not_already_in_the_language(codoc_dir):
    """Selection is by DETECTED language, which is what makes the command idempotent
    and an interrupted run safe to simply re-run."""
    en_id, zh_id = _seed(codoc_dir)
    seen: list[list[str]] = []

    def spy(features, language, **_kw):
        seen.append([f["id"] for f in features])
        return {en_id: ("索引快照差异", CITED_ZH)}

    res = translate_tree(codoc_dir, language=ZH, propose=spy)
    assert seen == [[en_id]]          # the Chinese node was never sent
    assert res.already == 1
    assert res.translated == 1

    # Idempotent: a second run has nothing left to do and makes no call.
    again = translate_tree(codoc_dir, language=ZH, propose=spy)
    assert again.translated == 0 and again.calls == 0


def test_the_translation_lands_in_the_store_and_the_render(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    with open_store(codoc_dir) as s:
        f = s.get_feature(en_id)
        assert f.title == "索引快照差异"
        assert "compute_changeset" in f.description
    assert "索引快照差异" in (codoc_dir / "tree.codoc").read_text(encoding="utf-8")


def test_the_previous_wording_survives_in_the_ledger(codoc_dir):
    """The undo story. Without this, a migration is irreversible for anyone whose
    tree.codoc was not committed first."""
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    with open_store(codoc_dir) as s:
        prevs = [e.op.prev_description for e in s.events_for_feature(en_id, limit=5)
                 if e.op.prev_description]
        assert prevs and prevs[0] == CITED


def test_the_prior_writer_role_is_preserved(codoc_dir):
    """apply_op reassigns the writer to whoever wrote last, so translating would
    re-stamp a human-authored node as loop-written — dropping it from the strict
    preserve gate to the loose one and licensing the loop to revise it freely."""
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    with open_store(codoc_dir) as s:
        assert s.feature_writer_info(en_id)[1] == ACTOR_HUMAN


def test_the_event_still_records_that_the_loop_wrote_the_words(codoc_dir):
    """Preserving the ROLE must not falsify the LEDGER: the Chinese sentences are the
    model's, and blame has to say so even though the node stays author-protected."""
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    with open_store(codoc_dir) as s:
        ev = [e for e in s.events_for_feature(en_id, limit=5)
              if e.source == TRANSLATE_SOURCE]
        assert ev and ev[0].actor == ACTOR_LOOP
        assert "translated to" in ev[0].op.rationale


def test_no_realize_directive_is_queued(codoc_dir):
    """The failure that would matter most: an AMEND arriving through the authored
    channel mints a realize directive, so a mass translation could ask the agent to
    reimplement the entire codebase. Directives are minted in Loop B's drain, which a
    direct apply never touches — pinned here because the cost of regressing it is a
    hundred spurious code changes."""
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    assert not (codoc_dir / "realize.md").exists()
    assert not (codoc_dir / "realize.json").exists()


def test_dry_run_writes_nothing(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    res = translate_tree(codoc_dir, language=ZH, dry_run=True,
                         propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    assert res.translated == 1
    assert res.preview == [("Index snapshot diff", "索引快照差异")]
    with open_store(codoc_dir) as s:
        assert s.get_feature(en_id).title == "Index snapshot diff"


def test_a_refused_node_is_reported_and_left_alone(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    bad = CITED_ZH.replace("compute_changeset", "计算变更集")
    res = translate_tree(codoc_dir, language=ZH,
                         propose=_translator({en_id: ("索引快照差异", bad)}))
    assert res.translated == 0
    assert [s.reason for s in res.skipped] and "citation" in res.skipped[0].reason
    with open_store(codoc_dir) as s:
        assert s.get_feature(en_id).title == "Index snapshot diff"


def test_a_failed_batch_does_not_sink_the_run(codoc_dir):
    """Half a tree translated and the other half intact beats a rolled-back run: the
    command is resumable, so the remainder is one re-run away."""
    _seed(codoc_dir)

    def boom(features, language, **_kw):
        raise RuntimeError("rate limited")

    res = translate_tree(codoc_dir, language=ZH, propose=boom)
    assert res.translated == 0
    assert res.skipped and "rate limited" in res.skipped[0].reason


def test_the_language_is_not_a_one_way_door(codoc_dir):
    """Translating BACK is the same operation in the other direction.

    The regression this pins: `codoc translate` refused to run whenever the target
    was English, on the reasoning that English is the default and therefore means
    "unset". That made a tree translated to Chinese impossible to bring back —
    switching the setting worked, and the one command that could act on it declined.
    """
    en = resolve("en")
    with open_store(codoc_dir) as s:
        zh = Feature(title="索引快照差异",
                     description="比较两次索引快照，找出新增和删除的代码块。")
        s.upsert_feature(zh)
        fid = zh.id

    back = {fid: ("Index snapshot diff",
                  "Diffs two index snapshots and reports what was added or removed.")}
    res = translate_tree(codoc_dir, language=en, propose=_translator(back))
    assert res.translated == 1
    with open_store(codoc_dir) as s:
        assert s.get_feature(fid).title == "Index snapshot diff"


def test_an_already_english_tree_reports_nothing_to_do(codoc_dir):
    """…and the honest answer for a tree that needs no work is "nothing to do", not a
    refusal to look."""
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="Index snapshot diff",
                                 description="Diffs two index snapshots each pass."))

    def never(features, language, **_kw):
        raise AssertionError("no LLM call should be made when nothing needs translating")

    res = translate_tree(codoc_dir, language=resolve("en"), propose=never)
    assert res.translated == 0 and res.already == 1 and not res.skipped


def test_limit_bounds_a_trial_run(codoc_dir):
    with open_store(codoc_dir) as s:
        for i in range(5):
            s.upsert_feature(Feature(title=f"Feature {i}",
                                     description=f"Does the {i}th English thing here."))
    sent: list[str] = []

    def spy(features, language, **_kw):
        sent.extend(f["id"] for f in features)
        return {}

    translate_tree(codoc_dir, language=ZH, limit=2, propose=spy)
    assert len(sent) == 2


# ── the progress channel (`.codoc/translate.json`) ───────────────────────────
#
# The IDE draws a per-node skeleton for every fid still in `pending` and replaces
# each skeleton as its batch's re-render lands — so the channel's honesty (fids
# leave `pending` exactly when their fate is decided, `running` never survives the
# run) is what the whole two-stage language switch UX rests on.

def _progress(codoc_dir):
    from codoc.loop.translate import read_translate_progress
    return read_translate_progress(codoc_dir)


def test_progress_finalizes_not_running_with_empty_pending(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    p = _progress(codoc_dir)
    assert p["running"] is False
    assert p["pending"] == []
    assert p["translated"] == 1
    assert p["total"] == 1
    assert p["target"] == "zh-Hans"


def test_progress_pending_shrinks_per_batch_and_render_is_incremental(codoc_dir):
    """Each batch must (a) drop its fids from `pending` and (b) re-render the
    derived artifacts, so the IDE reveals translations batch by batch instead of
    all at the end."""
    import codoc.loop.translate as tr
    with open_store(codoc_dir) as s:
        f1 = Feature(title="First feature", description="Does the first English thing.")
        f2 = Feature(title="Second feature", description="Does the second English thing.")
        s.upsert_feature(f1)
        s.upsert_feature(f2)
        ids = [f1.id, f2.id]

    observed: list[dict] = []
    rendered_mid: list[str] = []
    zh_out = {ids[0]: ("第一个特性", "做第一件事的中文描述内容。"),
              ids[1]: ("第二个特性", "做第二件事的中文描述内容。")}

    def one_at_a_time(features, language, **_kw):
        # Snapshot the progress file AND the render AS THE RUN GOES (each propose
        # call happens after the previous batch's progress write + re-render).
        observed.append(_progress(codoc_dir))
        tree = codoc_dir / "tree.codoc"
        rendered_mid.append(tree.read_text(encoding="utf-8") if tree.exists() else "")
        return {f["id"]: zh_out[f["id"]] for f in features}

    old_batch = tr.BATCH
    tr.BATCH = 1
    try:
        translate_tree(codoc_dir, language=ZH, propose=one_at_a_time)
    finally:
        tr.BATCH = old_batch

    # First call: both pending. Second call: the first batch's fid is gone AND its
    # translation is already visible in the re-rendered tree.codoc — mid-run, before
    # the second batch has even been proposed.
    assert set(observed[0]["pending"]) == set(ids)
    assert observed[0]["running"] is True
    assert observed[1]["pending"] == [ids[1]]
    assert "第一个特性" in rendered_mid[1]
    final = _progress(codoc_dir)
    assert final["running"] is False and final["pending"] == []
    assert (codoc_dir / "tree.doc.json").exists()


def test_progress_reports_skips_and_still_finalizes(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    bad = CITED_ZH.replace("compute_changeset", "计算变更集")
    translate_tree(codoc_dir, language=ZH,
                   propose=_translator({en_id: ("索引快照差异", bad)}))
    p = _progress(codoc_dir)
    assert p["running"] is False and p["pending"] == []
    assert p["skipped"] and "citation" in p["skipped"][0]["reason"]


def test_progress_finalizes_even_when_the_run_dies(codoc_dir):
    """A raised error mid-run must not leave `running: true` behind — the IDE would
    skeleton-lock nodes for a run that no longer exists."""
    _seed(codoc_dir)

    def boom(features, language, **_kw):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        translate_tree(codoc_dir, language=ZH, propose=boom)
    assert _progress(codoc_dir)["running"] is False


def test_dry_run_writes_no_progress(codoc_dir):
    en_id, _ = _seed(codoc_dir)
    translate_tree(codoc_dir, language=ZH, dry_run=True,
                   propose=_translator({en_id: ("索引快照差异", CITED_ZH)}))
    from codoc.loop.translate import translate_progress_path
    assert not translate_progress_path(codoc_dir).exists()
