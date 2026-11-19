"""How a pass renders what it sends (no model is called)."""
from __future__ import annotations

from codoc.llm.passes import format_brief


def test_a_choice_and_its_cost_render_as_prose_a_description_may_copy():
    text = format_brief({"decisions": [{"choice": "Errors thrown in a hook are not retried",
                                        "gave_up": "These errors do not reach `beforeError` hooks"}],
                         "ordering": [{"before": "Parsing", "then": "validation", "otherwise": "Bad input passes"}]})
    assert "Gives up" not in text and "Otherwise:" not in text
    assert "In exchange, these errors do not reach `beforeError` hooks." in text
    assert "If not, bad input passes." in text
