"""Surviving the JSON a model actually emits.

Descriptions are prose, and prose has quotation marks in it. Asked to state a
reason, a model writes `the server calls it "stale"` — the inner quote closes
the JSON string and the parser then asks for a comma it will never find. Before
this, that single character cost an entire `codoc init`: nineteen files' worth
of calls paid for and discarded, with an error message pointing at a column
number.

The prompts now ask for quote-free prose, which is the real fix. This is the
net under it.
"""
from __future__ import annotations

import json

import pytest

from codoc.agent.base import parse_solution, repair_json


def _sol(body: str) -> str:
    return f"<solution>\n{body}\n</solution>"


class TestRepair:
    def test_unescaped_quote_inside_a_value(self):
        raw = '{"description": "the server calls it "stale" on retry"}'
        assert json.loads(repair_json(raw))["description"] == \
            'the server calls it "stale" on retry'

    def test_raw_newline_inside_a_value(self):
        raw = '{"description": "first line\nsecond line"}'
        assert json.loads(repair_json(raw))["description"] == "first line\nsecond line"

    def test_already_escaped_quotes_are_left_alone(self):
        raw = '{"description": "he said \\"hi\\" once"}'
        assert json.loads(repair_json(raw))["description"] == 'he said "hi" once'

    def test_valid_json_is_unchanged_in_meaning(self):
        raw = '{"ops": [{"kind": "attach", "feature_id": "f-1", "bindings": [["a.py", "a.py::b"]]}]}'
        assert json.loads(repair_json(raw)) == json.loads(raw)

    def test_quote_before_a_delimiter_still_closes(self):
        """The heuristic must not swallow a legitimate string ending."""
        raw = '{"a": "one", "b": "two"}'
        assert json.loads(repair_json(raw)) == {"a": "one", "b": "two"}

    def test_backslash_at_the_end_does_not_run_off(self):
        repair_json('{"a": "trailing\\')  # must not raise


class TestParseSolution:
    def test_recovers_the_real_bootstrap_failure(self):
        """The shape that killed a `codoc init`: a quoted phrase in a
        description, reported as `Expecting ',' delimiter`."""
        body = ('{"ops": [\n'
                '  {"id": "n1", "kind": "add_node", "title": "Digest auth",\n'
                '   "description": "Signs a request the server calls "stale" so the '
                'retry succeeds.", "bindings": []}\n'
                ']}')
        with pytest.raises(json.JSONDecodeError):
            json.loads(body)
        ops = parse_solution(_sol(body))["ops"]
        assert ops[0]["title"] == "Digest auth"
        assert '"stale"' in ops[0]["description"]

    def test_strict_parse_is_preferred(self):
        """A clean sample must never be rewritten — repair is a rescue path, and
        silently reshaping good output would hide a real prompt regression."""
        body = '{"ops": [{"kind": "attach", "feature_id": "f-1"}]}'
        assert parse_solution(_sol(body)) == json.loads(body)

    def test_repair_applies_inside_a_fenced_block(self):
        body = '```json\n{"description": "calls it "stale""}\n```'
        assert parse_solution(body)["description"] == 'calls it "stale"'

    def test_unrecoverable_output_still_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parse_solution("I'm afraid I can't help with that.")
