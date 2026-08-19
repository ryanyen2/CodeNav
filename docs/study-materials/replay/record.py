#!/usr/bin/env python3
"""Record an agent session as replayable frames.

The study replays a recorded agent session instead of making every participant
wait for one. Recording happens once per project, on the experimenter's machine,
with the codoc daemon running so that the tree state a participant sees is the
state codoc really produced.

Two subcommands:

    record.py watch  <workspace> <raw-dir>        while the agent runs
    record.py build  <raw-dir> <transcript.jsonl> <frames-dir> [--seconds 180]

`watch` copies every file that changed since the last look into a numbered
snapshot, about once a second, and stops on Ctrl-C. `build` turns those
snapshots into frames with playback delays, and renders the terminal text for
each frame from the Claude Code transcript.

Delays are scaled by one constant factor, so the lag between a code edit and the
tree reacting to it survives playback in proportion. The factor is written into
the manifest and is meant to be reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Never snapshotted. The index directories are the daemon's own working state,
# they are large and binary, and no participant ever sees them, so they are
# copied once into the last frame rather than into every frame.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".claude-study", ".ruff_cache", ".mypy_cache",
}
INDEX_DIRS = {".codoc/lancedb", ".codoc/cocoindex.db"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
# The player writes this when it hands the workspace over, so it exists after a
# replay and never during a recording. Counting it would make a correct replay
# look like it had produced a file the recording did not, and its contents are a
# timestamp, so two correct replays would also disagree with each other.
SKIP_FILES = {".codoc/replay.stamp"}


def _skip(rel: str, with_index: bool) -> bool:
    if rel in SKIP_FILES:
        return True
    parts = Path(rel).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    if Path(rel).suffix in SKIP_SUFFIXES:
        return True
    if not with_index and any(rel.startswith(d + os.sep) for d in INDEX_DIRS):
        return True
    return False


def scan(root: Path, with_index: bool = False) -> dict[str, str]:
    """Every included file under root, as a map of relative path to sha256."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = str(full.relative_to(root))
            if _skip(rel, with_index):
                continue
            try:
                out[rel] = hashlib.sha256(full.read_bytes()).hexdigest()
            except (OSError, ValueError):
                continue
    return out


def watch(workspace: Path, raw: Path, interval: float) -> int:
    """Copy what changed, about once a second, until interrupted."""
    raw.mkdir(parents=True, exist_ok=True)
    previous = scan(workspace)
    (raw / "base.json").write_text(json.dumps(previous, indent=2, sort_keys=True))
    base = raw / "base"
    for rel in previous:
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, dest)
    print(f"base: {len(previous)} files. Watching. Ctrl-C when the agent is done.")

    n, started = 0, time.time()
    try:
        while True:
            time.sleep(interval)
            current = scan(workspace)
            writes = [r for r, h in current.items() if previous.get(r) != h]
            deletes = [r for r in previous if r not in current]
            if not writes and not deletes:
                continue
            n += 1
            snap = raw / f"{n:04d}"
            for rel in writes:
                dest = snap / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(workspace / rel, dest)
            (raw / f"{n:04d}.json").write_text(json.dumps({
                "n": n,
                "at_s": round(time.time() - started, 2),
                "wall": datetime.now(timezone.utc).isoformat(),
                "writes": sorted(writes),
                "deletes": sorted(deletes),
            }, indent=2))
            previous = current
            print(f"  {n:4d}  +{len(writes)} -{len(deletes)}  {writes[0] if writes else ''}")
    except KeyboardInterrupt:
        print(f"\nstopped after {n} snapshots, {round(time.time() - started)}s")

    # The last snapshot carries the index directories, so the daemon can pick up
    # a consistent workspace when it starts at the handover.
    final = scan(workspace, with_index=True)
    tail = raw / "final"
    for rel in final:
        if not any(rel.startswith(d + os.sep) for d in INDEX_DIRS):
            continue
        dest = tail / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, dest)
    (raw / "final.json").write_text(json.dumps({
        "at_s": round(time.time() - started, 2),
        "files": sorted(f for f in final if any(
            f.startswith(d + os.sep) for d in INDEX_DIRS)),
    }, indent=2))
    return 0


# The terminal text a participant reads is rendered from the transcript rather
# than captured off the screen, so it can be aligned to the frames by timestamp
# and so it looks the same on every machine.

