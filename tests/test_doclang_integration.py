"""A non-Latin tree, end to end through the parts that were Latin-shaped.

Every fixture in ``tests/fixtures`` is Latin-script, which is why the assumptions
these tests cover survived so long: a tree authored in Chinese exercised no test.
So the assertions here are deliberately about the *seams* — render→parse identity,
the amend gate, the dedup key, the placeholder adopter, the prompt, and what the
MCP tools tell an agent — rather than about the language itself.
"""
from __future__ import annotations

import json

import pytest

from codoc import doclang as dl
from codoc.agent.base import load_prompt, split_prompt
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.render import write_tree
from codoc.loop.apply import (
    PRESERVE_RATIO_HUMAN, is_small_amend, preserved_ratio,
)
from codoc.model.event import ACTOR_HUMAN, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    (tmp_path / ".codoc").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── render → parse round trip ───────────────────────────────────────────────

ZH_NODES = [
    ("索引快照差异", "比较两次索引快照，找出新增、修改和删除的代码块。"),
    ("特征树渲染", "把存储里的特征树导出成 tree.codoc，供作者阅读和编辑。"),
    ("解析树适配器", "为 Python 与 TypeScript 提供 tree-sitter 适配，产出统一的代码块。"),
]


def test_chinese_tree_round_trips(codoc_dir):
    """The identity invariant the whole pipeline rests on: render → parse → diff is
    empty. If it were not, ``has_pending_user_edits`` would be permanently true and
    every later render would be skipped — the tree would silently go stale."""
    with open_store(codoc_dir) as s:
        for title, desc in ZH_NODES:
            s.upsert_feature(Feature(title=title, description=desc))
        write_tree(s, codoc_dir)
        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors
        assert {n.title for n in parsed.nodes} == {t for t, _ in ZH_NODES}
        assert diff_codoc(parsed, s).is_empty()


def test_chinese_tree_with_citations_and_author_signals_round_trips(codoc_dir):
    """A realistic Chinese description: an inline `codoc:` citation whose target
    stays Latin, a bolded focus span, and full-width punctuation."""
    desc = ("通过 [compute_changeset()](codoc:codoc/loop/diff.py#compute_changeset) "
            "比较快照（避免重复解析），并把**结果缓存**起来。")
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="变更集计算", description=desc))
        write_tree(s, codoc_dir)
        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors
        assert len(parsed.nodes) == 1
        assert "codoc:codoc/loop/diff.py#compute_changeset" in parsed.nodes[0].description
        assert diff_codoc(parsed, s).is_empty()


def test_nested_chinese_tree_round_trips(codoc_dir):
    """Indentation is what separates a description line from a child node, and CJK
    characters are double-WIDTH but single characters — so a renderer that padded by
    display width instead of count would break here."""
    with open_store(codoc_dir) as s:
        parent = Feature(title="两个循环", description="代码与意图之间的双向同步。")
        s.upsert_feature(parent)
        child = Feature(title="循环 A：代码到文档", description="快照差分后反射到特征树。",
                        parent_id=parent.id)
        s.upsert_feature(child)
        s.upsert_feature(Feature(title="循环 B：文档到代码", description="把意图编辑排入实现队列。",
                                 parent_id=parent.id))
        write_tree(s, codoc_dir)
        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors
        assert len(parsed.nodes) == 3
        assert diff_codoc(parsed, s).is_empty()


def test_tree_codoc_is_written_as_utf8(codoc_dir):
    """The export is read back by the parser, the TS parity parser, and a human. A
    locale-dependent write would mojibake all three on a non-UTF-8 machine."""
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="索引快照差异", description="比较两次快照。"))
        write_tree(s, codoc_dir)
    raw = (codoc_dir / "tree.codoc").read_bytes()
    assert "索引快照差异".encode("utf-8") in raw


def test_doc_projection_holds_readable_characters(codoc_dir):
    """``tree.doc.json`` is the webview's whole input and lands in code review.
    Escaped, one CJK character costs six ASCII ones — several-fold growth and an
    unreviewable diff, for no gain."""
    from codoc.loop.loop_b import write_tree_doc

    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="索引快照差异", description="比较两次快照。"))
        write_tree_doc(s, codoc_dir)
    raw = (codoc_dir / "tree.doc.json").read_text(encoding="utf-8")
    assert "索引快照差异" in raw
    assert "\\u7d22" not in raw
    json.loads(raw)  # still valid JSON


# ── the amend gate ──────────────────────────────────────────────────────────

