#!/usr/bin/env python3
"""Tests for the replay harness.

    python3 docs/study-materials/replay/test_replay.py

The test that matters is the round trip. Replaying the frames into a clean
workspace has to reproduce the state the recording ended in, file for file,
because a participant reviews the replayed state and the planted problems are
rated against the recorded one.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import play  # noqa: E402
import record  # noqa: E402


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fake_recording(raw: Path, steps: list[dict]) -> None:
    """Write the snapshots `watch` would have written for a session."""
    stage = raw / "_stage"
    stage.mkdir(parents=True, exist_ok=True)
    base = steps[0]
    for rel, text in base["files"].items():
        write(stage, rel, text)
    (raw / "base.json").write_text(json.dumps(record.scan(stage), indent=2))
    subprocess.run(["cp", "-R", str(stage), str(raw / "base")], check=True)

    for i, step in enumerate(steps[1:], start=1):
        snap = raw / f"{i:04d}"
        for rel, text in step["files"].items():
            write(stage, rel, text)
            write(snap, rel, text)
        for rel in step.get("deletes", []):
            (stage / rel).unlink(missing_ok=True)
        (raw / f"{i:04d}.json").write_text(json.dumps({
            "n": i,
            "at_s": step["at_s"],
            "writes": sorted(step["files"]),
            "deletes": sorted(step.get("deletes", [])),
        }, indent=2))


SESSION = [
    {"at_s": 0, "files": {
        "scribe/lines.py": "one\n",
        "scribe/notes.py": "notes\n",
        ".codoc/tree.doc.json": '{"features": 1}',
        ".venv/ignored.py": "never copied\n",
    }},
    {"at_s": 40, "files": {"scribe/config.py": "config\n"}},
    {"at_s": 90, "files": {
        "scribe/lines.py": "one\ntwo\n",
        ".codoc/tree.doc.json": '{"features": 2}',
    }},
    {"at_s": 200, "files": {"scribe/notes.py": "renumbered\n"},
     "deletes": ["scribe/config.py"]},
]


class ScanTest(unittest.TestCase):
    def test_skips_environments_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "scribe/lines.py", "code\n")
            write(root, ".venv/lib/thing.py", "vendored\n")
            write(root, "scribe/__pycache__/lines.pyc", "bytes\n")
            write(root, ".git/HEAD", "ref\n")
            self.assertEqual(sorted(record.scan(root)), ["scribe/lines.py"])

    def test_index_directories_are_out_unless_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".codoc/tree.doc.json", "{}")
            write(root, ".codoc/lancedb/chunks.lance", "binary\n")
            self.assertEqual(sorted(record.scan(root)), [".codoc/tree.doc.json"])
            self.assertEqual(len(record.scan(root, with_index=True)), 2)


class SecretTest(unittest.TestCase):
    def test_no_key_file_can_reach_a_frame(self):
        # A recording is copied into every participant's workspace and collected
        # back, so a key in a frame is handed out twelve times and returns.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "scribe/lines.py", "code\n")
            write(root, ".env", "ANTHROPIC_API_KEY=sk-secret\n")
            write(root, ".claude-study/api-key", "sk-also-secret\n")
            write(root, "certs/server.pem", "-----BEGIN\n")
            self.assertEqual(sorted(record.scan(root)), ["scribe/lines.py"])
            self.assertEqual(sorted(record.scan(root, with_index=True)), ["scribe/lines.py"])


class TransientTest(unittest.TestCase):
    def test_no_process_state_reaches_a_frame(self):
        # SQLite's write-ahead log is meaningless without the database it was
        # written beside, and the first derivation captured one on its own. A
        # recorded pid file is worse: the player refuses to run while a daemon
        # owns the workspace, and it would be refusing because of a process that
        # died weeks earlier on somebody else's machine.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".codoc/tree.doc.json", "{}")
            write(root, ".codoc/codoc.db", "sqlite")
            write(root, ".codoc/codoc.db-wal", "log")
            write(root, ".codoc/codoc.db-shm", "shared")
            write(root, ".codoc/loop.lock", "")
            write(root, ".codoc/watch.pid", '{"pid": 1}')
            # The store itself is carried once, into the last frame, so a
            # per-frame scan does not list it either.
            self.assertEqual(sorted(record.scan(root)), [".codoc/tree.doc.json"])
            self.assertEqual(sorted(record.scan(root, with_index=True)),
                             [".codoc/codoc.db", ".codoc/tree.doc.json"])


class BuildTest(unittest.TestCase):
    def build(self, tmp: Path, seconds: float = 30.0) -> dict:
        raw, frames = tmp / "raw", tmp / "frames"
        fake_recording(raw, SESSION)
        self.assertEqual(record.build(raw, None, frames, seconds), 0)
        return json.loads((frames / "manifest.json").read_text())

    def test_one_frame_per_snapshot_and_delays_hit_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.build(Path(tmp), seconds=30.0)
            self.assertEqual(len(manifest["frames"]), 3)
            self.assertAlmostEqual(manifest["playback_duration_s"], 30.0, places=0)
            self.assertAlmostEqual(manifest["speed"], 200 / 30, places=2)

    def test_a_short_recording_is_not_stretched(self):
        # Asking for 180 seconds of playback from a 200-second session compresses
        # it. Asking for 180 from a 30-second one must not slow it down, or the
        # replay would show codoc reacting more slowly than it really does.
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.build(Path(tmp), seconds=1000.0)
            self.assertEqual(manifest["speed"], 1.0)
            self.assertAlmostEqual(manifest["playback_duration_s"], 200.0, places=0)

    def test_the_recorded_lag_survives_in_proportion(self):
        # Frame 2 waited 50s of the 200s session and frame 3 waited 110s, so
        # after compression frame 3 still waits 2.2 times as long as frame 2.
        with tempfile.TemporaryDirectory() as tmp:
            frames = self.build(Path(tmp))["frames"]
            ratio = frames[2]["delay_s"] / frames[1]["delay_s"]
            self.assertAlmostEqual(ratio, 110 / 50, places=2)


class RoundTripTest(unittest.TestCase):
    def test_replay_reproduces_the_recorded_end_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames, ws = tmp / "raw", tmp / "frames", tmp / "workspace"
            fake_recording(raw, SESSION)
            record.build(raw, None, frames, seconds=0.01)

            ws.mkdir()
            write(ws, "left/over.py", "should be removed by the reset\n")
            play.play(ws, frames, speed=1000.0, step=False,
                      do_reset=True, do_transcript=False)

            expected = record.scan(raw / "_stage")
            self.assertEqual(record.scan(ws), expected)
            self.assertFalse((ws / "left/over.py").exists())
            self.assertFalse((ws / "scribe/config.py").exists(),
                             "a file the agent deleted must not survive replay")
            self.assertEqual((ws / "scribe/notes.py").read_text(), "renumbered\n")

    def test_replaying_twice_lands_in_the_same_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames, ws = tmp / "raw", tmp / "frames", tmp / "workspace"
            fake_recording(raw, SESSION)
            record.build(raw, None, frames, seconds=0.01)
            ws.mkdir()
            play.play(ws, frames, 1000.0, False, True, False)
            first = record.scan(ws)
            play.play(ws, frames, 1000.0, False, True, False)
            self.assertEqual(record.scan(ws), first)


class DeriveTest(unittest.TestCase):
    def test_deriving_carries_the_code_and_records_what_the_condition_added(self):
        # `derive` replays the neutral code recording into one condition's
        # workspace and records what that condition's own machinery did. Here
        # there is no daemon, so what it must still get right is the code: the
        # derived frames have to reproduce the same end state, plus whatever the
        # workspace already had that the recording never touched.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, neutral, ws, out = tmp / "raw", tmp / "neutral", tmp / "ws", tmp / "out"
            fake_recording(raw, SESSION)
            record.build(raw, None, neutral, seconds=0.01)

            # The condition's workspace starts from the same code and carries a
            # description the neutral recording knows nothing about.
            ws.mkdir()
            play.play(ws, neutral, 1000.0, False, True, False)
            play.reset(ws, neutral)
            write(ws, "CLAUDE.md", "the description this condition keeps\n")

            self.assertEqual(record.derive(neutral, ws, out, settle=0.0, timeout=0.0), 0)

            replayed = tmp / "replayed"
            replayed.mkdir()
            play.play(replayed, out, 1000.0, False, True, False)
            self.assertEqual(record.scan(replayed), record.scan(ws))
            self.assertEqual((replayed / "scribe" / "notes.py").read_text(), "renumbered\n")
            self.assertTrue((replayed / "CLAUDE.md").exists(),
                            "the condition's own description has to survive the derivation")

    def test_the_conditions_own_record_pass_lands_in_the_last_frame(self):
        # The baseline's record is written by an agent at the end of a session
        # rather than by a daemon as it goes, so `derive` runs it after the last
        # code frame. What it writes has to reach the frames, or the baseline
        # ships a description that never learned about the change.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, neutral, ws, out = tmp / "raw", tmp / "neutral", tmp / "ws", tmp / "out"
            fake_recording(raw, SESSION)
            record.build(raw, None, neutral, seconds=0.01)
            ws.mkdir()
            play.play(ws, neutral, 1000.0, False, True, False)
            play.reset(ws, neutral)
            write(ws, "CLAUDE.md", "before the change\n")

            record.derive(neutral, ws, out, settle=0.0, timeout=0.0,
                          after="printf 'after the change\n' > CLAUDE.md")

            replayed = tmp / "replayed"
            replayed.mkdir()
            play.play(replayed, out, 1000.0, False, True, False)
            self.assertEqual((replayed / "CLAUDE.md").read_text(), "after the change\n")

    def test_the_derived_manifest_keeps_the_pacing_and_says_where_it_came_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, neutral, ws, out = tmp / "raw", tmp / "neutral", tmp / "ws", tmp / "out"
            fake_recording(raw, SESSION)
            record.build(raw, None, neutral, seconds=30.0)
            ws.mkdir()
            play.play(ws, neutral, 1000.0, False, True, False)
            play.reset(ws, neutral)
            record.derive(neutral, ws, out, settle=0.0, timeout=0.0)

            before = json.loads((neutral / "manifest.json").read_text())
            after = json.loads((out / "manifest.json").read_text())
            self.assertEqual([f["delay_s"] for f in after["frames"]],
                             [f["delay_s"] for f in before["frames"]])
            self.assertEqual(after["speed"], before["speed"])
            self.assertIn("derived_from", after)


class RebuildTest(unittest.TestCase):
    def test_rebuilding_keeps_what_was_written_by_hand(self):
        # notes.md records what the agent was steered into and the transcript is
        # the session itself. Neither can be regenerated, and rebuilding the
        # frames used to delete both.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames = tmp / "raw", tmp / "frames"
            fake_recording(raw, SESSION)
            record.build(raw, None, frames, seconds=1.0)
            (frames / "notes.md").write_text("steered into D1\n")
            (frames / "transcript.jsonl").write_text('{"sessionId":"abc"}\n')

            record.build(raw, None, frames, seconds=1.0)
            self.assertEqual((frames / "notes.md").read_text(), "steered into D1\n")
            self.assertIn("abc", (frames / "transcript.jsonl").read_text())


class HandoverTest(unittest.TestCase):
    def test_the_handover_is_stamped_and_left_out_of_the_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames, ws = tmp / "raw", tmp / "frames", tmp / "workspace"
            fake_recording(raw, SESSION)
            record.build(raw, None, frames, seconds=0.01)
            ws.mkdir()
            play.play(ws, frames, 1000.0, False, True, False)

            stamp = ws / ".codoc" / "replay.stamp"
            self.assertTrue(stamp.exists(), "scoring needs to know when the person took over")
            self.assertGreater(json.loads(stamp.read_text())["handover_ms"], 0)
            self.assertNotIn(".codoc/replay.stamp", record.scan(ws),
                             "the stamp is the player's own, not part of the recording")

    def test_a_reset_clears_a_stamp_from_an_earlier_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames, ws = tmp / "raw", tmp / "frames", tmp / "workspace"
            fake_recording(raw, SESSION)
            record.build(raw, None, frames, seconds=0.01)
            ws.mkdir()
            write(ws, ".codoc/replay.stamp", json.dumps({"handover_ms": 1}))
            play.reset(ws, frames)
            self.assertFalse((ws / ".codoc" / "replay.stamp").exists(),
                             "a stale handover would count the recording as the person's work")


class GuardTest(unittest.TestCase):
    def test_the_player_refuses_while_a_daemon_owns_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            write(ws, ".codoc/watch.pid", json.dumps({"pid": os.getpid()}))
            self.assertEqual(play.daemon_pid(ws), os.getpid())
            with self.assertRaises(SystemExit) as caught:
                play.play(ws, ws, 1.0, False, False, False)
            self.assertIn("daemon is running", str(caught.exception))

    def test_a_dead_pid_does_not_block_the_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            write(ws, ".codoc/watch.pid", json.dumps({"pid": 999999}))
            self.assertIsNone(play.daemon_pid(ws))


class TranscriptTest(unittest.TestCase):
    def test_the_session_is_rewritten_to_the_participant_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            frames, ws, home = tmp / "frames", tmp / "codoc-study/scribe", tmp / "home"
            frames.mkdir(parents=True)
            ws.mkdir(parents=True)
            (frames / "transcript.jsonl").write_text("\n".join([
                json.dumps({"sessionId": "abc-123", "cwd": "/recorder/scribe",
                            "type": "user"}),
                json.dumps({"sessionId": "abc-123", "cwd": "/recorder/scribe",
                            "type": "assistant"}),
            ]))
            original = Path.home
            try:
                Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
                out = play.install_transcript(ws, frames)
            finally:
                Path.home = original  # type: ignore[assignment]
            self.assertIsNotNone(out)
            self.assertEqual(out.name, "abc-123.jsonl")
            self.assertEqual(out.parent.name, str(ws.resolve()).replace(os.sep, "-"))
            self.assertNotIn("/recorder/scribe", out.read_text())
            self.assertIn(str(ws.resolve()), out.read_text())

    def test_terminal_text_comes_out_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text("\n".join([
                json.dumps({"timestamp": "2026-08-19T10:00:00Z", "type": "user",
                            "message": {"role": "user", "content": "add a config file"}}),
                json.dumps({"timestamp": "2026-08-19T10:00:05Z", "type": "assistant",
                            "message": {"role": "assistant", "content": [
                                {"type": "text", "text": "Reading the code."},
                                {"type": "tool_use", "name": "Edit",
                                 "input": {"file_path": "scribe/config.py"}}]}}),
            ]))
            lines = record.render_transcript(path)
            self.assertEqual([text for _, text in lines], [
                "> add a config file", "Reading the code.", "  Edit(scribe/config.py)"])
            self.assertEqual(lines[2][0] - lines[0][0], 5.0)


class QuiesceTest(unittest.TestCase):
    def test_the_end_state_is_taken_after_the_daemon_is_stopped(self):
        """The daemon keeps working after the last frame goes in, so the
        workspace carries on moving while the recording is finished, and `check`
        then compares the replay against a state the recording never held. It
        looked like corrupt frames and was one more Loop A pass eleven seconds
        late."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ws = tmp / "ws"
            (ws / ".codoc").mkdir(parents=True)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            try:
                (ws / ".codoc" / "watch.pid").write_text(json.dumps({"pid": proc.pid}))
                record._quiesce(ws)
                self.assertIsNotNone(proc.poll(), "the daemon was left running")
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)

    def test_quiescing_a_workspace_with_no_daemon_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(record._quiesce(Path(tmp)))

    def test_quiesce_says_whether_there_was_one_to_stop(self):
        """derive needs to know, because if a daemon was stopped it has to take
        one more diff: between the last frame's scan and the stop the daemon can
        finish a pass, and those writes would then be in the workspace and in no
        frame at all."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".codoc").mkdir(parents=True)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            try:
                (ws / ".codoc" / "watch.pid").write_text(json.dumps({"pid": proc.pid}))
                self.assertTrue(record._quiesce(ws))
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)


class IdleTest(unittest.TestCase):
    """The pause between turns is the experimenter, not the agent."""

    def test_a_long_wait_between_turns_is_clipped_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames = tmp / "raw", tmp / "frames"
            fake_recording(raw, [
                {"at_s": 0.0, "files": {"a.py": "start"}},
                {"at_s": 10.0, "files": {"a.py": "one"}},
                {"at_s": 910.0, "files": {"a.py": "two"}},   # 900s of somebody thinking
                {"at_s": 920.0, "files": {"a.py": "three"}},
            ])
            self.assertEqual(record.build(raw, None, frames, 60.0), 0)
            manifest = json.loads((frames / "manifest.json").read_text())
            self.assertEqual(manifest["idle_removed_s"], 900.0 - record.IDLE_CAP_S)
            self.assertEqual(manifest["real_duration_s"], 920.0)

    def test_the_lag_that_matters_survives_untouched(self):
        """Every gap short enough to be about the tools is kept in proportion,
        including how long codoc takes to react to an edit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames = tmp / "raw", tmp / "frames"
            fake_recording(raw, [
                {"at_s": 0.0, "files": {"a.py": "start"}},
                {"at_s": 10.0, "files": {"a.py": "one"}},
                {"at_s": 40.0, "files": {"a.py": "two"}},
            ])
            self.assertEqual(record.build(raw, None, frames, 25.0), 0)
            manifest = json.loads((frames / "manifest.json").read_text())
            self.assertEqual(manifest["idle_removed_s"], 0.0)
            one, two = manifest["frames"][0]["delay_s"], manifest["frames"][1]["delay_s"]
            self.assertAlmostEqual(two / one, 3.0, places=2)


