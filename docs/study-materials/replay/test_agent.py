"""The participant's first turn.

The session's whole design rests on the participant believing they asked for the
change, so the parts that could give it away are the parts worth testing: the
opening screen has to be the machine's own, the request has to render the way one
does, and the workspace has to be one nobody has already played into.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
import play as player  # noqa: E402


# ── the request, as it is typed ──────────────────────────────────────────────

def test_wrap_breaks_on_words():
    lines = agent.wrap("the quick brown fox jumps over the lazy dog", 12)
    assert all(len(line) <= 12 for line in lines)
    assert all(not line.startswith(" ") for line in lines), (
        "a continuation line starting with a space moves the left edge of the "
        "text from line to line, which no input box does")
    assert " ".join(lines) == "the quick brown fox jumps over the lazy dog"


def test_wrap_splits_a_word_with_nowhere_to_break():
    lines = agent.wrap("supercalifragilistic", 8)
    assert lines == ["supercal", "ifragili", "stic"]


def test_wrap_of_nothing_is_nothing():
    assert agent.wrap("", 20) == []


def test_every_row_of_the_box_is_the_same_width():
    # The rows are built from the wrap, so a wrap that returns a line one longer
    # than the inner width pushes that row's right border out by one and the box
    # stops being a box.
    width = 30
    text = "add a config file so the rules can be changed"
    rows = ["│ " + (("> " if n == 0 else "  ") + line).ljust(width - 3) + "│"
            for n, line in enumerate(agent.wrap(text, width - 4))]
    assert rows, "the request did not render at all"
    assert all(len(row) == width for row in rows)


# ── the opening screen ───────────────────────────────────────────────────────

def test_the_banner_keeps_what_was_drawn_above_the_input_box():
    # The box is redrawn on every keystroke, so it has to be ours. Everything
    # above it is drawn once and is the part worth keeping.
    raw = ("welcome\n".encode() + "╭────╮\n│ >  │\n╰────╯".encode())
    assert agent.split_banner(raw) == b"welcome\n"


def test_a_screen_with_no_box_is_kept_whole():
    assert agent.split_banner(b"welcome\n") == b"welcome\n"


def test_nothing_captured_is_reported_rather_than_written(tmp_path):
    assert agent.split_banner(b"") is None


def test_a_first_run_screen_is_not_kept_as_a_welcome():
    # The first time the assistant is run in a fresh config directory it asks
    # about a theme and a login rather than drawing a welcome. Keeping that would
    # put a setup question in front of a participant at the moment they are
    # supposed to be asking for a change.
    assert agent.looks_unfinished(b"Let's get started.\n\nChoose the text style")
    assert agent.looks_unfinished(b"Select login method")
    # The real thing, with the escape codes the assistant actually emits between
    # words, is not a first-run screen.
    assert not agent.looks_unfinished(
        b"\x1b[38;5;209m\xe2\x9c\xbb\x1b[0m Welcome to Claude Code\n  cwd: /x")


def test_the_fallback_names_the_workspace():
    # Drawn only when the capture failed. It exists so a machine whose capture
    # did not work still runs a session, rather than starting with a traceback
    # in front of a participant.
    assert "{cwd}" in agent.FALLBACK
    assert "Welcome" in agent.FALLBACK


def test_a_captured_screen_is_preferred_to_the_fallback(tmp_path, capsysbinary):
    profile = tmp_path / agent.PROFILE
    profile.mkdir()
    (profile / agent.WELCOME).write_bytes(b"this machine's own welcome")
    agent.show_banner(tmp_path)
    assert b"this machine's own welcome" in capsysbinary.readouterr().out


# ── the handover ─────────────────────────────────────────────────────────────

def test_the_session_id_comes_from_the_recorded_transcript(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "transcript.jsonl").write_text(
        json.dumps({"cwd": "/somewhere", "sessionId": "abc-123"}) + "\n")
    assert agent.session_id(frames) == "abc-123"


def test_the_recorded_session_lands_where_the_study_assistant_looks(
        tmp_path, monkeypatch):
    # The launcher points the assistant at the workspace's own config directory,
    # which is what keeps the study off the participant's account. A session
    # installed in the home directory instead is one `--continue` cannot see, so
    # the turn after the recording would start with none of the change's
    # context, and carrying that context is the reason it is installed at all.
    profile = tmp_path / "ws" / agent.PROFILE
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(profile))
    assert player.config_dir() == profile

    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert player.config_dir() == Path.home() / ".claude"


def test_no_transcript_means_no_session_to_resume(tmp_path):
    assert agent.session_id(tmp_path) is None


def test_a_missing_recording_is_refused_rather_than_half_played(tmp_path):
    # A workspace with no frames would otherwise draw the welcome, take the
    # request and then fail, with the participant having asked for something
    # that never arrives.
    assert agent.first_turn(tmp_path, tmp_path / "nothing", 1.0) == 2


def test_stopping_a_daemon_that_is_not_running_is_not_a_failure(tmp_path):
    (tmp_path / ".codoc").mkdir()
    assert agent.stop_daemon(tmp_path) is False


def test_the_workspace_is_claimed_and_given_back(tmp_path):
    # The daemon is the editor's to run, not ours. Killing it from outside left
    # the extension believing it still had one and starting our own behind its
    # back gave the workspace two writers, which is how a tree fills with
    # proposals nobody asked for. The lock is the whole protocol.
    (tmp_path / ".codoc").mkdir()
    lock = agent.hand_over(tmp_path, timeout=0.5)
    assert lock.exists()
    agent.hand_back(tmp_path)
    assert not lock.exists()


def test_giving_it_back_twice_is_not_a_failure(tmp_path):
    (tmp_path / ".codoc").mkdir()
    agent.hand_back(tmp_path)
    agent.hand_back(tmp_path)


def test_the_lock_is_never_recorded_into_a_frame(tmp_path):
    # `reset()` deletes what the base state does not have, so a lock the scan
    # could see would be deleted mid-replay and the daemon would come back while
    # the player was still writing.
    import record
    (tmp_path / ".codoc").mkdir()
    (tmp_path / ".codoc" / agent.LOCK).write_text("{}")
    assert ".codoc/" + agent.LOCK not in record.scan(tmp_path)


@pytest.mark.parametrize("arm", ["codoc", "baseline"])
def test_both_arms_have_a_recording_to_play(arm):
    # The launcher only takes the first turn when a manifest is there, so a
    # missing one is a silent fallback to a live agent making its own change,
    # which every participant would then review a different version of.
    root = Path(__file__).resolve().parent / "frames"
    for project in ("scribe", "tally"):
        assert (root / project / arm / "manifest.json").exists(), (
            f"{project}/{arm} has no recording")
