"""A notebook as chunks: whose words name a section, and what counts as a change.

Four properties carry the adapter. A notebook of plain cells must produce exactly the
chunks the equivalent script would, because a notebook IS Python and a second set of
addresses for the same code would be a second thing to keep in step. A heading must
name the statements under it, because that is the author's own name for the step and
the tree exists to use it. The markdown must reach the chunk, since prose that reaches
no prompt is prose codoc cannot describe from. And identity must move for a reworded
paragraph and stand still for a re-run, because identity is what decides whether Loop A
wakes: a run rewrites most of the file and changes nothing, an edited sentence rewrites
one line and changes what the code is FOR.
"""
from __future__ import annotations

import json

from codoc.lang import detect_language, get_adapter, parses_cleanly
from codoc.lang.notebook import NotebookAdapter, read_cells
from codoc.lang.python import PythonAdapter
from codoc.pipelines.indexing.gate import (
    NOTEBOOK_READ_CEILING_BYTES,
    READ_CEILING_BYTES,
    read_ceiling,
    too_large_to_read,
)

FILE = "work/churn.ipynb"


def _nb(*cells: tuple[str, str], **top: object) -> str:
    """A notebook file, written the way nbformat writes one (a line per list entry)."""
    out = []
    for kind, src in cells:
        lines = src.split("\n")
        lines = [line + "\n" for line in lines[:-1]] + lines[-1:]
        out.append({"cell_type": kind, "source": lines, "metadata": {},
                    "outputs": [], "execution_count": 1})
    doc: dict[str, object] = {"cells": out, "metadata": {},
                              "nbformat": 4, "nbformat_minor": 5}
    doc.update(top)
    return json.dumps(doc, indent=1)


NOTEBOOK = _nb(
    ("markdown", "# Churn model\nFrom the raw export to a fitted model."),
    ("code", "!pip install pandas --quiet\nimport pandas as pd\nfrom pathlib import Path"),
    ("markdown", "## Load the data\nThe export lands in `data/`, one file per month."),
    ("code", "RAW = Path('data')\nframes = [pd.read_csv(p) for p in RAW.glob('*.csv')]"),
    ("code", "df.head?"),
    ("markdown", "## Feature engineering"),
    ("code", "def tenure(row):\n    return (row.last_seen - row.joined).days\n\n"
             "df['tenure'] = df.apply(tenure, axis=1)"),
    ("markdown", "## Train\nOne fold, because the export is small."),
    ("code", "%%bash\necho not python"),
    ("code", "class Model:\n    def fit(self, x, y):\n        self.w = x.T @ y\n"
             "        return self\n\nm = Model().fit(X, y)"),
    ("markdown", "## Train\nAgain, with the engineered column."),
    ("code", "m2 = Model().fit(X2, y)"),
)


def _addresses(source: str = NOTEBOOK, file: str = FILE) -> list[str]:
    return [c.symbol_path.split("::", 1)[1]
            for c in get_adapter("notebook").extract_chunks(file, source)]


def _by_name(source: str = NOTEBOOK) -> dict[str, str]:
    return {c.symbol_path.split("::", 1)[1]: c.source
            for c in get_adapter("notebook").extract_chunks(FILE, source)}


# --------------------------------------------------------------------------
# It is a language, not a second kind of reader
# --------------------------------------------------------------------------

def test_a_notebook_is_a_language_the_registry_knows():
    assert detect_language("nb/train.ipynb") == "notebook"
    assert isinstance(get_adapter("notebook"), NotebookAdapter)


def test_plain_cells_produce_exactly_the_script_s_chunks():
    """No headings, no prose: the same addresses, so nothing has two names."""
    code = "import os\n\ndef go():\n    return os.getcwd()\n\ngo()"
    assert set(_addresses(_nb(("code", code)), "p.ipynb")) == {
        c.symbol_path.split("::", 1)[1]
        for c in PythonAdapter().extract_chunks("p.py", code)
    }


# --------------------------------------------------------------------------
# A heading names the step
# --------------------------------------------------------------------------

