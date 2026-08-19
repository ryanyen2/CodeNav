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
