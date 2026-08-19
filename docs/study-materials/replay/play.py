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
from record import (SKIP_DIRS, SKIP_DIR_SUFFIXES, Leaked, check_no_leak,  # noqa: E402
                    scan, scrub)

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


def config_dir() -> Path:
    """Where the assistant this session runs keeps its history.

    NOT `~/.claude`. Each workspace runs the assistant under its own config
    directory, which is what keeps the study off the participant's own account,
    and the launcher exports `CLAUDE_CONFIG_DIR` before anything here runs. A
    session installed in the home directory instead is one that `--continue`
    cannot see, so the turn after the recording would start a conversation with
    none of the change's context, which is the whole point of installing it.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def install_transcript(workspace: Path, frames: Path) -> Path | None:
    """Put the recorded session where `claude --resume` will find it.

    The participant's first prompt then continues the session that produced the
    change, with the agent's own context intact, and the recorded transcript is
    also the terminal scrollback.

    The recorder's own paths are retargeted at the participant's workspace and
    the harness's leaked file names are dropped, for the reasons written above
    `NEUTRALISED` in `record.py`. Scrubbing runs per JSON record and per string
    inside it, because the leak is inside a tool result rather than at the top
    level, and a record that stops being valid JSON would not load at all.
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
    out_dir = config_dir() / "projects" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{session_id}.jsonl"

    cleaned = []
    for number, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            cleaned.append(line)
            continue
        entry = _scrub_record(entry, recorded_root or "", target_root)
        line = json.dumps(entry)
        check_no_leak(line, target_root, f"the recorded session, record {number}")
        cleaned.append(line)
    out.write_text("\n".join(cleaned) + "\n")
    return out


def _scrub_record(value, recorded_root: str, target_root: str):
    """Scrub every string in one transcript record, leaving its shape alone."""
    if isinstance(value, str):
        return scrub(value, recorded_root, target_root)
    if isinstance(value, list):
        return [_scrub_record(v, recorded_root, target_root) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_record(v, recorded_root, target_root)
                for k, v in value.items()}
    return value


def segments(manifest: dict) -> list[tuple[int, int]]:
    """The frame ranges between checkpoints, as [start, end) over frame NUMBERS.

    A recording without `checkpoints` is one segment, which is what every existing
    recording is, so nothing has to be re-cut to keep working.

    A checkpoint is the frame after which the agent stops and waits for an answer:
    it has proposed a plan and cannot implement until somebody says yes. Playing
    straight through it was the whole trouble with the first design, because the
    part of the session codoc exists for went past read only.
    """
    total = len(manifest["frames"])
    stops = sorted({int(n) for n in manifest.get("checkpoints", []) if 0 < int(n) < total})
    out, start = [], 0
    for n in stops:
        out.append((start, n))
        start = n
    out.append((start, total))
    return [(a, b) for a, b in out if b > a]


def play(workspace: Path, frames: Path, speed: float, step: bool,
         do_reset: bool, do_transcript: bool,
         span: tuple[int, int] | None = None, tail: bool = True) -> int:
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
    target_root = str(workspace.resolve())
    recorded = manifest.get("recorded_root") or ""

    if do_reset:
        reset(workspace, frames)
    if do_transcript:
        installed = install_transcript(workspace, frames)
        if installed:
            print(f"{DIM}session installed at {installed}{RESET}")

    chosen = manifest["frames"][span[0]:span[1]] if span else manifest["frames"]
    total = len(chosen)
    for frame in chosen:
        delay = frame["delay_s"] / speed if speed else 0.0
        text = scrub(frame.get("terminal") or "", recorded, target_root)
        check_no_leak(text, target_root, f"the terminal text of frame {frame['n']}")
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

    if not tail:
        return 0
    end = frames / "final"
    if end.exists():
        copy_tree(end, workspace)
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
    try:
        return play(args.workspace.expanduser(), args.frames, args.speed,
                    args.step, args.reset, args.transcript)
    except Leaked as leak:
        # Refusing is the whole point. Everything the harness put there is
        # already dropped, so what is left is the agent having named a tool, and
        # that is a recording to make again rather than a line to delete.
        print(f"\nthis recording is not neutral, so it is not being replayed.\n{leak}",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