class OneWatcherTest(unittest.TestCase):
    """Two watchers on one raw directory destroy a recording silently.

    Each keeps its own counter and its own idea of what changed last, so they
    overwrite each other's numbered snapshots and each records half the diff. The
    round trip still passes, because the end state is carried by the last frame
    and the final copy, while the middle of the recording runs backwards. It
    happened once, to the tally recording, ten times in sixty-six frames.
    """

    def test_a_second_watcher_refuses_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "watcher.pid").write_text(str(os.getpid()))
            self.assertEqual(record.owning_watcher(raw), os.getpid())
            with self.assertRaises(SystemExit) as caught:
                record.watch(Path(tmp), raw, 0.1)
            self.assertIn("already recording", str(caught.exception))

    def test_a_dead_watcher_does_not_block_the_next_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "watcher.pid").write_text("999999")
            self.assertIsNone(record.owning_watcher(raw))

    def test_a_watcher_stops_on_the_signal_a_script_sends_and_cleans_up(self):
        """`kill -INT` on a `nohup ... &` job does nothing, because such a job
        inherits SIGINT set to ignore. That is how two watchers ended up on one
        directory: the stop said it had worked and had not."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ws, raw = tmp / "ws", tmp / "raw"
            ws.mkdir()
            (ws / "a.py").write_text("start")
            proc = subprocess.Popen(
                [sys.executable, str(HERE / "record.py"), "watch",
                 str(ws), str(raw), "--interval", "0.2"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid)
            try:
                for _ in range(50):
                    if (raw / "watcher.pid").exists():
                        break
                    time.sleep(0.1)
                self.assertTrue((raw / "watcher.pid").exists(), "no pid file was taken")
                proc.terminate()
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            self.assertFalse((raw / "watcher.pid").exists(),
                             "the watcher left its pid file behind, so the next "
                             "recording would refuse to start")

    def test_build_refuses_a_recording_whose_clock_runs_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, frames = tmp / "raw", tmp / "frames"
            fake_recording(raw, [
                {"at_s": 0.0, "files": {"a.py": "start"}},
                {"at_s": 10.0, "files": {"a.py": "one"}},
                {"at_s": 4.0, "files": {"a.py": "two"}},
            ])
            self.assertEqual(record.build(raw, None, frames, 30.0), 1)
            self.assertFalse((frames / "manifest.json").exists())


class NeutralityTest(unittest.TestCase):
    """The recording must not name either tool in what a participant reads.

    The first scribe recording named both, and neither leak came from the agent.
    A `git status` in a workspace made neutral by deleting the tool files listed
    them as staged deletions, and Claude Code prints absolute paths, so every
    Read line carried the recording directory's own name.
    """

    def test_the_harness_own_leaks_are_dropped(self):
        text = "\n".join([
            "  Read(/work/scribe/convert.py)",
            "  Bash(git status --short)",
            " D .claude/skills/codoc-intent/SKILL.md",
            " D .codoc/tree.codoc",
            " D .mcp.json",
            " D CLAUDE.md",
            " M scribe/convert.py",
        ])
        out = record.scrub(text, "/recorder/scribe", "/home/p/codoc-study/scribe")
        self.assertIn("  Read(/work/scribe/convert.py)", out)
        self.assertIn(" M scribe/convert.py", out)
        for gone in (".claude", ".codoc", ".mcp.json", "CLAUDE.md"):
            self.assertNotIn(gone, out)

    def test_the_recorder_path_is_retargeted_at_the_participant(self):
        out = record.scrub("  Read(/recorder/scribe/cli.py)", "/recorder/scribe",
                           "/home/p/codoc-study/scribe")
        self.assertEqual(out, "  Read(/home/p/codoc-study/scribe/cli.py)")

    def test_a_truncated_path_still_survives_because_a_token_is_written_first(self):
        """A long command is cut at eighty characters, and a path cut in the
        middle cannot be matched and replaced afterwards. So the token goes in at
        build time, before anything truncates, and a cut token names nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            long_command = "cd /recorder/scribe && " + "echo padding " * 12
            path.write_text(json.dumps({
                "timestamp": "2026-08-19T10:00:00Z", "type": "assistant", "cwd": "/recorder/scribe",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": long_command}}]}}))
            rendered = record.render_transcript(path, "/recorder/scribe")[0][1]
            self.assertNotIn("/recorder", rendered)
            record.check_no_leak(record.scrub(rendered, "/recorder/scribe", "/p/codoc-study/scribe"),
                                 "/p/codoc-study/scribe", "rendered")

    def test_the_participant_own_path_is_not_mistaken_for_a_leak(self):
        """Their workspace is `~/codoc-study/<project>` and they see it all
        session in both conditions, so the check runs with it taken out."""
        record.check_no_leak("  Read(/home/p/codoc-study/scribe/cli.py)",
                             "/home/p/codoc-study/scribe", "rendered")

    def test_the_player_refuses_a_recording_the_agent_made_non_neutral(self):
        with self.assertRaises(record.Leaked):
            record.check_no_leak("I looked at the codoc tree first.",
                                 "/home/p/codoc-study/scribe", "rendered")

    def test_a_leaking_transcript_is_never_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            frames, ws, home = tmp / "frames", tmp / "codoc-study/scribe", tmp / "home"
            frames.mkdir(parents=True)
            ws.mkdir(parents=True)
            (frames / "transcript.jsonl").write_text("\n".join([
                json.dumps({"sessionId": "abc", "cwd": "/recorder/scribe", "type": "user",
                            "message": {"role": "user", "content": [
                                {"type": "tool_result",
                                 "content": "ok\n D .codoc/tree.codoc\n M scribe/cli.py"}]}}),
                json.dumps({"sessionId": "abc", "cwd": "/recorder/scribe", "type": "assistant",
                            "message": {"role": "assistant", "content": [
                                {"type": "text", "text": "I read the codoc tree."}]}}),
            ]))
            original = Path.home
            try:
                Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
                with self.assertRaises(record.Leaked):
                    play.install_transcript(ws, frames)
            finally:
                Path.home = original  # type: ignore[assignment]


class RetextTest(unittest.TestCase):
    def test_rendering_the_scrollback_again_leaves_the_frames_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = Path(tmp) / "frames"
            (frames / "0001").mkdir(parents=True)
            (frames / "0001" / "a.py").write_text("kept")
            (frames / "transcript.jsonl").write_text(json.dumps({
                "timestamp": "2026-08-19T10:00:00Z", "type": "assistant", "cwd": "/recorder/scribe",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Read",
                     "input": {"file_path": "/recorder/scribe/a.py"}}]}}))
            (frames / "manifest.json").write_text(json.dumps({
                "recorded_at": "2026-08-19T00:00:00Z",
                "frames": [{"n": 1, "at_s": 10.0, "delay_s": 1.0, "writes": ["a.py"],
                            "deletes": [], "terminal": "stale"}]}))
            self.assertEqual(record.retext(frames), 0)
            manifest = json.loads((frames / "manifest.json").read_text())
            self.assertEqual(manifest["recorded_root"], "/recorder/scribe")
            self.assertIn(record.WORKSPACE_TOKEN, manifest["frames"][0]["terminal"])
            self.assertEqual((frames / "0001" / "a.py").read_text(), "kept")


