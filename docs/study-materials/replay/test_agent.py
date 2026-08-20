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

# docs/study-materials/replay → the repository root.
REPO = Path(__file__).resolve().parents[3]


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


# ── the session, in stages ───────────────────────────────────────────────────

def test_a_recording_with_no_checkpoints_is_one_segment():
    # Every recording made before this existed has none, and they must keep
    # playing exactly as they did.
    assert player.segments({"frames": [{}] * 10}) == [(0, 10)]


def test_checkpoints_cut_the_frames_into_segments():
    assert player.segments({"frames": [{}] * 10, "checkpoints": [4, 7]}) \
        == [(0, 4), (4, 7), (7, 10)]


def test_a_checkpoint_outside_the_recording_is_ignored():
    # 0 would make an empty first segment and 10 an empty last one, and an empty
    # segment is a pause with nothing on either side of it.
    assert player.segments({"frames": [{}] * 10, "checkpoints": [0, 10, 4]}) \
        == [(0, 4), (4, 10)]


def sidecar(tmp_path, *, proposals=(), auto_edits=()):
    """A sidecar in the shape `codoc_file/render.py` actually writes.

    Written out rather than abbreviated, because the abbreviation is what hid the
    bug: `by_event` is nested under `proposals`, the player read it from the top
    level, and so `pending_proposals` returned 0 whatever was outstanding and no
    checkpoint ever waited for anybody. A fixture that agrees with the reader
    instead of with the writer proves nothing about the two meeting.
    """
    codoc = tmp_path / ".codoc"
    codoc.mkdir(exist_ok=True)
    path = codoc / "tree.bindings.json"
    path.write_text(json.dumps({
        "version": 6,
        "proposals": {"by_feature": {}, "by_event": {e: {} for e in proposals},
                      "by_parent": {}},
        "auto_edits": {f: {"at": "x", "prev": "y"} for f in auto_edits},
    }))
    return path


def test_the_sidecar_fixture_matches_what_codoc_writes():
    # The shape above is asserted against the renderer that produces it, so a
    # rename there fails here rather than silently restoring "nothing is ever
    # pending" — which is a checkpoint that does not stop, in a study whose whole
    # point is the moment it stops at.
    render = (REPO / "codoc" / "codoc_file" / "render.py").read_text()
    assert '"proposals": _proposals_map(' in render
    assert '"auto_edits": _auto_edits(' in render
    assert 'return {"by_feature": by_feature, "by_event": by_event' in render


def _verdict(tmp_path, accept):
    with (tmp_path / ".codoc" / "inbox.host.jsonl").open("a") as fh:
        fh.write(json.dumps({"event_id": "e-1", "accept": accept}) + "\n")


def test_waiting_ends_when_a_proposal_is_answered(tmp_path, monkeypatch):
    bindings = sidecar(tmp_path, proposals=["e-1", "e-2"])
    assert agent.pending_proposals(tmp_path) == 2

    # One answered is enough: the participant has engaged with the plan, which is
    # what the checkpoint is for. Holding out for ALL of them would stall on a
    # proposal somebody deliberately left alone.
    calls = {"n": 0}

    def answer_after_one_poll(_s: float) -> None:
        calls["n"] += 1
        bindings.write_text(json.dumps({
            "proposals": {"by_event": {"e-2": {}}}, "auto_edits": {}}))

    monkeypatch.setattr(agent.time, "sleep", answer_after_one_poll)
    assert agent.wait_for_an_answer(tmp_path, "plan?", timeout=5.0) == agent.ANSWERED
    assert calls["n"] == 1


