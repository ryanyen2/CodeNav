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
             "Do you want to use this API key",
             # The folder trust dialog, which is per project rather than per
             # machine. setup.sh pre-answers it in the profile it writes; this is
             # what stops a machine where that did not work from keeping a
             # security question as its welcome screen.
             "Is this a project you created")


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


# ── handing the workspace over, and back ─────────────────────────────────────
#
# The player writes the files the daemon owns, so the two must not run at once.
# The daemon is NOT ours to start: the extension starts it on activation and keeps
# a lock naming the process it owns. Killing it from outside left the extension
# believing it still had one, and starting our own behind its back gave the
# workspace two writers, which is how a participant's tree filled with proposals
# nobody asked for.
#
# So this declares itself instead. `.codoc/replay.lock` means "somebody else is
# writing here"; the extension stops its daemon while it exists and starts one
# again when it goes. Scanning skips `.lock`, so the file is never recorded into a
# frame and never deleted by the reset.

LOCK = "replay.lock"


def hand_over(workspace: Path, timeout: float = 10.0) -> Path:
    """Claim the workspace, and wait for the daemon to let go of it."""
    codoc = workspace / ".codoc"
    codoc.mkdir(exist_ok=True)
    lock = codoc / LOCK
    lock.write_text(json.dumps({"by": "replay", "pid": os.getpid()}) + "\n")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if player.daemon_pid(workspace) is None:
            return lock
        time.sleep(0.25)
    # It did not stand down, so either no editor is watching this workspace or its
    # watcher never fired. Stopping it directly is worse than not replaying at all
    # only if something else then restarts it, and the lock is what stops that.
    stop_daemon(workspace)
    return lock


def hand_back(workspace: Path) -> None:
    (workspace / ".codoc" / LOCK).unlink(missing_ok=True)


def stop_daemon(workspace: Path, timeout: float = 10.0) -> bool:
    """Last resort, when nothing answered the lock."""
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


# ── the first turn ───────────────────────────────────────────────────────────

# ── the session, in stages ───────────────────────────────────────────────────
#
# A recording used to play start to finish and then hand over a finished change.
# The part of the session codoc is FOR — a plan arriving as nodes, somebody
# answering it, a description moving when the code moves — went past read only,
# and a participant who tried to accept a proposal during it was told the verdict
# was not picked up, because the daemon was stopped for the whole run.
#
# So the recording is cut at the point the agent stops to ask. Playback runs to
# the checkpoint, gives the workspace back so the editor is live, and waits for an
# answer. The next segment was recorded AFTER the same answer, so a participant
# who accepts puts their store into the state it expects and playback continues
# consistently. A participant who REJECTS has diverged, which is the most
# interesting thing they can do: the recording stops there and they carry on with
# the live agent.

WAIT_POLL_S = 1.0
WAIT_TIMEOUT_S = 900.0


