"""titles_outline — the LLM's duplicate-avoidance context (codoc/agent/base.py)."""
from __future__ import annotations

from codoc.agent.base import titles_outline


def test_outline_marks_planned_placeholders_inline():
    rows = [
        {"id": "f-1", "title": "Transport", "parent_id": None},
        {"id": "f-2", "title": "GitHub link behavior", "parent_id": "f-1", "planned": True},
    ]
    out = titles_outline(rows).splitlines()
    assert out[0] == "- [f-1] Transport"
    assert out[1] == "  - [f-2] GitHub link behavior  (planned — attach its code, don't duplicate)"


def test_outline_marks_planned_orphans_in_the_flat_tail():
    rows = [{"id": "f-9", "title": "Orphan", "parent_id": "f-gone", "planned": True}]
    out = titles_outline(rows)
    assert "(planned — attach its code, don't duplicate)" in out