def _ts(entry: dict) -> float:
    raw = entry.get("timestamp")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def render_transcript(path: Path) -> list[tuple[float, str]]:
    """The session as a list of timestamped lines of terminal text."""
    lines: list[tuple[float, str]] = []
    for raw_line in path.read_text(errors="replace").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        when = _ts(entry)
        message = entry.get("message") or {}
        role = message.get("role") or entry.get("type")
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and role == "assistant":
                text = (block.get("text") or "").strip()
                if text:
                    lines.append((when, text))
            elif kind == "text" and role == "user":
                text = (block.get("text") or "").strip()
                if text:
                    lines.append((when, f"> {text}"))
            elif kind == "tool_use":
                lines.append((when, _tool_line(block)))
    return lines


def _tool_line(block: dict) -> str:
    name = block.get("name", "tool")
    args = block.get("input") or {}
    if name in {"Edit", "Write", "NotebookEdit"}:
        return f"  {name}({args.get('file_path', '')})"
    if name == "Read":
        return f"  Read({args.get('file_path', '')})"
    if name == "Bash":
        return f"  Bash({(args.get('command') or '')[:80]})"
    if name in {"Grep", "Glob"}:
        return f"  {name}({args.get('pattern', '')})"
    return f"  {name}(...)"


def build(raw: Path, transcript: Path | None, frames: Path, seconds: float) -> int:
    snapshots = sorted(p for p in raw.glob("[0-9]*.json"))
    if not snapshots:
        print("no snapshots in", raw, file=sys.stderr)
        return 1
    meta = [json.loads(p.read_text()) for p in snapshots]
    real_duration = meta[-1]["at_s"] or 1.0
    # Never below one. A recording shorter than the target would otherwise be
    # stretched, and a replay slower than the session it came from would show a
    # tree reacting more slowly than codoc really reacts.
    speed = max(1.0, real_duration / seconds) if seconds > 0 else 1.0

    text_lines = render_transcript(transcript) if transcript and transcript.exists() else []
    if text_lines:
        origin = text_lines[0][0]
        text_lines = [(t - origin, s) for t, s in text_lines]

    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    shutil.copytree(raw / "base", frames / "base")

    out_frames, previous_at, cursor = [], 0.0, 0
    for i, entry in enumerate(meta, start=1):
        src = raw / f"{entry['n']:04d}"
        dest = frames / f"{i:04d}"
        if src.exists():
            shutil.copytree(src, dest)
        chunk = []
        while cursor < len(text_lines) and text_lines[cursor][0] <= entry["at_s"]:
            chunk.append(text_lines[cursor][1])
            cursor += 1
        out_frames.append({
            "n": i,
            "at_s": entry["at_s"],
            "delay_s": round(max(entry["at_s"] - previous_at, 0.0) / speed, 3),
            "writes": entry["writes"],
            "deletes": entry["deletes"],
            "terminal": "\n".join(chunk),
        })
        previous_at = entry["at_s"]

    tail = raw / "final"
    if tail.exists():
        shutil.copytree(tail, frames / "final")
    if text_lines and cursor < len(text_lines):
        out_frames[-1]["terminal"] += "\n" + "\n".join(s for _, s in text_lines[cursor:])

    (frames / "manifest.json").write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "real_duration_s": round(real_duration, 1),
        "playback_duration_s": round(sum(f["delay_s"] for f in out_frames), 1),
        "speed": round(speed, 2),
        "frames": out_frames,
    }, indent=2))
    print(f"{len(out_frames)} frames, {round(real_duration)}s recorded, "
          f"{round(sum(f['delay_s'] for f in out_frames))}s of playback, {speed:.1f}x")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watch", help="snapshot a workspace while the agent runs")
    w.add_argument("workspace", type=Path)
    w.add_argument("raw", type=Path)
    w.add_argument("--interval", type=float, default=1.0)

    b = sub.add_parser("build", help="turn snapshots into frames")
    b.add_argument("raw", type=Path)
    b.add_argument("transcript", type=Path, nargs="?")
    b.add_argument("frames", type=Path)
    b.add_argument("--seconds", type=float, default=180.0,
                   help="how long the replay should take, default 180")

    args = parser.parse_args(argv)
    if args.command == "watch":
        return watch(args.workspace, args.raw, args.interval)
    return build(args.raw, args.transcript, args.frames, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