def test_a_small_chinese_repair_is_recognised_as_a_repair(codoc_dir):
    """The gate asks "did this keep what was there?". With a 24-character floor no
    run in a Chinese description ever qualified, so `preserved_ratio` was ~0 and a
    one-clause repair to the author's own prose was queued as a full rewrite."""
    old = "比较两次索引快照，找出新增、修改和删除的代码块，交给上层决定如何归属，避免重复解析整个仓库。"
    new = "比较两次索引快照，找出新增、修改和删除的代码块，交给循环 A 决定如何归属，避免重复解析整个仓库。"
    assert preserved_ratio(old, new) >= PRESERVE_RATIO_HUMAN

    with open_store(codoc_dir) as s:
        f = Feature(title="索引快照差异", description=old)
        s.upsert_feature(f)
        # The strict bar applies only to prose a PERSON wrote, which is the case
        # that matters: an author whose repair is refused sees their own wording
        # sent to a review queue.
        s.set_feature_writer(f.id, "someone", ACTOR_HUMAN)
        op = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description=new)
        assert is_small_amend(op, s)


def test_a_chinese_rewrite_is_still_a_rewrite(codoc_dir):
    """The gate must not have been loosened into uselessness: prose replaced with
    unrelated prose still has to surface for review."""
    old = "比较两次索引快照，找出新增、修改和删除的代码块，交给上层决定如何归属。"
    new = "把特征树导出为只读文件，供作者在编辑器里阅读，并且不再回读手工编辑。"
    assert preserved_ratio(old, new) < PRESERVE_RATIO_HUMAN


def test_english_amend_behaviour_is_unchanged(codoc_dir):
    """Pins the port: `clause_chars` returns 24 for Latin prose, so the English
    gate must give exactly the answers it gave before."""
    old = ("Snapshot-diffs the index each pass and reports what changed, so the "
           "loop never re-parses a file it has already seen.")
    repaired = old.replace("never re-parses", "does not re-parse")
    assert preserved_ratio(old, repaired) >= PRESERVE_RATIO_HUMAN
    assert preserved_ratio(old, "Exports the tree to a read-only file.") == 0.0


# ── the dedup key ───────────────────────────────────────────────────────────

def test_full_width_variants_share_one_dedup_key():
    """Both loops key node identity on this, and an IME is exactly where the two
    spellings come from — so this is the duplicate-node bug arriving by keyboard."""
    from codoc.loop.loop_a import _norm_title as norm_a
    from codoc.loop.loop_b import _norm_title as norm_b

    assert norm_a("解析（快照）") == norm_a("解析(快照)")
    assert norm_a("解析（快照）") == norm_b("解析（快照）")


def test_both_loops_share_one_normalizer():
    """They must agree or a title deduped by one loop is minted twice by the other."""
    from codoc.loop.loop_a import _norm_title as norm_a
    from codoc.loop.loop_b import _norm_title as norm_b

    for t in ("Index Snapshot", "索引快照", "Ｐａｒｓｅ", "  a  b  ", None):
        assert norm_a(t) == norm_b(t)


# ── the placeholder adopter ─────────────────────────────────────────────────

def test_a_chinese_placeholder_adopts_the_symbol_it_planned(codoc_dir):
    """A substring test cannot fire when the prose is in another language than the
    code, so a Chinese plan node never adopted its own symbol and a duplicate got
    minted beside it."""
    from codoc.loop.loop_a import _placeholder_owner

    planned = Feature(title="变更集计算",
                      description="实现 compute_changeset，用于比较两次索引快照。")
    other = Feature(title="特征树渲染", description="把特征树导出成只读文件。")
    owner = _placeholder_owner([other, planned],
                               "codoc/loop/diff.py::compute_changeset", sole_ok=False)
    assert owner == planned.id


def test_an_ambiguous_match_declines_rather_than_guesses(codoc_dir):
    """Adopting the wrong placeholder binds new code to a feature planned for
    something else and marks that plan realized — worse than a duplicate."""
    from codoc.loop.loop_a import _placeholder_owner

    a = Feature(title="快照比较（读取）", description="负责 snapshot 的读取。")
    b = Feature(title="快照比较（写入）", description="负责 snapshot 的写入。")
    assert _placeholder_owner([a, b], "x.py::snapshot_sync", sole_ok=False) is None


