"""What the bootstrap pass is told when the file it is reading is a notebook.

The adapter gets a notebook's code, its headings and its paragraphs into the
chunks. That is necessary and not sufficient: the pass on the other end still reads
them as a script that happens to contain long strings, and then makes three mistakes
it cannot make on a `.py` file — it paraphrases sentences a PERSON wrote about their
own work, it collapses the sections that person named into one node (the
coarse-grouping rule is right for a module and wrong for an author's own
decomposition), and it reports the shell lines codoc commented out as code somebody
disabled.

So the file kind adds one instruction block. The two properties that matter are that
it is *there* for a notebook and *absent* otherwise, and that it rides in the VOLATILE
tail: a bootstrap wave shares one cached prefix across every file, and a per-file
block in the prefix would split that cache for every notebook in the repo.
"""
from __future__ import annotations

import pytest

from codoc.agent import bootstrap_agent
from codoc.agent.base import load_prompt

CHUNKS = [{"symbol_path": "nb.ipynb::load-the-data", "source": "df = read()"}]


@pytest.fixture
def sent(monkeypatch):
    """Capture the prompt of each bootstrap file call, as (prefix_parts, volatile)."""
    calls: list[tuple[list[str], str]] = []

    def fake_run_agent(prompt, config, *, prefix_parts=None):
        calls.append((list(prefix_parts or []), prompt))
        return {"ops": []}

    monkeypatch.setattr(bootstrap_agent, "run_agent", fake_run_agent)
    return calls


def _ask(sent, file: str) -> tuple[str, str]:
    """Run one file pass; return (the cached prefix, the volatile tail)."""
    bootstrap_agent.propose_file_features(file, CHUNKS, [], [], repo_name="repo")
    assert len(sent) == 1, "one call per file"
    prefix_parts, volatile = sent[0]
    return "".join(prefix_parts), volatile


# ── it is there, and only where it applies ───────────────────────────────────

def test_a_notebook_carries_the_note(sent):
    _, volatile = _ask(sent, "work/churn.ipynb")
    assert "the words in it are the author's" in volatile


def test_a_python_file_carries_nothing_extra(sent):
    """A repo with no notebooks must send the prompt it always sent.

    This is why the note is a per-file block and not a standing "if this file is a
    notebook" paragraph in the instructions: that would spend prefix tokens on a case
    that is usually absent, and ask the model to decide something the path settles.
    """
    prefix, volatile = _ask(sent, "codoc/store/db.py")
    assert "the author's" not in volatile
    assert "the author's" not in prefix
    assert "{notebook_note}" not in volatile, "the placeholder is substituted, not left"


def test_the_note_says_the_three_things_it_exists_to_say(sent):
    _, volatile = _ask(sent, "work/churn.ipynb")
    # the prose is a person's own — carry the meaning, do not paraphrase it
    assert "tradeoff" in volatile
    # the headings are that person's decomposition — follow it past coarse-grouping
    assert "one feature per section" in volatile
    # a `#` in a code cell may be codoc's own doing
    assert "commented out" in volatile and "Never describe them as disabled" in volatile


# ── where it rides ───────────────────────────────────────────────────────────

def test_the_note_is_in_the_volatile_tail_not_the_cached_prefix(sent):
    prefix, volatile = _ask(sent, "work/churn.ipynb")
    assert "the words in it are the author's" in volatile
    assert "the words in it are the author's" not in prefix


def test_a_mixed_wave_keeps_one_byte_identical_prefix(sent):
    """The property the placement exists for.

    Every call in a wave shares the prefix, so a notebook in the repo must not cost
    the `.py` files their cache hit — nor pay twice for its own.
    """
    prefixes = []
    for file in ("codoc/store/db.py", "work/churn.ipynb", "codoc/loop/apply.py",
                 "work/eda.ipynb"):
        bootstrap_agent.propose_file_features(file, CHUNKS, [], [], repo_name="repo")
        prefixes.append("".join(sent[-1][0]))
    assert len(set(prefixes)) == 1


def test_the_note_is_one_file_and_not_a_second_copy_of_the_prompt(sent):
    """It is loaded from `prompts/notebook_note.txt`, so editing that edits the pass."""
    _, volatile = _ask(sent, "work/churn.ipynb")
    note = load_prompt("notebook_note")
    assert note.strip() in volatile
    assert volatile.count("the words in it are the author's") == 1


def test_the_note_lands_above_the_chunks_it_is_about(sent):
    """Read before the data it changes the reading of, like every other framing block."""
    _, volatile = _ask(sent, "work/churn.ipynb")
    assert volatile.index("the words in it are the author's") < volatile.index("### Chunks")
