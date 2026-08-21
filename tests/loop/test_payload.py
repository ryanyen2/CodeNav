"""The prompt budget: what a pass shows the model, and what it gives up first.

Two properties matter more than the arithmetic. The ordinary file must be
untouched — a budget that quietly shortens what every normal pass sees would trade
a pathological case for a regression across the whole corpus. And a cut must read
as a cut: a truncated body that looks like a complete short one is worse than no
body at all, because the model then describes what it thinks it saw.
"""
from __future__ import annotations

from codoc.loop.payload import (
    BUDGET,
    ELISION,
    PER_CHUNK,
    allowances,
    head,
    passes,
    shown_sources,
)


def _sized(paths_to_size: dict[str, int]) -> dict[str, str]:
    """Sources of the given lengths, each a real one-line definition.

    Length is what the budget spends, so a synthetic body of the right size is
    honest input — but the first line has to be a signature, or every case would
    be testing the no-signature fallback instead of the rule.
    """
    out = {}
    for path, size in paths_to_size.items():
        name = path.rsplit("::", 1)[-1].replace(".", "_")
        signature = f"def {name}(self, value):"
        body = "\n".join(f"    step_{i}()" for i in range((size // 14) + 1))
        out[path] = f"{signature}\n{body}\n"
    return out

DEF = '''def send(self, request, timeout=None):
    """Carry a prepared request onto the network."""
    conn = self.get_connection(request.url)
    return conn.urlopen(request.method, request.url, timeout=timeout)
'''

WRAPPED = '''def to_dict(
    self,
    validate: bool = True,
    context: dict | None = None,
) -> dict:
    """Serialize the channel."""
    return {}
'''


# --------------------------------------------------------------- the ordinary case

def test_a_file_that_fits_is_shown_in_full_at_the_usual_allowance():
    sources = _sized({f"m.py::f{i}": 400 for i in range(20)})
    assert set(allowances(sources).values()) == {PER_CHUNK}
    assert shown_sources(sources) == {p: head(s, PER_CHUNK)
                                      for p, s in sources.items()}


def test_a_short_definition_is_passed_through_untouched():
    assert head(DEF, PER_CHUNK) == DEF
    assert ELISION not in head(DEF, PER_CHUNK)


def test_nothing_to_show_is_not_an_error():
    assert allowances({}) == {}


# ----------------------------------------------------------------- what gives way

def test_members_give_way_before_top_level_definitions():
    # 300 methods and 40 classes: the partition is decided at the top level, and a
    # method's body cannot change which feature its class belongs to.
    sizes = {f"m.py::C{i // 8}.meth{i}": 2_000 for i in range(300)}
    sizes.update({f"m.py::C{i}": 2_000 for i in range(40)})
    allowed = allowances(_sized(sizes))
    member = allowed["m.py::C0.meth0"]
    top = allowed["m.py::C0"]
    assert member < top


def test_the_whole_set_is_brought_under_the_budget():
    sources = _sized({f"m.py::C{i // 8}.meth{i}": 4_000 for i in range(800)})
    assert sum(map(len, shown_sources(sources).values())) <= BUDGET


def test_no_symbol_is_reduced_to_nothing():
    # 5,000 chunks is past every rung. Each one still arrives with its signature,
    # so a partition over all of them stays possible — which folding uncovered
    # chunks into the largest node can never recover.
    sources = _sized({f"m.py::C{i // 20}.meth{i}": 9_000 for i in range(5_000)})
    shown = shown_sources(sources)
    assert len(shown) == 5_000
    assert all("def " in text for text in shown.values())


def test_where_the_signatures_alone_exceed_the_budget_the_budget_yields():
    # A generated module of long wrapped signatures. No rung can go below the
    # signature, so the honest floor is what the signatures cost — and cutting
    # them would hand the model names with no parameters, which it cannot use.
    wrapped = {
        f"m.py::C{i // 8}.method{i}": (
            "def method(\n" + "".join(f"    arg_{j}: SomeLongTypeName | None = None,\n"
                                      for j in range(6)) + ") -> dict:\n    return {}\n"
        )
        for i in range(900)
    }
    shown = shown_sources(wrapped)
    spent = sum(map(len, shown.values()))
    assert spent > BUDGET
    assert all(text.count("arg_") == 6 for text in shown.values())


def test_the_rung_is_chosen_against_what_the_set_actually_costs():
    # 400 chunks of 50 chars each would blow every rung if charged at allowance
    # times count, and cost 20,000 characters in fact.
    sources = _sized({f"m.py::f{i}": 50 for i in range(400)})
    assert set(allowances(sources).values()) == {PER_CHUNK}


def test_a_crowded_set_concedes_less_when_its_chunks_are_small():
    # Same symbol count, different bodies. Charged at allowance times count both
    # would concede; charged at what they cost, only the one with real bodies does.
    crowded = _sized({f"m.py::C{i // 8}.meth{i}": 8_000 for i in range(400)})
    small = _sized({f"m.py::C{i // 8}.meth{i}": 100 for i in range(400)})
    assert allowances(small)["m.py::C0.meth0"] == PER_CHUNK
    assert allowances(crowded)["m.py::C0.meth0"] < PER_CHUNK


def test_a_nested_definition_counts_as_a_member():
    sources = _sized({"m.py::outer": 400, "m.py::outer.inner": 400,
                      "m.py::__module__": 400})
    allowed = allowances(sources, budget=400, per_chunk=200)
    assert allowed["m.py::outer.inner"] <= allowed["m.py::outer"]
    # `__module__` is glue, not a member of anything — it is addressed at the top
    # level and shares the top-level allowance.
    assert allowed["m.py::__module__"] == allowed["m.py::outer"]


# ------------------------------------------------------------------ what a cut is

def test_a_cut_says_it_was_cut():
    cut = head(DEF, 60)
    assert cut.endswith(ELISION)


def test_the_signature_survives_even_when_it_alone_is_too_long():
    cut = head(WRAPPED, 20)
    # A name shown without its parameters is worse than a long line: the
    # parameters are what the reader is being asked to recognize.
    assert "def to_dict(" in cut
    assert "validate: bool = True," in cut
    assert cut.endswith("-> dict:" + ELISION)


def test_a_cut_lands_on_a_line_boundary():
    cut = head(DEF, 100)
    for line in cut.replace(ELISION, "").splitlines():
        assert line in DEF


def test_the_docstring_comes_in_when_there_is_room_for_it():
    cut = head(DEF, 130)
    assert "Carry a prepared request onto the network." in cut
    assert "conn.urlopen" not in cut  # …and the body it did not have room for


def test_a_chunk_that_is_not_a_definition_is_still_cut_by_lines():
    glue = "\n".join(f"CONSTANT_{i} = {i}" for i in range(40))
    cut = head(glue, 100)
    assert cut.startswith("CONSTANT_0 = 0")
    assert len(cut) <= 100 + len(ELISION)
    assert cut.endswith(ELISION)


def test_a_decorated_definition_keeps_its_decorator_and_signature():
    src = '@overload\ndef aggregate(self, _: Op, /) -> Angle: ...\n\n@overload\ndef x(): ...\n'
    cut = head(src, 30)
    assert cut.startswith("@overload\ndef aggregate(")


# ---------------------------------------------------------------------------
# When one pass cannot see the set at all (passes)
# ---------------------------------------------------------------------------

def _methods(owner: str, count: int, size: int) -> dict[str, str]:
    """One class's methods, each carrying *size* chars of body."""
    return _sized({f"m.py::{owner}.method_{i:03d}": size for i in range(count)})


def _flat(groups: list[dict[str, str]]) -> list[str]:
    return [path for group in groups for path in group]


def test_a_set_that_fits_is_one_pass_showing_every_definition_whole():
    """The ordinary file, which is nearly every file: one call, nothing cut."""
    sources = _methods("Store", 40, 200)
    assert passes(sources) == [sources]


def test_a_set_too_large_for_one_pass_becomes_several_that_each_fit():
    sources = {}
    for i in range(10):
        sources.update(_methods(f"Class{i}", 30, PER_CHUNK * 2))

    groups = passes(sources)

    assert len(groups) > 1
    for group in groups:
        assert sum(map(len, group.values())) <= BUDGET
    # Every symbol named exactly once, in the order the caller gave them: a split
    # may not drop a definition, and passes over a file should read in its order.
    assert _flat(groups) == list(sources)


def test_no_pass_is_shown_half_a_class():
    """The rule the prompt itself states — a method belongs to its class's feature.

    A pass holding half a class would be asked to name a feature over evidence
    another pass is holding, which is worse than either conceding or splitting.
    """
    sources = {}
    for i in range(10):
        sources.update(_methods(f"Class{i}", 30, PER_CHUNK * 2))

    for group in passes(sources):
        for owner in {p.rsplit("::", 1)[-1].split(".", 1)[0] for p in group}:
            whole = {p for p in sources if p.startswith(f"m.py::{owner}.")}
            assert whole <= set(group), f"{owner} was split across passes"


def test_one_class_too_large_for_any_pass_gets_a_pass_to_itself():
    """A 300-method class is one feature; no split makes it two, so it concedes."""
    sources = dict(_methods("Huge", 300, PER_CHUNK * 2))
    sources.update(_methods("Small", 5, 200))

    groups = passes(sources)

    assert len(groups) == 2
    assert set(groups[0]) == {p for p in sources if "Huge." in p}
    assert set(groups[1]) == {p for p in sources if "Small." in p}
    # And inside that pass the budget does exactly what it did before.
    assert max(map(len, groups[0].values())) < PER_CHUNK


def test_a_pass_is_cut_the_way_a_single_pass_would_have_been():
    """Splitting decides WHICH call sees a symbol, never how its source is cut."""
    sources = _methods("Huge", 300, PER_CHUNK * 2)
    groups = passes(sources)
    assert len(groups) == 1                      # one owner, nowhere to split to
    assert groups[0] == shown_sources(sources)


def test_top_level_definitions_are_each_their_own_owner():
    """A module of free functions has no class to keep whole, so it packs freely."""
    sources = _sized({f"m.py::func_{i:03d}": PER_CHUNK * 2 for i in range(400)})

    groups = passes(sources)

    assert len(groups) > 1
    assert _flat(groups) == list(sources)
    for group in groups:
        assert sum(map(len, group.values())) <= BUDGET


def test_nothing_to_show_is_no_passes_at_all():
    assert passes({}) == []

# --- settings sections ------------------------------------------------------

def _sections(file: str, names: dict[str, int]) -> dict[str, str]:
    """Settings sections of the given sizes, each a real header plus keys."""
    out = {}
    for name, size in names.items():
        keys = "\n".join(f'key_{i} = "value"' for i in range((size // 20) + 1))
        out[f"{file}::{name}"] = f"[{name}]\n{keys}\n"
    return out


def test_a_nested_settings_section_is_not_compressed_as_a_member():
    """`[tool.pytest.ini_options]` is a member of a document, not of a definition.

    Nothing above it states its values, so conceding them first would spend the
    budget on the one thing the tree cannot say without the file — while a method's
    class does state what the method is for, which is what the concession rests on.
    """
    crowd = _methods("Huge", 400, PER_CHUNK * 2)   # forces the ladder down a rung
    crowd.update(_sized({"m.py::Huge": PER_CHUNK * 2}))
    sections = _sections("pyproject.toml", {"tool.pytest.ini_options": 400})
    allowed = allowances({**crowd, **sections})

    top_share = allowed["m.py::Huge"]
    assert allowed["pyproject.toml::tool.pytest.ini_options"] == top_share
    assert allowed["m.py::Huge.method_000"] < top_share


def test_a_settings_file_that_fits_is_shown_whole():
    """The ordinary case, and the one that matters: a config file is small, so the
    reader's `month = "made"` reaches the prompt verbatim."""
    src = '# How a tally lines up its summaries.\n[periods]\nmonth = "made"\n'
    shown = shown_sources({"tally/rules.toml::periods": src})
    assert shown["tally/rules.toml::periods"] == src


def test_a_settings_section_cut_by_the_budget_keeps_its_header():
    """A section shown without its header names nothing — the same reason `head`
    guarantees a signature. `[periods]` is that section's signature."""
    long_section = _sections("rules.toml", {"periods": 4_000})["rules.toml::periods"]
    cut = head(long_section, 200)
    assert cut.startswith("[periods]\n")
    assert cut.endswith(ELISION)
