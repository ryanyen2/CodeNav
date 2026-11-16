"""The Markdown codecs: inline (text + spans) and block (BlockSpec lists)."""
from __future__ import annotations

import random

from codoc.calculus import block_md
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.doc import BULLET, CODE_BLOCK, DIAGRAM, HEADING, NUMBERED, PARAGRAPH, TABLE
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.text import BOLD, CITE, CODE, LINK


def _merged(spans: list[Span], n: int) -> dict:
    """Spans as per-kind sets of covered offsets: adjacent same-kind pieces
    are one mark once ``Text.set_mark`` merges them."""
    out: dict = {}
    for s in spans:
        key = (s.kind, tuple(sorted(s.attrs.items())))
        out.setdefault(key, set()).update(range(s.start, min(s.end, n)))
    return {k: v for k, v in out.items() if v}


def _as_stored(spans: list[Span], n: int) -> list[Span]:
    """The spans as a store would hold them: same-kind marks that overlap or
    touch are one mark."""
    out: list[Span] = []
    for (kind, attrs), offs in _merged(spans, n).items():
        run: list[int] = []
        for o in sorted(offs) + [None]:
            if run and (o is None or o != run[-1] + 1):
                out.append(Span(kind, dict(attrs), run[0], run[-1] + 1))
                run = []
            if o is not None:
                run.append(o)
    return out


# ── inline ───────────────────────────────────────────────────────────────────

def test_inline_forms():
    text, spans = parse("Calls `send()` **once**, see [docs](https://x.y/z) and [Client](codoc:a.py#a.py::Client).")
    assert text == "Calls send() once, see docs and Client."
    assert [(s.kind, text[s.start:s.end]) for s in spans] == [
        (CODE, "send()"), (BOLD, "once"), (LINK, "docs"), (CITE, "Client")]
    assert spans[3].attrs == {"file": "a.py", "symbol": "a.py::Client"}


def test_code_holds_markup_literally():
    text, spans = parse("`list[int]` and `**kwargs` and `a\\`b` and `C:\\\\dir`")
    assert text == "list[int] and **kwargs and a`b and C:\\dir"
    assert [text[s.start:s.end] for s in spans] == ["list[int]", "**kwargs", "a`b", "C:\\dir"]
    assert all(s.kind == CODE for s in spans)


def test_nesting_link_bold_code():
    text, spans = parse("[**bold `code`** tail](https://x.y)")
    assert text == "bold code tail"
    kinds = {s.kind: text[s.start:s.end] for s in spans}
    assert kinds == {LINK: "bold code tail", BOLD: "bold code", CODE: "code"}


def test_unclosed_markup_is_literal():
    for md in ("a ` b", "a ** b", "[x](not a url)", "[x", "``"):
        assert parse(md) == (md, [])


def test_inline_round_trip_fuzz():
    rng = random.Random(7)
    alphabet = "ab *[]\\#`-+.|_()"
    for trial in range(1500):
        n = rng.randint(0, 14)
        text = "".join(rng.choice(alphabet) for _ in range(n))
        spans: list[Span] = []
        for _ in range(rng.randint(0, 4)):
            if n == 0:
                break
            a = rng.randrange(n)
            b = rng.randint(a + 1, n)
            kind = rng.choice([BOLD, CODE, LINK, CITE])
            attrs = {LINK: {"href": "https://x.y/z"}, CITE: {"file": "a.py", "symbol": "a.py::f"}}.get(kind, {})
            # the store keeps link and cite exclusive: no two overlap
            if kind in (LINK, CITE) and any(s.kind in (LINK, CITE) and s.start < b and a < s.end for s in spans):
                continue
            spans.append(Span(kind, attrs, a, b))
        spans = _as_stored(spans, n)
        md = render(text, spans)
        text2, spans2 = parse(md)
        assert text2 == text, (trial, text, spans, md)
        assert _merged(spans2, n) == _merged(spans, n), (trial, text, spans, md, spans2)


# ── block ────────────────────────────────────────────────────────────────────

SKETCH = """\
Retries a failed request so a caller does not see a transient error.

- Retries only on timeout; a `4xx` is returned as is.
- Backoff doubles from `0.5s`, capped by `MAX_BACKOFF`.
    - Jitter is added so clients do not retry in step.

1. Send the request.
2. On timeout, wait and send again.

| setting | default | meaning |
|---|---|---|
| `retries` | 3 | attempts after the first |

```mermaid
flowchart LR
  send --> wait --> send
```

```python
for attempt in range(retries + 1): ...
```
"""


