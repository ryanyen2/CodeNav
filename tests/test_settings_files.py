"""Settings files as chunks: what a section is, and what counts as a change.

Two properties carry the unit. A section must arrive WITH the comments above it,
because in a settings file that is where the reasoning is written and quoting the
value without it is quoting a number. And identity must move on a changed value and
stay still for a reformat, because identity is what decides whether Loop A wakes.
"""
from __future__ import annotations

from codoc.settings_files import (
    MODULE_CHUNK,
    available_formats,
    detect_format,
    extract_chunks,
    hashes,
    is_settings_file,
    parses_cleanly,
    resolve_symbol_path,
)

RULES = '''# How a tally lines up its summaries.
version = 2

# Three months rather than two, so a coincidence does not become a commitment.
[periods]
month = "made"
week = "posted"

[periods.week]
starts = "monday"

# An unmatched merchant stops the run: a silent bucket hides the one row a
# reviewer needed to see.
[merchants]
unmatched = "stop"
'''


def _by_name(file: str, source: str) -> dict[str, str]:
    return {c.symbol_path.split("::", 1)[1]: c.source
            for c in extract_chunks(file, source)}


# ---------------------------------------------------------------------------
# What a section is
# ---------------------------------------------------------------------------

def test_each_table_is_a_chunk_and_a_nested_one_is_its_member():
    """The same owner/member relation a class and its methods have, so everything
    downstream that reads a dotted path works on settings unchanged."""
    names = list(_by_name("tally/rules.toml", RULES))
    assert names == [MODULE_CHUNK, "periods", "periods.week", "merchants"]


def test_a_sections_comments_come_with_it():
    got = _by_name("tally/rules.toml", RULES)
    assert "a coincidence does not become a commitment" in got["periods"]
    assert "a silent bucket hides the one row" in got["merchants"]


def test_a_comment_run_is_claimed_by_one_section_only():
    """The sentence above `[merchants]` explains `[merchants]`, and reading it into
    the table before it would attribute a reason to the wrong decision."""
    got = _by_name("tally/rules.toml", RULES)
    assert "a silent bucket" not in got["periods.week"]
    assert "a coincidence" not in got[MODULE_CHUNK]


def test_the_keys_before_the_first_section_are_the_files_own():
    got = _by_name("tally/rules.toml", RULES)
    assert "version = 2" in got[MODULE_CHUNK]
    assert "How a tally lines up its summaries" in got[MODULE_CHUNK]


def test_a_flat_file_is_one_addressable_chunk():
    """Honest answer for a settings file with no sections: still quotable, still
    bindable, rather than not indexed at all."""
    got = _by_name("app.toml", 'debug = true\nretries = 3\n')
    assert list(got) == [MODULE_CHUNK]
    assert "retries = 3" in got[MODULE_CHUNK]


def test_an_empty_file_has_nothing_to_bind():
    assert extract_chunks("app.toml", "\n\n") == []


def test_a_chunks_bytes_are_where_it_is_in_the_file():
    for chunk in extract_chunks("tally/rules.toml", RULES):
        assert RULES[chunk.start_byte:chunk.end_byte] == chunk.source


def test_the_chunks_cover_the_file_without_overlapping():
    """Every byte of a settings file lands in exactly one section, so a decision
    cannot be lost between two of them."""
    chunks = extract_chunks("tally/rules.toml", RULES)
    assert chunks[0].start_byte == 0
    assert chunks[-1].end_byte == len(RULES)
    for earlier, later in zip(chunks, chunks[1:]):
        assert earlier.end_byte == later.start_byte


def test_ini_sections_are_chunks_and_semicolons_are_comments():
    ini = "; why this exists\n[server]\nport = 8080\n\n[client]\nretries = 2\n"
    got = _by_name("app.cfg", ini)
    assert list(got) == ["server", "client"]
    assert "why this exists" in got["server"]


def test_yaml_top_level_keys_are_chunks():
    yml = "# the queue policy\nqueue:\n  workers: 4\n\nlogging:\n  level: info\n"
    got = _by_name("deploy.yaml", yml)
    assert list(got) == ["queue", "logging"]
    assert "the queue policy" in got["queue"]
    assert "workers: 4" in got["queue"]


def test_json_top_level_keys_are_chunks_and_the_brace_is_not_one():
    doc = '{\n  "queue": {\n    "workers": 4\n  },\n  "level": "info"\n}\n'
    got = _by_name("deploy.json", doc)
    assert list(got) == ["queue", "level"]
    assert MODULE_CHUNK not in got
    assert '"workers": 4' in got["queue"]


