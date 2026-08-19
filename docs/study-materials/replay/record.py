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
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Never snapshotted. The index directories are the daemon's own working state,
# they are large and binary, and no participant ever sees them, so they are
# copied once into the last frame rather than into every frame.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".claude-study", ".ruff_cache", ".mypy_cache", "build", "dist",
}
# Editable installs leave one of these behind, named for the package. It is build
# output, it differs by machine, and replaying it would overwrite the install in
# the participant's own workspace.
SKIP_DIR_SUFFIXES = (".egg-info",)
INDEX_DIRS = {".codoc/lancedb", ".codoc/cocoindex.db"}
# Copied once, into the last frame, for the same reason as the index directories.
# The store is the daemon's own state and nothing reads it during a replay: the
# webview draws tree.doc.json and the status bar reads status.json. A copy per
# frame was 85% of a recording's size and wrote a database nineteen times during a
# three-minute replay for nothing anybody could see. The last one is kept, because
# the workspace has to be consistent when the participant takes over.
FINAL_ONLY_FILES = {".codoc/codoc.db"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
# The player writes this when it hands the workspace over, so it exists after a
# replay and never during a recording. Counting it would make a correct replay
# look like it had produced a file the recording did not, and its contents are a
# timestamp, so two correct replays would also disagree with each other.
# Never snapshotted. `replay.stamp` is the player's own. The rest is state that
# belongs to whichever process is running right now: SQLite's write-ahead log and
# shared-memory sidecars are meaningless without the database they were written
# beside, the loop lock and the daemon's pid file describe a process that will not
# exist when the frame is replayed, and a recorded pid file would make the
# player's own daemon guard fire against a daemon that died weeks ago.
SKIP_FILES = {
    ".codoc/replay.stamp", ".codoc/loop.lock", ".codoc/watch.pid",
}
SKIP_FILE_SUFFIXES = (".db-wal", ".db-shm", ".db-journal", ".lock", ".pid", ".log")

# Never snapshotted, whatever else changes. A recording is copied into every
# participant's workspace and then collected back, so a key that got into a frame
# would be handed to twelve people and travel home again. The study has been close
# to this once already, when `git add -A` in a workspace with no .gitignore took
# `.claude-study/api-key` and `.env` into a commit that would have gone home
# inside the history.
SECRET_NAMES = {".env", ".env.local", "api-key", ".netrc", "credentials",
                "id_rsa", ".npmrc", ".pypirc"}
SECRET_SUFFIXES = {".pem", ".key"}

# Keeping the harness out of what the participant reads.
#
# The code session is recorded in a workspace with neither tool in it, so the
# transcript both conditions read never names either one. Two things defeated
# that on the first recording, and both were the harness rather than the agent.
#
# The recording workspace is `~/codoc-recording/<project>-neutral`, and Claude
# Code prints absolute paths, so the scrollback said `codoc-recording` and
# `-neutral` on every Read line. And the workspace is made neutral by deleting
# the tool files, which leaves them staged as deletions, so a `git status` the
# agent ran listed `.codoc/tree.codoc` and `.claude/skills/codoc-intent/SKILL.md`
# by name. A baseline participant would have read codoc's own file names in their
# own terminal history.
#
# Both are repaired at play time, because the participant's workspace path is
# only known then. `NEUTRALISED` drops a line naming a file the neutral workspace
# removed. `RESIDUAL` is the gate. If either tool is still named after that, the
# harness cannot account for it, which means the agent said it, and that is a
# recording to make again rather than a line to delete.
# One entry per path `record-session.sh strip_tools` removes, so the two lists
# can be read against each other.
NEUTRALISED = re.compile(r"\.codoc|\.claude|\.mcp\.json|CLAUDE\.md", re.I)
# Written into the terminal text at build time, in place of the directory the
# recording was made in, and expanded to the participant's own workspace at
# play time. The substitution has to happen before `_tool_line` truncates a
# long command, because a path cut at eighty characters cannot be matched and
# replaced afterwards, while a cut placeholder is harmless.
WORKSPACE_TOKEN = "{{WORKSPACE}}"

# The longest gap between two snapshots that is kept as it was recorded.
#
# A recording is made by a person sending the agent a follow-up when the last one
# lands, and the pause between them is that person reading, deciding and typing.
# It is not the agent working, and the participant is told they are watching one
# session that ran while they were at lunch. Left alone it is dead air: on the
# first tally recording the gaps between turns were minutes long and made up more
# of the timeline than the work did.
#
# Only gaps longer than this are clipped, so every lag that is actually about the
# tools survives untouched and in proportion, including the one the study cares
# about, which is how long codoc takes to react to an edit. The manifest records
# how much was removed and it is meant to be reported alongside the factor.
IDLE_CAP_S = 120.0
RESIDUAL = re.compile(r"codoc|\.mcp\.json|CLAUDE\.md", re.I)


class Leaked(Exception):
    """The recording names a tool somewhere the harness cannot account for."""


def scrub(text: str, recorded_root: str, target_root: str) -> str:
    """Retarget the recorder's paths, and drop the lines the harness leaked."""
    if target_root:
        text = text.replace(WORKSPACE_TOKEN, target_root)
    if recorded_root and target_root and recorded_root != target_root:
        text = text.replace(recorded_root, target_root)
    return "\n".join(ln for ln in text.split("\n") if not NEUTRALISED.search(ln))


def check_no_leak(text: str, target_root: str, where: str) -> None:
    """Refuse to hand a participant a scrollback that names either tool.

    The participant's own workspace is `~/codoc-study/<project>`, which contains
    the word, and they see their own path all session in both conditions. So the
    check runs with their path taken out, and what it is looking for is the
    recording's path and the other condition's files.
    """
    left = text.replace(target_root, "") if target_root else text
    for number, line in enumerate(left.split("\n"), start=1):
        if RESIDUAL.search(line):
            raise Leaked(f"{where}, line {number} names a tool the participant "
                         f"must not see here:\n  {line.strip()[:200]}")


def _skip(rel: str, with_index: bool) -> bool:
    if rel in SKIP_FILES or rel.endswith(SKIP_FILE_SUFFIXES):
        return True
    name = Path(rel).name
    if name in SECRET_NAMES or Path(rel).suffix in SECRET_SUFFIXES:
        return True
    parts = Path(rel).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    if any(p.endswith(SKIP_DIR_SUFFIXES) for p in parts):
        return True
    if Path(rel).suffix in SKIP_SUFFIXES:
        return True
    if not with_index and any(rel.startswith(d + os.sep) for d in INDEX_DIRS):
        return True
    if not with_index and rel in FINAL_ONLY_FILES:
        return True
    return False


def _final_only(rel: str) -> bool:
    """Whether this file is carried once, in the last frame, rather than per frame."""
    return rel in FINAL_ONLY_FILES or any(
        rel.startswith(d + os.sep) for d in INDEX_DIRS)


def scan(root: Path, with_index: bool = False) -> dict[str, str]:
    """Every included file under root, as a map of relative path to sha256."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.endswith(SKIP_DIR_SUFFIXES)]
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


def owning_watcher(raw: Path) -> int | None:
    """The pid of a live watcher already writing here, if there is one."""
    pidfile = raw / "watcher.pid"
    if not pidfile.exists():
        return None
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return None
    return pid


def watch(workspace: Path, raw: Path, interval: float) -> int:
    """Copy what changed, about once a second, until interrupted.

    Two watchers on one raw directory silently destroy a recording, and the
    damage does not show up in the round trip. Each keeps its own counter and its
    own idea of what changed last, so they overwrite each other's numbered
    snapshots and each records only its own half of the diff. The end state still
    replays correctly, because the last frame and the final copy carry it, while
    the middle of the recording runs backwards: on the tally recording the
    timeline stepped back ten times in sixty-six frames, which a participant would
    see as code appearing and then reverting.
    """
    raw.mkdir(parents=True, exist_ok=True)
    owner = owning_watcher(raw)
    if owner:
        raise SystemExit(
            f"a watcher is already recording into {raw} as pid {owner}. Stop it "
            "first, because two watchers overwrite each other's snapshots and the "
            "round trip will not catch it.")
    (raw / "watcher.pid").write_text(str(os.getpid()))
    # A job started with `nohup ... &` from a script inherits SIGINT set to
    # ignore, so the `kill -INT` that used to stop the watcher did nothing and
    # said it had worked. Answering SIGTERM the same way Ctrl-C is answered means
    # one stop path for a person and for a script, and the pid file goes either
    # way because the cleanup is in the same `finally`.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
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
        return _watch_loop(workspace, raw, interval, previous, started)
    finally:
        (raw / "watcher.pid").unlink(missing_ok=True)


def _watch_loop(workspace: Path, raw: Path, interval: float,
                previous: dict, started: float) -> int:
    n = 0
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
        if not _final_only(rel):
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


def recorded_root(transcript: Path | None) -> str:
    """The directory the session was recorded in, as Claude Code wrote it down."""
    if not transcript or not transcript.exists():
        return ""
    for raw in transcript.read_text(errors="replace").splitlines():
        try:
            cwd = json.loads(raw).get("cwd")
        except json.JSONDecodeError:
            continue
        if cwd:
            return str(cwd)
    return ""


def render_transcript(path: Path, root: str = "") -> list[tuple[float, str]]:
    """The session as a list of timestamped lines of terminal text."""
    lines: list[tuple[float, str]] = []
    for raw_line in path.read_text(errors="replace").splitlines():
        raw_line = raw_line.strip()
        if root:
            raw_line = raw_line.replace(root, WORKSPACE_TOKEN)
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
    # A recording whose clock runs backwards was watched by two watchers at once,
    # and the round trip does not catch it: the end state still replays, while the
    # middle shows code appearing and then reverting. Refuse rather than ship it.
    backwards = [(i, meta[i - 1]["at_s"], meta[i]["at_s"])
                 for i in range(1, len(meta)) if meta[i]["at_s"] < meta[i - 1]["at_s"]]
    if backwards:
        first = backwards[0]
        print(f"the recording's clock runs backwards {len(backwards)} time(s), "
              f"first at snapshot {first[0] + 1} ({first[1]}s then {first[2]}s). "
              f"Two watchers wrote into {raw}. Record it again with one.",
              file=sys.stderr)
        return 1
    real_duration = meta[-1]["at_s"] or 1.0
    # Gaps first, because the factor has to be computed against the timeline that
    # is actually going to be played.
    gaps, previous = [], 0.0
    for entry in meta:
        gaps.append(max(entry["at_s"] - previous, 0.0))
        previous = entry["at_s"]
    kept = [min(g, IDLE_CAP_S) for g in gaps]
    idle_removed = round(sum(gaps) - sum(kept), 1)
    played_duration = sum(kept) or 1.0
    # Never below one. A recording shorter than the target would otherwise be
    # stretched, and a replay slower than the session it came from would show a
    # tree reacting more slowly than codoc really reacts.
    speed = max(1.0, played_duration / seconds) if seconds > 0 else 1.0

    root = recorded_root(transcript)
    text_lines = render_transcript(transcript, root) if transcript and transcript.exists() else []
    if text_lines:
        origin = text_lines[0][0]
        text_lines = [(t - origin, s) for t, s in text_lines]

    # `notes.md` records what the agent was steered into and `transcript.jsonl` is
    # the session itself. Both are written beside the frames and neither can be
    # regenerated, so a rebuild keeps them. Rebuilding used to delete them.
    keep = {}
    for name in ("notes.md", "transcript.jsonl"):
        path = frames / name
        if path.exists():
            keep[name] = path.read_bytes()
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    for name, body in keep.items():
        (frames / name).write_bytes(body)
    shutil.copytree(raw / "base", frames / "base")

    out_frames, cursor = [], 0
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
            "delay_s": round(kept[i - 1] / speed, 3),
            "writes": entry["writes"],
            "deletes": entry["deletes"],
            "terminal": "\n".join(chunk),
        })

    tail = raw / "final"
    if tail.exists():
        shutil.copytree(tail, frames / "final")
    if text_lines and cursor < len(text_lines):
        out_frames[-1]["terminal"] += "\n" + "\n".join(s for _, s in text_lines[cursor:])

    (frames / "manifest.json").write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_root": root,
        "real_duration_s": round(real_duration, 1),
        "idle_removed_s": idle_removed,
        "idle_cap_s": IDLE_CAP_S,
        "playback_duration_s": round(sum(f["delay_s"] for f in out_frames), 1),
        "speed": round(speed, 2),
        "frames": out_frames,
    }, indent=2))
    print(f"{len(out_frames)} frames, {round(real_duration)}s recorded, "
          f"{round(idle_removed)}s of it waiting between turns, "
          f"{round(sum(f['delay_s'] for f in out_frames))}s of playback, {speed:.1f}x")
    return 0


# ---------------------------------------------------------------------------
# Deriving one condition's frames from the neutral code recording
# ---------------------------------------------------------------------------
#
# The code session is recorded once, in a workspace with no codoc and no
# description, for two reasons. Both conditions have to review the same code, or
# a detection count cannot be compared across them. And the transcript is read by
# participants in both conditions, so it must not mention either tool: an agent
# left in a codoc workspace explores it, finds `.codoc/tree.codoc` and the codoc
# skill, and says so in its own output.
#
# `derive` replays that neutral recording into one condition's workspace and
# records what the condition's own machinery did in response. For codoc that is
# the daemon, running live, one Loop A pass per frame. Nothing is authored.


# The daemon debounces filesystem events by 600ms before it starts a pass
# (`watch.DEBOUNCE_MS`), so for the first moment after a write it has not noticed
# anything and looks exactly like a daemon that has finished. Waiting for quiet
# without waiting for it to start first is what made the first derivation record
# a description that moved once in forty-two frames: every frame was declared
# settled before the daemon had woken up.
NOTICE_GRACE_S = 6.0


def _codoc_newest(workspace: Path) -> float:
    codoc = workspace / ".codoc"
    newest = 0.0
    skip = tuple(str(codoc / d.split("/", 1)[1]) for d in INDEX_DIRS)
    for path in codoc.rglob("*"):
        if path.is_file() and not str(path).startswith(skip):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


# The daemon has one state that means work is under way and several that mean it
# has finished and is waiting for a person. `code_drift` is the important one: it
# says a pass ran and left proposals for somebody to accept, which is exactly the
# state a participant reviews. Treating it as busy made the derivation sit out its
# whole timeout at every settle point after the first proposal appeared.
RESTING_STATES = {"in_sync", "idle", "code_drift", "tree_dirty", "awaiting_impl", ""}


def _codoc_busy(workspace: Path) -> bool:
    try:
        state = json.loads((workspace / ".codoc" / "status.json").read_text()).get("state", "")
    except (OSError, ValueError):
        return True
    return state not in RESTING_STATES


def _wait_for_daemon(workspace: Path, mark: float, settle: float, timeout: float) -> float:
    """Wait for the daemon to notice the write, react to it, and go quiet again.

    Returns the seconds waited. A daemon that never reacts is not an error: it
    decided the change needed nothing, which is a fact about codoc that the study
    reports rather than papers over.
    """
    waited = 0.0
    noticed = False
    while waited < timeout:
        newest, busy = _codoc_newest(workspace), _codoc_busy(workspace)
        if busy or newest > mark:
            noticed = True
        if noticed and not busy and (time.time() - newest) >= settle:
            return waited
        if not noticed and waited >= NOTICE_GRACE_S:
            return waited
        time.sleep(1.0)
        waited += 1.0
    return waited


def derive(frames: Path, workspace: Path, out: Path, settle: float,
           timeout: float, settle_every: int = 1, after: str = "",
           pace: bool = False) -> int:
    """Replay the neutral recording into one condition and record its response.

    `settle_every` is how many code frames go in before the daemon is given time
    to catch up. The daemon coalesces rapid saves into one pass anyway, so
    pausing at every frame buys nothing except one LLM call per frame, and it
    makes the tree flicker through twenty updates during a three-minute replay.
    Pausing every few frames gives the participant a handful of moments where the
    description visibly catches up with the code, which is what there is to see.
    """
    manifest = json.loads((frames / "manifest.json").read_text())
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    base = scan(workspace)
    tail = out / "base"
    for rel in base:
        dest = tail / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, dest)

    watching = (workspace / ".codoc").is_dir()
    previous = base
    derived = []
    for frame in manifest["frames"]:
        mark = _codoc_newest(workspace) if watching else 0.0
        src = frames / f"{frame['n']:04d}"
        if src.exists():
            for path in src.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(src)
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        for rel in frame.get("deletes", []):
            (workspace / rel).unlink(missing_ok=True)

        # Without pacing the frames go in as fast as the disk allows, the daemon
        # coalesces the lot into one pass, and the description moves once at the
        # very end. A participant then watches nothing happen for three minutes
        # and everything happen at once, which is not what codoc does.
        if pace and frame["delay_s"]:
            time.sleep(min(frame["delay_s"], 20.0))

        waited = 0.0
        last = frame is manifest["frames"][-1]
        if watching and (last or frame["n"] % settle_every == 0):
            waited = _wait_for_daemon(workspace, mark, settle, timeout)

        current = scan(workspace)
        writes = [r for r, h in current.items() if previous.get(r) != h]
        deletes = [r for r in previous if r not in current]
        dest_dir = out / f"{frame['n']:04d}"
        for rel in writes:
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace / rel, dest)
        derived.append({**frame, "writes": sorted(writes), "deletes": sorted(deletes),
                        "settled_after_s": round(waited, 1)})
        previous = current
        moved = [w for w in writes if w.startswith(".codoc/")]
        print(f"  frame {frame['n']:>3}  +{len(writes)} -{len(deletes)}"
              f"  {len(moved)} under .codoc  settled in {waited:.0f}s")

    # The condition's own record-updating machinery, for a condition whose
    # machinery is not a daemon. In the baseline that is the documentation
    # maintenance skill, run once at the end the way it runs at the end of any
    # session. Its own transcript is discarded: the participant's scrollback is
    # the neutral recording, and the daemon's Loop A passes are not in a
    # transcript either, so discarding it is what keeps the two conditions
    # symmetrical.
    if after:
        print(f"  running the condition's own record pass: {after[:60]}")
        subprocess.run(after, cwd=workspace, shell=True, timeout=1800)
        current = scan(workspace)
        writes = [r for r, h in current.items() if previous.get(r) != h]
        if writes:
            dest_dir = out / f"{derived[-1]['n']:04d}"
            for rel in writes:
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(workspace / rel, dest)
            derived[-1]["writes"] = sorted(set(derived[-1]["writes"]) | set(writes))
            print(f"  it wrote {len(writes)} file(s)")

    final = scan(workspace, with_index=True)
    last = out / "final"
    for rel in final:
        if not _final_only(rel):
            continue
        dest = last / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, dest)

    (out / "manifest.json").write_text(json.dumps({
        **{k: v for k, v in manifest.items() if k != "frames"},
        "derived_from": str(frames),
        "derived_at": datetime.now(timezone.utc).isoformat(),
        "frames": derived,
    }, indent=2))
    if (frames / "transcript.jsonl").exists():
        shutil.copy2(frames / "transcript.jsonl", out / "transcript.jsonl")
    touched = sum(1 for f in derived if any(w.startswith(".codoc/") for w in f["writes"]))
    print(f"{len(derived)} frames, the description moved in {touched} of them")
    return 0


def retext(frames: Path) -> int:
    """Render a finished recording's scrollback again, from its own transcript.

    The files in the frames are not touched, only the terminal text and the
    recorded root in the manifest. It exists because the scrollback is derived
    from the transcript while the frames cost an hour of daemon time to derive,
    so a fault in what the participant reads should not mean deriving both
    conditions again.
    """
    manifest_path = frames / "manifest.json"
    transcript = frames / "transcript.jsonl"
    if not manifest_path.exists() or not transcript.exists():
        print(f"{frames} needs both a manifest and a transcript", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    root = recorded_root(transcript)
    text_lines = render_transcript(transcript, root)
    if text_lines:
        origin = text_lines[0][0]
        text_lines = [(t - origin, s) for t, s in text_lines]

    cursor = 0
    for frame in manifest["frames"]:
        chunk = []
        while cursor < len(text_lines) and text_lines[cursor][0] <= frame["at_s"]:
            chunk.append(text_lines[cursor][1])
            cursor += 1
        frame["terminal"] = "\n".join(chunk)
    if text_lines and cursor < len(text_lines):
        manifest["frames"][-1]["terminal"] += "\n" + "\n".join(s for _, s in text_lines[cursor:])

    out = {}
    for key, value in manifest.items():
        if key == "frames":
            continue
        out[key] = value
        if key == "recorded_at":
            out["recorded_root"] = root
    out.setdefault("recorded_root", root)
    out["frames"] = manifest["frames"]
    manifest_path.write_text(json.dumps(out, indent=2))
    print(f"{len(manifest['frames'])} frames re-rendered from {transcript.name}")
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

    r = sub.add_parser("retext",
                       help="render an existing recording's scrollback again")
    r.add_argument("frames", type=Path)

    d = sub.add_parser("derive", help="replay a neutral recording into one condition")
    d.add_argument("frames", type=Path)
    d.add_argument("workspace", type=Path)
    d.add_argument("out", type=Path)
    d.add_argument("--settle", type=float, default=4.0,
                   help="seconds of quiet under .codoc that count as settled")
    d.add_argument("--timeout", type=float, default=300.0,
                   help="how long to wait for one Loop A pass before moving on")
    d.add_argument("--settle-every", type=int, default=1,
                   help="how many frames go in before the daemon is given time")
    d.add_argument("--pace", action="store_true",
                   help="wait each frame's own playback delay before the next one, "
                        "so the daemon gets the gaps it would really get")
    d.add_argument("--after", default="",
                   help="a command to run in the workspace after the last frame, "
                        "for a condition whose record is written by an agent "
                        "rather than by a daemon")

    args = parser.parse_args(argv)
    if args.command == "watch":
        return watch(args.workspace, args.raw, args.interval)
    if args.command == "retext":
        return retext(args.frames)
    if args.command == "derive":
        return derive(args.frames, args.workspace, args.out, args.settle,
                      args.timeout, max(1, args.settle_every), args.after, args.pace)
    return build(args.raw, args.transcript, args.frames, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