def test_block_parse_sketch():
    specs = block_md.parse(SKETCH, headings=False)
    assert [(s.kind, s.level) for s in specs] == [
        (PARAGRAPH, 0), (BULLET, 1), (BULLET, 1), (BULLET, 2), (NUMBERED, 1), (NUMBERED, 1),
        (TABLE, 0), (DIAGRAM, 0), (CODE_BLOCK, 0)]
    assert specs[3].text == "Jitter is added so clients do not retry in step."
    assert specs[6].text.count("\n") == 2
    assert specs[7].text == "flowchart LR\n  send --> wait --> send"
    assert specs[8].content == {"lang": "python"}
    assert block_md.render(specs) == SKETCH


def test_block_parse_tolerates_model_habits():
    md = "Steps:\n* one\n  wrapped\n  * two-deep\n2. numbered\n## Not a feature\n"
    specs = block_md.parse(md, headings=False)
    assert [(s.kind, s.level, s.text) for s in specs] == [
        (PARAGRAPH, 0, "Steps:"), (BULLET, 1, "one wrapped"), (BULLET, 2, "two-deep"),
        (NUMBERED, 1, "numbered"), (PARAGRAPH, 0, "Not a feature")]


def test_heading_ids_and_json_fence():
    md = '# Client {#b-0000000000aa}\n\n```codoc:image {#b-0000000000bb}\n{"asset": "a.png"}\n```\n'
    specs = block_md.parse(md)
    assert specs == [BlockSpec(HEADING, 1, "Client", id="b-0000000000aa"),
                     BlockSpec("image", content={"asset": "a.png"}, id="b-0000000000bb")]
    assert block_md.render(specs, ids=True) == md


def test_paragraph_that_looks_like_a_block_is_escaped():
    for text in ("- not a bullet", "1. not a step", "| not a table", "+ plus", "* star"):
        inline = render(text, [])
        md = block_md.render([BlockSpec(PARAGRAPH, text=inline)])
        specs = block_md.parse(md)
        assert [s.kind for s in specs] == [PARAGRAPH], md
        assert parse(specs[0].text)[0] == text, md


def test_source_holding_a_fence_line_gets_a_longer_fence():
    spec = BlockSpec(CODE_BLOCK, text="a\n```\nb", content={"lang": "md"})
    assert block_md.parse(block_md.render([spec])) == [spec]


def test_table_that_is_not_rows_falls_back_to_json():
    spec = BlockSpec(TABLE, text="| a |\nnot a row")
    md = block_md.render([spec])
    assert md.startswith("```codoc:table")
    assert block_md.parse(md) == [spec]


def test_block_round_trip_fuzz():
    rng = random.Random(11)
    words = ["alpha", "beta", "`x`", "**b**", "1.", "-", "|", "#", "gamma"]

    def inline() -> str:
        text, spans = parse(" ".join(rng.choice(words) for _ in range(rng.randint(1, 5))))
        return render(text, spans)

    def spec(prev_level: int) -> BlockSpec:
        kind = rng.choice([PARAGRAPH, BULLET, NUMBERED, TABLE, DIAGRAM, CODE_BLOCK, HEADING])
        if kind in (BULLET, NUMBERED):
            return BlockSpec(kind, rng.randint(1, prev_level + 1), inline())
        if kind == HEADING:
            return BlockSpec(HEADING, rng.randint(1, 6), inline())
        if kind == TABLE:
            return BlockSpec(TABLE, text="| a | b |\n|---|---|\n| 1 | 2 |")
        if kind == DIAGRAM:
            return BlockSpec(DIAGRAM, text="flowchart LR\n  a --> b", content=rng.choice([{}, {"derived": True}]))
        if kind == CODE_BLOCK:
            return BlockSpec(CODE_BLOCK, text="x = 1\n\ny = 2", content=rng.choice([{}, {"lang": "python"}]))
        return BlockSpec(PARAGRAPH, text=inline())

    for trial in range(400):
        specs: list[BlockSpec] = []
        for _ in range(rng.randint(0, 8)):
            prev = specs[-1].level if specs and specs[-1].kind in (BULLET, NUMBERED) else 0
            specs.append(spec(prev))
        md = block_md.render(specs)
        assert block_md.parse(md) == specs, (trial, specs, md)
