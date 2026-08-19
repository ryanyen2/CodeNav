#!/usr/bin/env python3
"""Replay a recorded agent session into a participant's workspace.

    play.py ~/codoc-study/scribe docs/study-materials/replay/frames/scribe/codoc

The player writes recorded files into the workspace and prints the recorded
terminal text, in the order and at the pace the manifest gives. Everything the
codoc extension shows comes from files under `.codoc/`, which it watches and
reparses, so writing the recorded copies of those files drives the whole
interface without changing anything in codoc.

The daemon has to be stopped while the player runs. Every frame under `.codoc/`
was produced by a real daemon during recording, so no participant waits for an
LLM call and every participant sees the same frames.

Options:

    --speed 2          play twice as fast as the manifest says
    --step             wait for Enter between frames, for a dry run
    --no-reset         do not restore the starting state first
    --no-transcript    do not install the session for `claude --resume`
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record import SKIP_DIRS, SKIP_DIR_SUFFIXES, scan  # noqa: E402

DIM, RESET = "\033[2m", "\033[0m"


def daemon_pid(workspace: Path) -> int | None:
    pidfile = workspace / ".codoc" / "watch.pid"
    if not pidfile.exists():
        return None
    try:
        raw = pidfile.read_text().strip()
        pid = int(json.loads(raw)["pid"]) if raw.startswith("{") else int(raw)
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def copy_tree(src: Path, workspace: Path) -> list[str]:
    written = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.endswith(SKIP_DIR_SUFFIXES)]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(src)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)
            written.append(str(rel))
    return written


def reset(workspace: Path, frames: Path) -> None:
    """Put the workspace back to the state the recording started from."""
    base = frames / "base"
    if not base.exists():
        raise SystemExit(f"no base state in {frames}")
    wanted = scan(base)
    for rel in scan(workspace):
        if rel not in wanted:
            (workspace / rel).unlink(missing_ok=True)
    # Not covered by the scan, and a stale one would report a handover that never
    # happened, so scoring would count the recording's own ledger events as the
    # participant's.
    (workspace / ".codoc" / "replay.stamp").unlink(missing_ok=True)
    copy_tree(base, workspace)


def install_transcript(workspace: Path, frames: Path) -> Path | None:
    """Put the recorded session where `claude --resume` will find it.

    The participant's first prompt then continues the session that produced the
    change, with the agent's own context intact, and the recorded transcript is
    also the terminal scrollback.
    """
    source = frames / "transcript.jsonl"
    if not source.exists():
        return None
    lines = source.read_text(errors="replace").splitlines()
    recorded_root, session_id = None, None
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        recorded_root = recorded_root or entry.get("cwd")
        session_id = session_id or entry.get("sessionId")
        if recorded_root and session_id:
            break
    if not session_id:
        return None

    target_root = str(workspace.resolve())
    slug = target_root.replace(os.sep, "-")
    out_dir = Path.home() / ".claude" / "projects" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{session_id}.jsonl"
    text = "\n".join(lines) + "\n"
    if recorded_root and recorded_root != target_root:
        text = text.replace(recorded_root, target_root)
    out.write_text(text)
    return out


def play(workspace: Path, frames: Path, speed: float, step: bool,
         do_reset: bool, do_transcript: bool) -> int:
    # The guard comes first, because writing recorded files into a workspace a
    # live daemon owns would race the daemon's own writes.
    pid = daemon_pid(workspace)
    if pid:
        raise SystemExit(
            f"the codoc daemon is running as pid {pid}. Stop it before replaying, "
            "because the player writes the files the daemon owns.")

    manifest_path = frames / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no manifest in {frames}")
    manifest = json.loads(manifest_path.read_text())

    if do_reset:
        reset(workspace, frames)
    if do_transcript:
        installed = install_transcript(workspace, frames)
        if installed:
            print(f"{DIM}session installed at {installed}{RESET}")

    total = len(manifest["frames"])
    for frame in manifest["frames"]:
        delay = frame["delay_s"] / speed if speed else 0.0
        text = frame.get("terminal") or ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        per_line = min(delay / len(lines), 0.35) if lines and delay else 0.0
        for line in lines:
            print(line, flush=True)
            if per_line:
                time.sleep(per_line)
        remaining = delay - per_line * len(lines)

        src = frames / f"{frame['n']:04d}"
        if src.exists():
            copy_tree(src, workspace)
        for rel in frame.get("deletes", []):
            (workspace / rel).unlink(missing_ok=True)

        if step:
            input(f"{DIM}frame {frame['n']}/{total}, Enter to continue{RESET}")
        elif remaining > 0:
            time.sleep(remaining)

    tail = frames / "final"
    if tail.exists():
        copy_tree(tail, workspace)
    write_handover_stamp(workspace, frames, manifest)

    print(f"\n{DIM}The agent has finished. The tests pass.{RESET}")
    return 0


def write_handover_stamp(workspace: Path, frames: Path, manifest: dict) -> Path | None:
    """Mark the moment the participant takes over.

    The shipped store already holds the recorded session's ledger events, stamped
    when the recording ran. Scoring has to count only what the participant did,
    so it needs to know when the participant started, and the end of the replay
    is that moment.
    """
    codoc = workspace / ".codoc"
    if not codoc.is_dir():
        return None
    stamp = codoc / "replay.stamp"
    stamp.write_text(json.dumps({
        "handover_ms": int(time.time() * 1000),
        "frames": str(frames),
        "recorded_at": manifest.get("recorded_at", ""),
        "speed": manifest.get("speed"),
    }, indent=2))
    return stamp


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--step", action="store_true")
    parser.add_argument("--no-reset", dest="reset", action="store_false")
    parser.add_argument("--no-transcript", dest="transcript", action="store_false")
    args = parser.parse_args(argv)
    return play(args.workspace.expanduser(), args.frames, args.speed,
                args.step, args.reset, args.transcript)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