def test_a_generic_word_alone_does_not_adopt(codoc_dir):
    """`get`/`run`/`set` recur across every subsystem, so one of them shared with a
    description must not be enough to bind code to that plan node."""
    from codoc.loop.loop_a import _discriminating, _placeholder_owner

    assert _discriminating("x.py::get") == set()
    assert _discriminating("x.py::get_config") == {"config"}

    # No literal "get" in the prose, so the substring test cannot fire and the new
    # term path is the only one left — and it must decline.
    f = Feature(title="配置读取", description="读取并缓存配置项。")
    assert _placeholder_owner([f], "x.py::get", sole_ok=False) is None


# ── the divergence signal ───────────────────────────────────────────────────

def test_divergence_can_actually_fire_on_chinese_prose():
    """It used to be structurally impossible: both sides tokenized to nothing, two
    empty sets compared as identical, and every Chinese realization scored a perfect
    1.0. A check that always answers "faithful" is not a check."""
    from codoc.loop.divergence import Divergence, Realization, classify_realization

    r = Realization(target_feature_id="f-1",
                    intent_text="给客户端加上超时重试",
                    realized_text="把特征树导出成只读文件")
    assert classify_realization(r, intent_ratio=0.4) is Divergence.INTENT


def test_an_equivalent_chinese_rewording_is_still_faithful():
    """The leniency the signal was designed around has to survive in a script whose
    word boundaries are unmarked."""
    from codoc.loop.divergence import Divergence, Realization, classify_realization

    r = Realization(target_feature_id="f-1",
                    intent_text="添加输入验证",
                    realized_text="验证输入")
    assert classify_realization(r, intent_ratio=0.4) is Divergence.FAITHFUL


# ── prompts ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["tree_update", "bootstrap_file", "bootstrap_org", "realize"])
def test_every_prompt_expands_the_marker(name):
    """A forgotten marker would ship the literal `{{doclang}}` to a model."""
    assert "{{doclang}}" not in load_prompt(name)
    assert "{{doclang}}" not in load_prompt(name, doc_language=dl.resolve("zh-Hans"))


@pytest.mark.parametrize("name", ["tree_update", "bootstrap_file", "bootstrap_org", "realize"])
def test_non_english_prompts_carry_the_directive(name):
    assert "简体中文" in load_prompt(name, doc_language=dl.resolve("zh-Hans"))


def test_the_directive_rides_in_the_cached_prefix():
    """Instructions belong in the stable prefix, so switching language costs one
    cache miss rather than a per-pass surcharge forever."""
    prefixes, tail = split_prompt(load_prompt("tree_update",
                                              doc_language=dl.resolve("zh-Hans")))
    assert any("简体中文" in p for p in prefixes)
    assert "简体中文" not in tail


@pytest.mark.parametrize("name", ["tree_update", "bootstrap_file", "bootstrap_org", "realize"])
def test_english_prompts_have_no_gap_where_the_block_would_go(name):
    """The marker collapses with the blank lines around it, so an English prompt
    reads as it did before — no stray blank run, and every cache-split segment still
    starts and ends on real content."""
    text = load_prompt(name)
    assert "\n\n\n" not in text
    # No cache-split segment may open or close on a blank line — that is what a
    # removed section leaves behind, and it is what the collapse exists to prevent.
    # (A single trailing newline is the file's own and predates this feature.)
    for segment in (*split_prompt(text),):
        for part in (segment if isinstance(segment, list) else [segment]):
            assert not part.startswith("\n")
            assert not part.rstrip(" \t").endswith("\n\n")


# ── the loop resolves the language from the workspace ───────────────────────
# These pin the wires. Each is one keyword argument threaded from a control file to
# a template, and if one came loose the tree would keep working and quietly emit
# English — the failure mode with no error and no test to catch it.

def test_realize_prompt_takes_the_language_from_the_workspace(codoc_dir):
    from codoc.loop.loop_b import build_realize_prompt

    dl.write_config(codoc_dir, doc_language="zh-Hans")
    prompt = build_realize_prompt(["UPDATE FEATURE 索引快照差异"], "/repo", ["d-abc"],
                                  codoc_dir=codoc_dir)
    assert "简体中文" in prompt
    assert "source code is not translated" in prompt
    # A caller with no workspace still gets a valid English prompt.
    assert "简体中文" not in build_realize_prompt(["x"], "/repo", ["d-1"])


