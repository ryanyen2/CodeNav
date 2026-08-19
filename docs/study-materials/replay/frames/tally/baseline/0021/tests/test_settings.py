"""The rules file, and what happens when it is wrong.

The rules moved out of the code and into rules.toml, which means a typo in a file
can now break a run that used to be impossible to break. These tests are mostly
about that: every mistake has to stop the run and say which rule, rather than
turning into a pattern that quietly matches nothing.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tally import categories, dedupe, money, recurring, settings
from tally.rows import Row

MINIMAL = """
[categories]
uncategorised = "unknown"

[[categories.rules]]
match = "tesco"
category = "groceries"

[transfers]
words = ["transfer"]

[recurring]
months = 3

[money]
flip_when_positive_share_above = 0.8
"""


def rules(tmp_path, text=MINIMAL):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return settings.load(path)


def row(description="TESCO STORES", amount="-10.00", made="2026-01-05"):
    made_date = date.fromisoformat(made)
    return Row(made=made_date, posted=made_date, description=description,
               amount=Decimal(amount))


# ── the file the tool ships with ─────────────────────────────────────────────

def test_the_shipped_rules_load():
    # Guards the file itself: a typo committed to rules.toml breaks every run,
    # and this is the test that catches it before the CLI does.
    loaded = settings.load()
    assert loaded.uncategorised == "uncategorised"
    assert loaded.recurring_months == 3
    assert [name for _, name in loaded.categories][:2] == ["utilities", "fuel"]


def test_the_order_in_the_file_is_the_order_they_are_tried():
    # "shell energy" is above "shell" in rules.toml on purpose, and the loader
    # must not sort or otherwise reorder them: the first match wins, so the order
    # in the file IS the policy.
    names = [pattern.pattern for pattern, _ in settings.load().categories]
    assert names.index("shell energy|british gas|octopus energy") < names.index(
        r"shell|bp\b|esso|texaco"
    )


# ── the rules come from the file, not the code ───────────────────────────────

def test_a_different_file_gives_different_categories(tmp_path):
    # The point of the whole change: a rule set can be swapped without touching
    # a line of code.
    mine = rules(tmp_path,
                 MINIMAL.replace('category = "groceries"', 'category = "the big shop"'))
    assert categories.categorise(row("TESCO STORES"), mine) == "the big shop"


def test_the_name_for_unmatched_comes_from_the_file(tmp_path):
    assert categories.categorise(row("MOONLIGHT RECORDS"), rules(tmp_path)) == "unknown"


def test_the_transfer_wording_comes_from_the_file(tmp_path):
    mine = rules(tmp_path,
                 MINIMAL.replace('words = ["transfer"]', 'words = ["moved to the pot"]'))
    assert dedupe.is_transfer(row("Moved to the pot"), mine)
    assert not dedupe.is_transfer(row("Transfer to savings"), mine)


def test_how_many_months_make_a_payment_recurring_comes_from_the_file(tmp_path):
    two_months = [row("NETFLIX.COM", "-10.99", f"2026-0{m}-09") for m in (1, 2)]
    assert recurring.find(two_months, rules(tmp_path)) == set()
    patient = rules(tmp_path, MINIMAL.replace("months = 3", "months = 2"))
    assert "netflix.com" in recurring.find(two_months, patient)


def test_when_to_flip_the_signs_comes_from_the_file(tmp_path):
    # Half in, half out. At 0.8 nothing is flipped; at 0.4 this file looks like
    # one of the banks that exports spending as positive.
    rows = [row(amount="10.00") for _ in range(5)] + [row(amount="-10.00") for _ in range(5)]
    money.sign_convention(rows, rules(tmp_path))
    assert rows[0].amount == Decimal("10.00")

    rows = [row(amount="10.00") for _ in range(5)] + [row(amount="-10.00") for _ in range(5)]
    money.sign_convention(rows, rules(tmp_path, MINIMAL.replace("0.8", "0.4")))
    assert rows[0].amount == Decimal("-10.00")


# ── when the file is wrong ───────────────────────────────────────────────────

def test_a_pattern_that_does_not_compile_stops_the_run(tmp_path):
    # The alternative is worse than an error: a broken pattern becomes a rule
    # that matches nothing, and a category silently disappears from the summary.
    with pytest.raises(settings.RulesError) as raised:
        rules(tmp_path, MINIMAL.replace('match = "tesco"', 'match = "tesco("'))
    assert "rule 1" in str(raised.value) and "groceries" in str(raised.value)


def test_a_rule_missing_its_category_says_which_rule(tmp_path):
    with pytest.raises(settings.RulesError) as raised:
        rules(tmp_path, MINIMAL.replace('category = "groceries"', ""))
    assert "rule 1" in str(raised.value)


def test_a_file_with_no_rules_in_it_is_an_error(tmp_path):
    # Every transaction would come out uncategorised, which looks like a broken
    # statement rather than a broken rules file.
    with pytest.raises(settings.RulesError):
        rules(tmp_path, MINIMAL.replace('match = "tesco"\ncategory = "groceries"', ""))


@pytest.mark.parametrize("gone", ["[transfers]", "[recurring]", "[money]"])
def test_a_missing_section_says_which_one(tmp_path, gone):
    with pytest.raises(settings.RulesError) as raised:
        rules(tmp_path, MINIMAL.replace(gone, "[unused]"))
    assert gone.strip("[]") in str(raised.value)


@pytest.mark.parametrize("bad", ["months = 0", "months = true", 'months = "three"'])
def test_a_nonsense_recurring_window_is_an_error(tmp_path, bad):
    with pytest.raises(settings.RulesError):
        rules(tmp_path, MINIMAL.replace("months = 3", bad))


@pytest.mark.parametrize("bad", ["1.5", "0", '"most"'])
def test_a_share_outside_nought_to_one_is_an_error(tmp_path, bad):
    with pytest.raises(settings.RulesError):
        rules(tmp_path, MINIMAL.replace("0.8", bad))


def test_a_missing_file_names_the_path(tmp_path):
    with pytest.raises(settings.RulesError) as raised:
        settings.load(tmp_path / "nowhere.toml")
    assert "nowhere.toml" in str(raised.value)


def test_a_file_that_is_not_toml_says_so(tmp_path):
    with pytest.raises(settings.RulesError) as raised:
        rules(tmp_path, "this is not toml{")
    assert "TOML" in str(raised.value)


def test_the_settings_cannot_be_changed_once_read(tmp_path):
    # Frozen on purpose: a rule that could write to the settings it was handed
    # would put the policy back in the code, which is where it just came from.
    with pytest.raises(Exception):
        rules(tmp_path).recurring_months = 12