def pending_proposals(workspace: Path) -> int:
    """How much is waiting for the participant, as the editor counts it.

    TWO kinds, because the two checkpoints ask two different things and only one
    of them is a proposal. At the plan stop the agent has put nodes in the tree and
    they are pending events. At the build stop the loop has REWRITTEN descriptions
    to match code that already landed — those are applied, not proposed, and what
    is outstanding is the Keep / Restore verdict on each. Counting only the first
    made the second checkpoint pass straight through the moment it is there for.

    `by_event` is nested under `proposals` in the sidecar and was read from the top
    level, so this returned 0 whatever was pending and the player never waited at
    any checkpoint at all. Both keys are read here, and the shape is asserted by
    `test_replay.py` so a sidecar rename cannot quietly restore the same silence.
    """
    codoc = workspace / ".codoc"
    try:
        data = json.loads((codoc / "tree.bindings.json").read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    proposals = (data.get("proposals") or {}).get("by_event") or {}
    rewrites = data.get("auto_edits") or {}

    # A rewrite the reader has already answered is not outstanding, and the sidecar
    # cannot say so: the sidecar is the daemon's, and Keep is answered in the editor
    # and changes nothing else — the document already says what they agreed to. The
    # extension records the answer in `reviewed.host.jsonl` for exactly this reader.
    # Without it a participant clicked Keep, the count never moved, and the stop waited
    # out its full fifteen minutes with the live assistant unreachable behind it.
    answered = set()
    try:
        for line in (codoc / "reviewed.host.jsonl").read_text().splitlines():
            if line.strip():
                answered.add(json.loads(line).get("feature_id"))
    except (OSError, json.JSONDecodeError):
        pass
    return len(proposals) + len([f for f in rewrites if f not in answered])


def verdicts(workspace: Path) -> list[bool]:
    """Every accept/reject this workspace has recorded, oldest first.

    Both channels, because either may hold one: the editor APPENDS to
    `inbox.host.jsonl` (it has no cross-process lock) and the daemon folds that
    into `inbox.json` under the lock. A verdict read from only one of them is a
    verdict missed for as long as the fold takes.
    """
    codoc = workspace / ".codoc"
    out: list[bool] = []
    try:
        data = json.loads((codoc / "inbox.json").read_text())
        out += [bool(v.get("accept")) for v in data.get("verdicts", [])]
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    try:
        for line in (codoc / "inbox.host.jsonl").read_text().splitlines():
            if line.strip():
                out.append(bool(json.loads(line).get("accept")))
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return out


# What the participant did at a stop, and what the player should do about it.
ANSWERED = "answered"       # they agreed; the next segment is what follows from it
DIVERGED = "diverged"       # they rejected; the recording is no longer their session
NOTHING = "nothing"         # there was nothing to answer, so there was no stop
UNANSWERED = "unanswered"   # nobody clicked inside the timeout


def wait_for_an_answer(workspace: Path, asked: str,
                       timeout: float = WAIT_TIMEOUT_S) -> str:
    """Hold at a checkpoint until the participant has answered it.

    Returns which of the four above happened. `UNANSWERED` is treated as carry on:
    a session that stalls forever because somebody did not click is worse than one
    that continues without the answer.

    A REJECT is reported as `DIVERGED` rather than folded in with an accept. The
    two are not the same event: everything after the cut was recorded against a
    store in which the plan is live, so playing on after a rejection reinstates the
    plan the participant just turned down — quietly, because the checkpoint frame
    carries the store. They would watch their own decision be undone, and the
    session record would say they accepted a plan they rejected.
    """
    before = pending_proposals(workspace)
    if not before:
        return NOTHING
    seen = len(verdicts(workspace))
    print(f"\n{asked}", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        given = verdicts(workspace)[seen:]
        if any(v is False for v in given):
            return DIVERGED
        if pending_proposals(workspace) < before or given:
            return ANSWERED
        time.sleep(WAIT_POLL_S)
    return UNANSWERED


DEFAULT_ASK = ("I have sketched this as a plan in the tree. Accept the parts you "
               "want and I will build them.")


def checkpoint_texts(manifest: dict, stops: int) -> list[str]:
    """What the agent says at each stop, one per stop.

    A recording with two stops asks two different questions, so the manifest may
    carry a list. A single string is used at every stop, which is what a one-stop
    recording means by it, and a list too short falls back the same way rather
    than stopping the session over a missing sentence.
    """
    says = manifest.get("checkpoint_says")
    if isinstance(says, str):
        says = [says]
    says = [s for s in (says or []) if str(s).strip()]
    return [says[i] if i < len(says) else (says[-1] if says else DEFAULT_ASK)
            for i in range(max(stops, 0))]


def play_staged(workspace: Path, frames: Path, speed: float) -> int:
    manifest = json.loads((frames / "manifest.json").read_text())
    spans = player.segments(manifest)
    asks = checkpoint_texts(manifest, len(spans) - 1)
    for i, span in enumerate(spans):
        last = i == len(spans) - 1
        hand_over(workspace)
        try:
            code = player.play(workspace, frames, speed=speed, step=False,
                               do_reset=(i == 0), do_transcript=(i == 0),
                               span=span, tail=last)
        finally:
            # Whatever happened, give the workspace back. A lock left behind is a
            # workspace whose daemon never returns, and nothing on screen says so.
            hand_back(workspace)
        if code:
            return code
        if last:
            continue
        answer = wait_for_an_answer(workspace, asks[i])
        if answer == DIVERGED:
            # From here the recording is somebody else's session. The live agent
            # takes it on, which is the whole point of a rejection being allowed.
            print("\n● Understood — I will leave that as it is and we can work "
                  "from here.", flush=True)
            return 0
    return 0


def first_turn(workspace: Path, frames: Path, speed: float) -> int:
    if not (frames / "manifest.json").exists():
        print(f"no recorded session in {frames}", file=sys.stderr)
        return 2

    show_banner(workspace)
    box = Prompt()
    request = box.read()
    box.clear()
    if not request:
        return 130

    # The recorded frames carry the request as the agent's own first line, so
    # nothing is echoed here. A participant who mistyped their paste still sees
    # the request the change was made from, which is also the one the assistant
    # is resumed on.
    code = play_staged(workspace, frames, speed)
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
    p.add_argument("--speed", type=float, default=1.0)

    c = sub.add_parser("capture", help="keep this machine's own opening screen")
    c.add_argument("workspace", type=Path)

    args = parser.parse_args(argv)
    if args.mode == "capture":
        return capture(args.workspace.expanduser().resolve())
    try:
        return first_turn(args.workspace.expanduser().resolve(), args.frames,
                          args.speed)
    except player.Leaked as leak:
        print(f"\nthis recording is not neutral, so it is not being replayed.\n{leak}",
              file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