def test_a_rejection_is_not_an_answer_to_carry_on_from(tmp_path, monkeypatch):
    # Everything after the cut was recorded against a store in which the plan is
    # LIVE, and the checkpoint frame carries the store — so playing on after a
    # rejection reinstates the plan the participant just turned down, quietly. They
    # would watch their own decision be undone, and the record would say they
    # accepted a plan they rejected.
    sidecar(tmp_path, proposals=["e-1"])

    def reject_after_one_poll(_s: float) -> None:
        _verdict(tmp_path, False)

    monkeypatch.setattr(agent.time, "sleep", reject_after_one_poll)
    assert agent.wait_for_an_answer(tmp_path, "plan?", timeout=5.0) == agent.DIVERGED


def test_an_accept_read_from_either_channel_counts(tmp_path, monkeypatch):
    # The editor APPENDS to inbox.host.jsonl because it holds no cross-process
    # lock; the daemon folds that into inbox.json under the lock. Reading only one
    # of them misses a verdict for as long as the fold takes.
    sidecar(tmp_path, proposals=["e-1"])
    (tmp_path / ".codoc" / "inbox.json").write_text(
        json.dumps({"verdicts": [{"event_id": "e-1", "accept": True}]}))
    assert agent.verdicts(tmp_path) == [True]
    _verdict(tmp_path, True)
    assert agent.verdicts(tmp_path) == [True, True]


def test_a_stop_with_nothing_in_it_is_not_a_stop(tmp_path):
    sidecar(tmp_path)
    assert agent.wait_for_an_answer(tmp_path, "plan?", timeout=0.1) == agent.NOTHING


def test_an_unanswered_rewrite_is_something_to_wait_for(tmp_path):
    # The second checkpoint is not about proposals. The loop has already REWRITTEN
    # the descriptions of the features the build touched — applied, not proposed —
    # and what is outstanding is the Keep / Restore verdict on each. Counting only
    # proposals made that stop pass straight through the thing it exists for.
    sidecar(tmp_path, auto_edits=["f-1", "f-2", "f-3"])
    assert agent.pending_proposals(tmp_path) == 3


def test_a_session_that_is_never_answered_carries_on(tmp_path, monkeypatch):
    # A study that hangs forever because somebody did not click is worse than one
    # that goes on without the answer.
    sidecar(tmp_path, proposals=["e-1"])
    monkeypatch.setattr(agent.time, "sleep", lambda _s: None)
    assert agent.wait_for_an_answer(tmp_path, "plan?", timeout=0.01) == agent.UNANSWERED


# ── what the agent says at each stop ─────────────────────────────────────────

def test_one_says_is_used_at_every_stop():
    # What every recording made before two stops existed means by a single string.
    assert agent.checkpoint_texts({"checkpoint_says": "answer it"}, 2) \
        == ["answer it", "answer it"]


def test_each_stop_gets_its_own_words():
    # Two stops ask two different questions — "here is the plan" and "here is what
    # the build did to the descriptions". Repeating the first at the second would
    # send the participant back to a decision already behind them.
    assert agent.checkpoint_texts({"checkpoint_says": ["the plan", "the diffs"]}, 2) \
        == ["the plan", "the diffs"]


def test_a_stop_with_nothing_said_still_says_something():
    assert agent.checkpoint_texts({}, 1) == [agent.DEFAULT_ASK]


def test_a_derived_recording_keeps_its_checkpoints():
    # `derive` copies everything but the frames through from the source manifest,
    # which is how a checkpoint set on the neutral recording reaches both arms.
    # Set it after deriving and the two conditions can disagree about where the
    # session pauses, which is a difference between the arms that is not the
    # manipulation.
    src = (Path(__file__).resolve().parent / "record.py").read_text()
    assert '{k: v for k, v in manifest.items() if k != "frames"}' in src
    assert 'manifest.get("checkpoints"' in src


def test_the_store_is_carried_at_a_checkpoint():
    # The daemon comes back at a stop and projects the tree from the store. The
    # store is otherwise carried once at the end, so a stop without it shows a
    # tree with none of the plan in it.
    src = (Path(__file__).resolve().parent / "record.py").read_text()
    assert "scan(workspace, with_index=at_stop)" in src