def test_loop_a_hands_the_language_to_its_llm_call(codoc_dir):
    """`apply_changeset` reads the setting from `codoc_dir` so the daemon needs no
    plumbing of its own — and so a bare unit-test caller still gets English."""
    from codoc.loop.diff import ChangeSet
    from codoc.loop.loop_a import apply_changeset

    dl.write_config(codoc_dir, doc_language="ja")
    seen = {}

    def capture(changes, subtree, all_titles, *, repo_name="codebase", config=None,
                doc_language=None, **_kw):
        seen["code"] = doc_language.code if doc_language else None
        return []

    with open_store(codoc_dir) as s:
        f = Feature(title="インデックス差分", description="スナップショットを比較する。")
        s.upsert_feature(f)
        # An unbound addition is the cheapest change that forces the LLM pass.
        cs = ChangeSet(added=[_chunk("a.py", "a.py::parse")], removed=[], modified=[])
        apply_changeset(cs, s, propose=capture, codoc_dir=str(codoc_dir))
    assert seen["code"] == "ja"


def _chunk(file: str, symbol: str):
    from codoc.loop.diff import ChunkRef

    return ChunkRef(file=file, symbol_path=symbol, fingerprint="t1", source="def parse(): ...",
                    types_hash="h1")


# ── what the agent is told ──────────────────────────────────────────────────

def test_mcp_reads_report_the_language(codoc_dir):
    """The coding agent is the one writer codoc cannot put a prompt in front of, so
    the tools have to carry the setting."""
    from codoc.mcp import tools

    cd = str(codoc_dir / ".codoc")
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="索引快照差异", description="比较两次快照。"))
    dl.write_config(cd, doc_language="zh-Hans")

    tree = tools.read_tree(cd)
    assert tree["doc_language"]["code"] == "zh-Hans"
    assert "instruction" in tree["doc_language"]
    assert tools.read_status(cd)["doc_language"]["code"] == "zh-Hans"


def test_english_repo_gets_no_instruction_paragraph(codoc_dir):
    """One short field in the common case, not a paragraph of unused prose."""
    from codoc.mcp import tools

    cd = str(codoc_dir / ".codoc")
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="Index diff", description="Diffs snapshots."))
    block = tools.read_tree(cd)["doc_language"]
    assert block["code"] == "en"
    assert "instruction" not in block


def test_english_prose_in_a_chinese_tree_is_flagged_but_applied(codoc_dir):
    """Advisory on purpose: refusing would discard work already done and leave the
    tree describing code that has already changed."""
    from codoc.mcp import tools

    cd = str(codoc_dir / ".codoc")
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="占位", description="占位说明。"))
    dl.write_config(cd, doc_language="zh-Hans")

    res = tools.propose_add(cd, title="Snapshot diff",
                            description="Diffs the index snapshot each pass.")
    assert res["ok"]
    assert "warning" in res


def test_correct_chinese_prose_with_citations_is_not_flagged(codoc_dir):
    """The check must approve the descriptions codoc itself asks for — which cite
    code, in Latin, by design."""
    from codoc.mcp import tools

    cd = str(codoc_dir / ".codoc")
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="占位", description="占位说明。"))
    dl.write_config(cd, doc_language="zh-Hans")

    res = tools.propose_add(
        cd, title="变更集计算",
        description=("通过 [compute_changeset()](codoc:codoc/loop/diff.py#compute_changeset) "
                     "比较两次索引快照，避免重复解析整个仓库。"))
    assert res["ok"]
    assert "warning" not in res


# ── the workspace heal ──────────────────────────────────────────────────────

def test_migrate_makes_an_old_workspace_track_the_language(codoc_dir):
    """`.codoc/.gitignore` is written once and never overwritten, so without this an
    existing repo keeps the setting untracked: the maintainer commits a Chinese
    tree and the contributor who clones gets an English default."""
    from codoc.loop.migrate import migrate_workspace

    cd = codoc_dir / ".codoc"
    gi = cd / ".gitignore"
    gi.write_text("*\n!.gitignore\n!tree.codoc\n!tree.doc.json\n", encoding="utf-8")

    res = migrate_workspace(cd)
    assert "config.json" in res.gitignore_healed
    assert "!config.json" in gi.read_text(encoding="utf-8")

    # Idempotent, and it leaves an existing customization alone.
    again = migrate_workspace(cd)
    assert not again.gitignore_healed


def test_migrate_leaves_a_customized_gitignore_alone(codoc_dir):
    from codoc.loop.migrate import migrate_workspace

    cd = codoc_dir / ".codoc"
    gi = cd / ".gitignore"
    custom = "# mine\n*\n!.gitignore\n!tree.codoc\n!tree.doc.json\n!config.json\n!notes.md\n"
    gi.write_text(custom, encoding="utf-8")
    assert not migrate_workspace(cd).gitignore_healed
    assert gi.read_text(encoding="utf-8") == custom