def test_a_minified_json_file_is_one_chunk_rather_than_a_misnamed_one():
    """A line is the unit here, so a file whose keys share one line is not
    addressable by section — and naming the whole file after its last key would be
    worse than naming it after the file."""
    got = _by_name("app.json", '{"queue": {"workers": 4}, "level": "info"}\n')
    assert list(got) == [MODULE_CHUNK]
    assert '"level": "info"' in got[MODULE_CHUNK]


def test_a_brace_inside_a_json_value_does_not_invent_a_section():
    doc = '{\n  "template": "{name} paid {amount}",\n  "retries": 2\n}\n'
    assert list(_by_name("app.json", doc)) == ["template", "retries"]


def test_an_array_of_tables_gives_one_chunk_per_entry():
    """`[[servers]]` repeats its header by design, and two chunks may not share a
    symbol path — the index keys on it, so a repeat would cost the whole file."""
    doc = '[[servers]]\nhost = "a"\n\n[[servers]]\nhost = "b"\n'
    got = _by_name("deploy.toml", doc)
    assert list(got) == ["servers", "servers[1]"]
    assert 'host = "b"' in got["servers[1]"]


def test_a_section_is_found_again_after_the_file_moves_around_it():
    """What keeps a binding alive across an edit: the table moved, the feature still
    points at it."""
    moved = "[merchants]\nunmatched = \"stop\"\n\n[periods]\nmonth = \"made\"\n"
    span = resolve_symbol_path(moved, "rules.toml::periods")
    assert span is not None
    assert moved[span[0]:span[1]].startswith("[periods]")


def test_a_section_that_is_gone_resolves_to_nothing():
    assert resolve_symbol_path("[periods]\n", "rules.toml::merchants") is None


# ---------------------------------------------------------------------------
# Which files are settings at all
# ---------------------------------------------------------------------------

def test_the_formats_are_the_ones_this_process_can_read():
    assert {"toml", "json", "ini"} <= available_formats()
    assert detect_format("a/b/rules.toml") == "toml"
    assert detect_format("codoc/loop/apply.py") is None


def test_packaging_and_lock_files_are_not_decisions_anybody_authored():
    """A tree that described them would spend its first nodes on the build."""
    assert not is_settings_file("pyproject.toml")
    assert not is_settings_file("uv.lock")
    assert not is_settings_file("web/package-lock.json")
    assert not is_settings_file("web/tsconfig.json")
    assert is_settings_file("tally/rules.toml")


# ---------------------------------------------------------------------------
# What counts as a change
# ---------------------------------------------------------------------------

def test_a_changed_value_is_a_changed_decision():
    before = hashes('month = "made"\n', "toml")
    after = hashes('month = "posted"\n', "toml")
    assert before[0] != after[0]


def test_a_re_tuned_value_leaves_the_files_shape_alone():
    """`types_hash` is the key paths, so it separates a re-tuning from a rewrite —
    the same split it has for code, where a body change is not a rename."""
    before = hashes('month = "made"\n', "toml")
    after = hashes('month = "posted"\n', "toml")
    assert before[1] == after[1]


def test_a_renamed_section_moves_both_signals():
    before = hashes('[periods]\nmonth = "made"\n', "toml")
    after = hashes('[windows]\nmonth = "made"\n', "toml")
    assert before[0] != after[0]
    assert before[1] != after[1]


def test_reordering_two_settings_is_not_a_change():
    """A formatter pass must not wake Loop A: it changed no decision."""
    one = hashes('month = "made"\nweek = "posted"\n', "toml")
    other = hashes('week = "posted"\nmonth = "made"\n', "toml")
    assert one == other


def test_reflowing_a_comment_is_not_a_change():
    with_comment = hashes('# three months, not two\nmonth = "made"\n', "toml")
    without = hashes('month = "made"\n', "toml")
    assert with_comment == without


def test_a_half_written_file_is_hashed_by_its_words_not_refused():
    """Mid-edit is a state a person passes through; the next save corrects it."""
    broken = 'month = "made\n[unclosed\n'
    tokens, types = hashes(broken, "toml")
    assert len(tokens) == 64
    assert hashes(broken, "toml") == (tokens, types)      # and it is stable
    assert hashes('month = "made"\n', "toml")[0] != tokens


def test_a_section_parses_on_its_own():
    """Each chunk is judged alone, because that is the unit the pipeline holds."""
    for chunk in extract_chunks("tally/rules.toml", RULES):
        assert parses_cleanly(chunk.source, "toml")


def test_a_json_member_is_judged_inside_its_object():
    """One member of an object is not a document, but it is a chunk."""
    doc = '{\n  "queue": {\n    "workers": 4\n  }\n}\n'
    (member,) = extract_chunks("deploy.json", doc)
    assert parses_cleanly(member.source, "json")


def test_a_damaged_section_says_so():
    assert not parses_cleanly('month = "made\n', "toml")