def test_a_heading_names_the_statements_under_it():
    names = _addresses()
    assert "load-the-data" in names
    assert "load-the-data.RAW" in names, "a definition is a member of its section"
    assert "feature-engineering.tenure" in names


def test_a_repeated_heading_keeps_its_members_in_their_own_section():
    """`train[1].m2` and not `train.m2` — filing it under the first would be a lie."""
    names = _addresses()
    assert "train" in names and "train[1]" in names
    assert "train.m" in names
    assert "train[1].m2" in names
    assert "train.m2" not in names


def test_sections_are_flat_because_headings_are_an_order_not_a_namespace():
    names = _addresses(_nb(
        ("markdown", "## Data"),
        ("code", "raw = 1"),
        ("markdown", "### Load"),
        ("code", "loaded = 2"),
    ), "s.ipynb")
    assert "load" in names
    assert "data.load" not in names


def test_a_heading_mid_cell_starts_its_section_without_splitting_a_literal():
    names = _addresses(_nb(
        ("markdown", "Intro line.\n\n## Second step\nWhat it does."),
        ("code", "x = 1"),
    ), "m.ipynb")
    assert "second-step" in names
    assert parses_cleanly("m.ipynb", _nb(
        ("markdown", "Intro line.\n\n## Second step\nWhat it does."),
        ("code", "x = 1"),
    ))


def test_a_heading_in_another_script_still_addresses_its_section():
    names = _addresses(_nb(("markdown", "## 加载数据"), ("code", "x = 1")), "z.ipynb")
    assert "加载数据" in names


# --------------------------------------------------------------------------
# The prose reaches the chunk
# --------------------------------------------------------------------------

def test_the_markdown_arrives_in_the_chunk_it_explains():
    """Written as a string and not a comment, or no prompt would ever see it."""
    section = _by_name()["load-the-data"]
    assert "## Load the data" in section
    assert "one file per month" in section


def test_a_section_of_only_prose_is_still_a_chunk():
    """`## Train`'s one cell is shell, so the prose is all there is — and it binds."""
    assert "One fold, because the export is small." in _by_name()["train"]


def test_prose_is_carried_through_unescaped():
    body = "## Notes\nA path like C:\\Users\\me and a `quote` kept as written."
    section = _by_name(_nb(("markdown", body), ("code", "x = 1")))["notes"]
    assert "C:\\Users\\me" in section


def test_prose_a_literal_cannot_hold_falls_back_without_taking_the_code_with_it():
    """Both triple-quote styles in one paragraph: nothing wraps it, so it goes as
    comments. The section keeps its name and its statements — only that block's prose
    stops reaching a prompt, which is the whole cost of the fallback and is why the
    fallback is per BLOCK rather than per cell or per notebook."""
    body = '## Notes\nIt said """this""" and then \'\'\'that\'\'\'.'
    source = _nb(("markdown", body), ("code", "x = 1"))
    assert parses_cleanly("q.ipynb", source) is True
    assert _addresses(source, "q.ipynb") == ["notes.x"]
    assert "It said" in get_adapter("notebook").synthetic_source(source)



# --------------------------------------------------------------------------
# Identity: the cells' source and nothing else
# --------------------------------------------------------------------------

def test_running_the_notebook_is_not_a_change():
    ran = json.loads(NOTEBOOK)
    for cell in ran["cells"]:
        cell["execution_count"] = 99
        cell["outputs"] = [{"output_type": "display_data",
                            "data": {"image/png": "iVBOR" + "A" * 4000}}]
    ran = json.dumps(ran, indent=1)
    adapter = get_adapter("notebook")
    assert adapter.token_stream(ran) == adapter.token_stream(NOTEBOOK)
    assert _by_name(ran) == _by_name()


def test_a_reworded_paragraph_is_a_change():
    """The inverse of the comment rule, and deliberately so: here the prose is intent."""
    edited = json.loads(NOTEBOOK)
    edited["cells"][2]["source"] = ["## Load the data\n", "One file per WEEK now.\n"]
    edited = json.dumps(edited, indent=1)
    adapter = get_adapter("notebook")
    assert adapter.token_stream(edited) != adapter.token_stream(NOTEBOOK)


