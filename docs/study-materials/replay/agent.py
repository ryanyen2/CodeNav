#!/usr/bin/env python3
"""The participant's first turn with the agent.

    agent.py play <workspace> <frames> [--codoc-bin PATH]
    agent.py capture <workspace>

The study asks somebody to review a change an agent made to their project. The
change is recorded once and replayed, so every participant reviews the same code
and nobody spends forty minutes watching an agent type. What was missing was the
moment that makes the change theirs: they have to ask for it.

So the first turn is this program. It shows the assistant's own opening screen,
takes their request, and then plays the recording. Everything after that first
turn is the real assistant, resumed on the recorded session, so they can ask it
about anything it did.

Two things keep the opening screen honest rather than imagined. `capture` runs
the real assistant once on the participant's own machine, at setup, and keeps
the bytes it drew before its input box. `play` prints those bytes back. A
version that draws its welcome differently therefore draws it differently here
too, and nobody has to keep a copy of somebody else's layout up to date.

The input box below it is ours, because the text has to be rendered as it is
typed and the captured bytes are a picture rather than a program.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import play as player  # noqa: E402

# Where the captured screen and the handover record live. Inside the assistant's
# own profile directory, which setup.sh already writes and collect.sh already
# takes, so nothing new has to be gathered at the end of a session.
PROFILE = ".claude-study"
WELCOME = "welcome.ansi"
HANDOVER = "handover.json"

DIM, RESET = "\033[2m", "\033[0m"

# Drawn only when there is no capture. It is the shape the assistant has used
# since 2.x, and it exists so a machine whose capture failed still runs a session
# rather than starting with a Python traceback in front of a participant.
FALLBACK = (
    "\n\033[38;5;209m✻\033[0m Welcome to \033[1mClaude Code\033[0m\n"
    "\n  \033[2m/help for help, /status for your current setup\033[0m\n"
    "\n  \033[2mcwd: {cwd}\033[0m\n\n"
)


# ── the opening screen ───────────────────────────────────────────────────────

def capture(workspace: Path, seconds: float = 8.0) -> int:
    """Record what the real assistant draws before its input box.

    Run at setup, on the participant's machine, with their own profile and their
    own key, so the screen kept is the one that machine would really have drawn.
    """
    import pty

    profile = workspace / PROFILE
    profile.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(profile)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(workspace)
        os.environ.clear()
        os.environ.update(env)
        os.execvp("claude", ["claude"])

    _set_size(fd, 40, 100)
    raw = b""
    quiet_since = None
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.25)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            raw += chunk
            quiet_since = time.time()
        elif quiet_since and len(raw) > 400 and time.time() - quiet_since > 1.2:
            break                      # it has finished drawing and is waiting
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

    banner = split_banner(raw)
    if not banner or looks_unfinished(banner):
        return 1
    (profile / WELCOME).write_bytes(banner)
    return 0


# What the assistant draws the FIRST time it is ever run in a config directory:
# a theme picker, a login choice, a question about a key it found in the
# environment. None of that is a welcome screen, and keeping one would show a
# participant a setup question at the moment they are supposed to be asking for
# a change. Setup runs the assistant once before this, which is what gets it past
# these, and this is the check that says so rather than assuming it.
FIRST_RUN = ("Let's get started", "Choose the text style", "Select login method",
             "Do you want to use this API key")


def looks_unfinished(banner: bytes) -> bool:
    text = banner.decode("utf-8", "replace")
    stripped = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    squashed = re.sub(r"\s+", "", stripped)
    return any(re.sub(r"\s+", "", q) in squashed for q in FIRST_RUN)


def split_banner(raw: bytes) -> bytes | None:
    """Keep what was drawn above the input box, and drop the box itself.

    The box is redrawn on every keystroke, so it has to be ours. Everything above
    it is drawn once and never again, which is exactly the part worth keeping.
    The cut is the last top-left box corner in the stream, because the input box
    is the last thing drawn and no assistant message follows it on an empty
    session.
    """
    corner = "╭".encode()
    if not raw:
        return None
    if corner not in raw:
        return raw
    cut = raw.rfind(corner)
    if cut <= 0:
        return raw
    banner = raw[:cut]
    # A trailing partial line before the box would leave the cursor mid row.
    return banner.rstrip(b" \t")


def show_banner(workspace: Path) -> None:
    captured = workspace / PROFILE / WELCOME
    if captured.exists():
        sys.stdout.buffer.write(captured.read_bytes())
        sys.stdout.flush()
        return
    sys.stdout.write(FALLBACK.format(cwd=workspace))
    sys.stdout.flush()


# ── the input box ────────────────────────────────────────────────────────────

class Prompt:
    """A single line of input, drawn in a box, rendered as it is typed."""

    HINT = "  \033[2m? for shortcuts\033[0m"

    def __init__(self) -> None:
        self.width = min(shutil.get_terminal_size((100, 40)).columns, 200)
        self.text = ""
        self.rows = 0

    # The box is redrawn in place rather than appended, so a long line growing
    # past the right edge reflows instead of leaving a trail of dead boxes.
    def draw(self) -> None:
        if self.rows:
            sys.stdout.write(f"\033[{self.rows}A")
        sys.stdout.write("\033[J")
        inner = self.width - 4
        lines = wrap(self.text or "", inner) or [""]
        top = "╭" + "─" * (self.width - 2) + "╮"
        bottom = "╰" + "─" * (self.width - 2) + "╯"
        out = [top]
        for n, line in enumerate(lines):
            lead = "> " if n == 0 else "  "
            out.append("│ " + (lead + line).ljust(self.width - 3) + "│")
        out.append(bottom)
        out.append(self.HINT)
        # Carriage returns as well as line feeds. The box is redrawn while the
        # terminal is in raw mode, where a line feed on its own moves down a row
        # and leaves the cursor where it was, so every row of the box would start
        # one column further right than the row above it.
        sys.stdout.write("\r\n".join(out) + "\r\n")
        sys.stdout.flush()
        self.rows = len(out)

    def clear(self) -> None:
        """Take the box back off the screen, the way submitting one does."""
        if self.rows:
            sys.stdout.write(f"\033[{self.rows}A\033[J")
            sys.stdout.flush()
        self.rows = 0

    def read(self) -> str:
        """Read one request. Returns "" if they interrupted."""
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        sys.stdout.write("\033[?2004h")     # bracketed paste, as the real one does
        self.draw()
        try:
            tty.setraw(fd)
            while True:
                data = os.read(fd, 4096)
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                if "\x03" in text:                    # Ctrl+C
                    self.text = ""
                    break
                text = text.replace("\x1b[200~", "").replace("\x1b[201~", "")
                submitted = False
                for ch in text:
                    if ch in "\r\n":
                        # Inside a paste a newline is part of the request, and a
                        # request typed by hand ends at the first one. The paste
                        # arrives in one read, so a newline that is not the last
                        # character is wrapping rather than a submission.
                        if ch == text[-1] and self.text.strip():
                            submitted = True
                        else:
                            self.text += " "
                        continue
                    if ch in ("\x7f", "\b"):
                        self.text = self.text[:-1]
                        continue
                    if ch == "\x1b" or ord(ch) < 32:
                        continue
                    self.text += ch
                self.text = re.sub(r"\s+", " ", self.text).lstrip()
                self.draw()
                if submitted:
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            sys.stdout.write("\033[?2004l")
            sys.stdout.flush()
        return self.text.strip()


def wrap(text: str, width: int) -> list[str]:
    """Break a request across the box on word boundaries.

    Breaking on the character count instead left a continuation line starting
    with the space that had been at the wrap point, so the left edge of the text
    moved by one from line to line.
    """
    lines, line = [], ""
    for word in text.split(" "):
        while len(word) > width:                  # one unbroken run, no choice
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:width])
            word = word[width:]
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= width:
            line = f"{line} {word}"
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _set_size(fd: int, rows: int, cols: int) -> None:
    import fcntl
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# ── the daemon around the replay ─────────────────────────────────────────────
#
# The player refuses to write while a live daemon owns the same files, and the
# participant should not be running commands to stop and start one. So this does
# it: quiet before the replay, running again the moment they take over.

def stop_daemon(workspace: Path, timeout: float = 10.0) -> bool:
    pid = player.daemon_pid(workspace)
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGINT)
    except OSError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if player.daemon_pid(workspace) is None:
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True


def start_daemon(workspace: Path, codoc_bin: Path) -> int | None:
    """Start the daemon behind the session, with its output in a log.

    Behind, because a participant watching a terminal print index passes is
    watching the tool work rather than reviewing their project, and because the
    only reason it was ever in front of them was that somebody had to type the
    command.
    """
    if not codoc_bin or not Path(codoc_bin).exists():
        return None
    logs = workspace / ".codoc"
    logs.mkdir(exist_ok=True)
    handle = open(logs / "watch.log", "ab", buffering=0)
    proc = subprocess.Popen(
        [str(codoc_bin), "watch", "--root", str(workspace)],
        stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
        start_new_session=True)
    return proc.pid


# ── the first turn ───────────────────────────────────────────────────────────

def first_turn(workspace: Path, frames: Path, codoc_bin: Path | None,
               speed: float) -> int:
    if not (frames / "manifest.json").exists():
        print(f"no recorded session in {frames}", file=sys.stderr)
        return 2

    show_banner(workspace)
    box = Prompt()
    request = box.read()
    box.clear()
    if not request:
        return 130

    stop_daemon(workspace)
    # The recorded frames carry the request as the agent's own first line, so
    # nothing is echoed here. A participant who mistyped their paste still sees
    # the request the change was made from, which is also the one the assistant
    # is resumed on.
    code = player.play(workspace, frames, speed=speed, step=False,
                       do_reset=True, do_transcript=True)
    if code:
        return code

    session = session_id(frames)
    profile = workspace / PROFILE
    profile.mkdir(parents=True, exist_ok=True)
    (profile / HANDOVER).write_text(json.dumps({
        "at_ms": int(time.time() * 1000),
        "request": request,
        "frames": str(frames),
        "session_id": session or "",
    }, indent=2) + "\n")

    if codoc_bin:
        start_daemon(workspace, Path(codoc_bin))
    return 0


def session_id(frames: Path) -> str | None:
    source = frames / "transcript.jsonl"
    if not source.exists():
        return None
    for line in source.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("sessionId"):
            return entry["sessionId"]
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("play", help="take the first turn and play the recording")
    p.add_argument("workspace", type=Path)
    p.add_argument("frames", type=Path)
    p.add_argument("--codoc-bin", type=Path, default=None,
                   help="start this daemon again once the recording has played")
    p.add_argument("--speed", type=float, default=1.0)

    c = sub.add_parser("capture", help="keep this machine's own opening screen")
    c.add_argument("workspace", type=Path)

    args = parser.parse_args(argv)
    if args.mode == "capture":
        return capture(args.workspace.expanduser().resolve())
    try:
        return first_turn(args.workspace.expanduser().resolve(), args.frames,
                          args.codoc_bin, args.speed)
    except player.Leaked as leak:
        print(f"\nthis recording is not neutral, so it is not being replayed.\n{leak}",
              file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
