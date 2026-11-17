"""The Markdown cases the extension's TypeScript port is tested against: the
Python codecs must give exactly the expected values, so both sides agree."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from codoc.calculus import block_md, inline_md

CASES = json.loads((Path(__file__).resolve().parents[2] / "vscode-codoc/src/test/fixtures/markdown-cases.json").read_text())


@pytest.mark.parametrize("case", CASES["blocks"], ids=lambda c: c["name"])
def test_block_case(case):
    assert [asdict(b) for b in block_md.parse(case["md"], headings=case["headings"])] == case["expect"]


@pytest.mark.parametrize("case", CASES["inline"], ids=lambda c: c["name"])
def test_inline_case(case):
    text, spans = inline_md.parse(case["md"])
    assert (text, [asdict(s) for s in spans]) == (case["text"], case["spans"])