def test_a_chunk_s_own_source_hashes_as_the_python_it_is():
    """What the identity walk is handed is a chunk, not a file. It must parse."""
    adapter = get_adapter("notebook")
    for source in _by_name().values():
        assert adapter.token_stream(source), source
        assert adapter.token_stream(source) == PythonAdapter().token_stream(source)


# --------------------------------------------------------------------------
# IPython that is not Python
# --------------------------------------------------------------------------

def test_a_shell_line_does_not_make_the_notebook_read_as_damaged():
    assert parses_cleanly(FILE, NOTEBOOK) is True
    assert "import pandas as pd" in _by_name()["churn-model"]


def test_a_cell_magic_owns_its_cell_and_contributes_nothing():
    assert "echo not python" not in "\n".join(_by_name().values())


def test_a_cell_that_cannot_parse_is_commented_out_whole():
    source = _nb(("code", "def broken(:\n    pass"), ("code", "kept = 1"))
    assert parses_cleanly("b.ipynb", source) is True
    assert _addresses(source, "b.ipynb") == ["kept"]


def test_a_notebook_whose_json_cannot_be_read_reports_damage():
    """Not emptiness — emptiness retires every feature bound to the file."""
    broken = '{"cells": [{"cell_type": "cod'
    assert parses_cleanly("x.ipynb", broken) is False
    assert get_adapter("notebook").extract_chunks("x.ipynb", broken) == []


def test_a_file_that_is_not_a_notebook_at_all_is_unjudged_rather_than_damaged():
    assert parses_cleanly("notes.md", "# hello") is None


# --------------------------------------------------------------------------
# Reading the file: cells, offsets, and the ceiling
# --------------------------------------------------------------------------

def test_nbformat_three_is_read_including_its_heading_cells():
    old = json.dumps({"nbformat": 3, "worksheets": [{"cells": [
        {"cell_type": "heading", "level": 2, "source": ["Setup"]},
        {"cell_type": "code", "input": ["import sys\n", "sys.path.append('.')"]},
    ]}]})
    assert [c.kind for c in read_cells(old) or []] == ["markdown", "code"]
    assert _addresses(old, "old.ipynb") == ["setup"]


def test_a_source_list_without_newlines_is_joined_rather_than_collapsed():
    hand_rolled = json.dumps({"nbformat": 4, "cells": [
        {"cell_type": "code", "source": ["import os", "def go():", "    return os"]},
    ]})
    assert "go" in _addresses(hand_rolled, "h.ipynb")


def test_offsets_lead_back_into_the_file_in_reading_order():
    """Bootstrap reads a file's chunks by start_byte, so order is the notebook's."""
    chunks = get_adapter("notebook").extract_chunks(FILE, NOTEBOOK)
    starts = [c.start_byte for c in chunks]
    assert starts == sorted(starts)
    assert all(c.end_byte > c.start_byte for c in chunks)
    assert all(c.start_byte < len(NOTEBOOK) for c in chunks)
    assert NOTEBOOK[chunks[0].start_byte:].startswith("# Churn model")


def test_a_symbol_resolves_to_where_it_is_written():
    start, end = get_adapter("notebook").resolve_symbol_path(
        NOTEBOOK, f"{FILE}::feature-engineering.tenure")
    assert NOTEBOOK[start:].startswith("def tenure(row):")
    assert end > start


def test_the_read_ceiling_is_per_kind_because_a_notebook_s_bytes_are_output():
    plots = 12_000_000
    assert too_large_to_read(plots, ceiling=read_ceiling("nb/figures.ipynb")) is False
    assert too_large_to_read(plots, ceiling=read_ceiling("codoc/big.py")) is True
    assert read_ceiling("a.IPYNB") == NOTEBOOK_READ_CEILING_BYTES
    assert read_ceiling("a.py") == READ_CEILING_BYTES
    assert NOTEBOOK_READ_CEILING_BYTES > READ_CEILING_BYTES