class WatchTest(unittest.TestCase):
    def test_watch_records_what_changed_while_it_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ws, raw = tmp / "workspace", tmp / "raw"
            write(ws, "scribe/lines.py", "one\n")
            proc = subprocess.Popen(
                [sys.executable, str(HERE / "record.py"), "watch", str(ws), str(raw),
                 "--interval", "0.2"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                time.sleep(1.0)
                write(ws, "scribe/config.py", "config\n")
                time.sleep(1.0)
            finally:
                proc.send_signal(signal.SIGINT)
                proc.communicate(timeout=20)
            self.assertTrue((raw / "base" / "scribe/lines.py").exists())
            snapshots = sorted(raw.glob("[0-9]*.json"))
            self.assertTrue(snapshots, "the new file should have produced a snapshot")
            recorded = json.loads(snapshots[0].read_text())
            self.assertIn("scribe/config.py", recorded["writes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SimulateTest(unittest.TestCase):
    """The agent's half, written instead of recorded.

    Recording one cost a key, forty minutes and a lot of steering, and every
    planted problem was steered in anyway — so what was being recorded was
    already an authored stimulus with a real agent typing it. codoc's half is
    still not authored: `derive` replays these frames into a live workspace.
    """

    def script(self, root: Path, steps: list, **extra) -> Path:
        d = root / "script"
        (d / "files").mkdir(parents=True)
        (d / "files" / "new.py").write_text("VALUE = 2\n")
        (d / "session.json").write_text(json.dumps(
            {"request": "make it configurable", "steps": steps, **extra}))
        return d

    def test_frames_replay_to_the_state_the_script_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            (ws / "a.py").write_text("VALUE = 1\n")
            script = self.script(root, [
                {"say": ["● reading"], "delay_s": 1},
                {"say": ["  Write(a.py)"], "delay_s": 1, "write": {"a.py": "files/new.py"}},
            ])
            out = root / "frames"
            self.assertEqual(record.simulate(script, ws, out), 0)

            played = root / "played"
            played.mkdir()
            play.play(played, out, speed=1000, step=False,
                      do_reset=True, do_transcript=False)
            self.assertEqual(record.scan(played), record.scan(ws))

    def test_the_first_frame_echoes_the_request(self):
        # A real recording gets this from the transcript. Without it the
        # participant pastes a request and watches a session that never mentions
        # it, and the bundle's own check that the two agree has nothing to read.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            (ws / "a.py").write_text("VALUE = 1\n")
            script = self.script(root, [{"say": ["● reading"], "delay_s": 1}])
            out = root / "frames"
            record.simulate(script, ws, out)
            first = json.loads((out / "manifest.json").read_text())["frames"][0]
            self.assertIn("make it configurable", first["terminal"])
            self.assertTrue(first["terminal"].startswith("> "))

    def test_the_session_it_writes_can_be_resumed(self):
        # The participant's first turn continues this file, so it has to carry the
        # request and what the agent said back.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            (ws / "a.py").write_text("VALUE = 1\n")
            script = self.script(root, [{"say": ["● reading"], "delay_s": 1}],
                                 session_id="abc-123")
            out = root / "frames"
            record.simulate(script, ws, out)
            lines = (out / "transcript.jsonl").read_text().splitlines()
            first = json.loads(lines[0])
            self.assertEqual(first["type"], "user")
            self.assertEqual(first["sessionId"], "abc-123")
            self.assertEqual(first["message"]["content"], "make it configurable")
            self.assertEqual(json.loads(lines[1])["type"], "assistant")

    def test_a_checkpoint_in_the_script_reaches_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            (ws / "a.py").write_text("VALUE = 1\n")
            script = self.script(root, [
                {"say": ["● one"], "delay_s": 1},
                {"say": ["● two"], "delay_s": 1},
            ], checkpoints=[1], checkpoint_says="accept the plan")
            out = root / "frames"
            record.simulate(script, ws, out)
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["checkpoints"], [1])
            self.assertEqual(manifest["checkpoint_says"], "accept the plan")
            self.assertTrue(manifest["authored"])

    def test_a_script_that_names_a_tool_is_refused(self):
        # The scrollback is read by BOTH arms, so it cannot mention either tool.
        # Authoring it makes that easier to get wrong, not harder.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            (ws / "a.py").write_text("VALUE = 1\n")
            script = self.script(root, [
                {"say": ["  Read(.codoc/tree.codoc)"], "delay_s": 1},
            ])
            with self.assertRaises(record.Leaked):
                record.simulate(script, ws, root / "frames")
