"""#P0-1 — markdown syntax in a description must survive render→parse→diff.

Before the fix, ``render.py`` wrote description lines verbatim while
``parse.py``'s feature regex matched *any* indented ``-``/``~`` line at any depth
(and dropped ``#`` lines as comments). So a description containing a markdown
bullet (``- validates tokens``), a heading (``# Notes``), or a tilde line parsed
back as a PHANTOM child feature with the original description truncated at the
first marker. That made ``diff_codoc`` non-empty forever, ``has_pending_user_edits``
permanently True, and ``safe_write_tree`` skipped every future render — ``tree.codoc``
went permanently stale and every watch batch misrouted to a no-op Loop B.

The parser now treats a marker/heading line indented deeper than a direct child
(``owner_indent + 2``) as description prose, because render.py writes descriptions
at ``owner_indent + 4`` and real children at ``owner_indent + 2``. These tests pin
the invariant ``render → parse → diff == empty`` over a broad, deterministic fuzz
of description content plus the exact field repros.
"""
from __future__ import annotations

import random

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import normalize_description, parse_text, parse_tree_file
from codoc.codoc_file.render import render_tree, write_tree
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    (tmp_path / ".codoc").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── exact field repros (the instructor's verified break) ─────────────────────

# NB: a `> …` blockquote is intentionally NOT prose — it is a steering comment,
# extracted to a separate channel (see test_steering.py). Real stored descriptions
# never carry raw `>` lines, so they are excluded here; #1 is about markdown MARKERS
# (``-`` ``~`` ``#`` ``+`` ``*``) appearing inside ordinary description prose.
MARKDOWN_REPROS = [
    "- validates tokens\n- checks expiry",         # a bullet list
    "Steps:\n- one\n- two\n- three",               # prose then bullets
    "# Overview\nsome text\n## Details\nmore",      # ATX headings
    "~ a tilde-led line",                           # looks like a retired feature
    "+ an added line\n* a star bullet",             # other markers
    "text with a `- ` inside a sentence - like this",
    "1. numbered\n2. list",                         # ordered list
    "```\n- code fence bullet\n```",                # fenced code block
    "See [ref](codoc:auth.py#login) then\n- do X",  # inline ref + bullet
]


@pytest.mark.parametrize("desc", MARKDOWN_REPROS)
def test_markdown_description_round_trips_text(codoc_dir, desc):
    """A single feature whose description holds markdown must re-parse as ONE node
    with the description intact (canonical form) — no phantom child, no truncation."""
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="Token auth", description=desc))
        write_tree(s, codoc_dir)
        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors
        assert len(parsed.nodes) == 1
        assert parsed.nodes[0].description == normalize_description(desc)
        # The load-bearing invariant: reconcile.has_pending_user_edits reads this.
        assert diff_codoc(parsed, s).is_empty()


def test_markdown_in_nested_tree_round_trips(codoc_dir):
    """A markdown bullet in a PARENT's description (rendered at parent_indent+4) must
    not be mistaken for the child that follows it (rendered at parent_indent+2)."""
    with open_store(codoc_dir) as s:
        parent = Feature(title="Auth", description="Handles:\n- login\n- logout")
        s.upsert_feature(parent)
        child = Feature(title="Sessions", description="- token TTL\n- refresh", parent_id=parent.id)
        s.upsert_feature(child)
        grandchild = Feature(title="TTL", description="# rules\n- 15 min", parent_id=child.id)
        s.upsert_feature(grandchild)
        write_tree(s, codoc_dir)
        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors
        assert len(parsed.nodes) == 3
        titles = {n.title for n in parsed.nodes}
        assert titles == {"Auth", "Sessions", "TTL"}
        # parent/child structure preserved (no bullet promoted to a feature)
        assert diff_codoc(parsed, s).is_empty()


# ── property-style deterministic fuzz over description content ────────────────

# `>` is deliberately absent — it is a steering marker, not description prose.
_FRAGMENTS = [
    "plain sentence.",
    "- bullet item",
    "* star bullet",
    "+ plus bullet",
    "~ tilde line",
    "# heading",
    "## subheading",
    "1. ordered item",
    "  - indented bullet",
    "text - with an inline dash",
    "[label](codoc:file.py#sym) citation",
    "[docs](https://example.com/x) link",
    "**bold focus** span",
    "",            # blank line (paragraph break)
    "   ",         # whitespace-only line
    "trailing space   ",
]


def _random_description(rng: random.Random) -> str:
    n = rng.randint(1, 8)
    return "\n".join(rng.choice(_FRAGMENTS) for _ in range(n))


@pytest.mark.parametrize("seed", range(60))
def test_fuzz_render_parse_diff_is_empty(codoc_dir, seed):
    """render → parse → diff == empty over arbitrary description content, across a
    randomly-shaped small tree. Deterministic (seeded) so a failure reproduces."""
    rng = random.Random(seed)
    with open_store(codoc_dir) as s:
        # A little tree: 1-3 roots, each with 0-2 children, arbitrary descriptions.
        roots = []
        for _ in range(rng.randint(1, 3)):
            f = Feature(title=f"Root{rng.randint(0, 999)}", description=_random_description(rng))
            s.upsert_feature(f)
            roots.append(f)
            for _ in range(rng.randint(0, 2)):
                c = Feature(title=f"Child{rng.randint(0, 999)}",
                            description=_random_description(rng), parent_id=f.id)
                s.upsert_feature(c)
        write_tree(s, codoc_dir)

        parsed = parse_tree_file(codoc_dir)
        assert not parsed.errors, (seed, parsed.errors)
        diff = diff_codoc(parsed, s)
        assert diff.is_empty(), (
            seed,
            [(op.kind.value, op.title, op.feature_id) for op in diff.user_ops],
        )


def test_render_is_a_fixed_point_under_reparse(codoc_dir):
    """Rendering the store, parsing it back into a fresh store, and re-rendering yields
    byte-identical text — the markdown fix must not perturb the canonical render."""
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="A", description="prose\n- b1\n- b2\n\n# h\nmore"))
        first = render_tree(s)
    # Parse the rendered text directly (no disk) and confirm the description survives.
    parsed = parse_text(first)
    assert len(parsed.nodes) == 1
    assert parsed.nodes[0].description == normalize_description("prose\n- b1\n- b2\n\n# h\nmore")
