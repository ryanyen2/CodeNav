"""Run the realize queue through the Claude Agent SDK.

``claude_agent_sdk.query("/codoc:sync")`` streams every agent message back, so
codoc reacts to each action as it happens:

* **terminal** — one compact line per action (edit / read / reflect / fetch /
  run), so the person can see what the agent is doing without reading the
  diff. Plain text, dim ANSI only on a tty.
* **activity.json** — every file touch is recorded through the same code path
  the interactive hooks use (``hook._handle_tool``), and a ``mcp__codoc__*``
  call marks the features it names ``reflecting``.
* **status.json** — the pass holds the realizing lease and renews it from its
  own tool activity.

Runnable as ``python -m codoc.claude.sdk_realize <root>`` so the daemon can
track it as a child process, and called in the foreground by ``codoc realize``.
The SDK import is guarded: :func:`sdk_available` gates engine selection, and
everything event-side is duck-typed, so tests need no SDK installed.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from codoc.daemon.epoch import PHASE_REFLECTING, mark_feature_phase
from codoc.daemon.publish import REALIZE_OWNER_ENV, renew_realizing, write_realizing
from codoc.workspace.layout import CODOC_DIR, REALIZE

Printer = Callable[[str], None]

_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_READ_TOOLS = frozenset({"Read"})
# Tools that are pure orchestration noise in a compact readout.
_QUIET_TOOLS = frozenset({"TodoWrite", "Task", "Glob", "Grep", "AskUserQuestion"})

_DETAIL = "implementing (sdk) — codoc realize"

# How often tool activity renews the realizing lease. Well under the lease
# itself, so one long directive never lets it lapse mid-pass, and coarse enough
# not to rewrite status.json on every tool call.
_HEARTBEAT_INTERVAL_SECONDS = 60.0


def sdk_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("claude_agent_sdk") is not None


def resolve_engine(engine: str) -> str:
    """``auto`` → ``sdk`` when claude-agent-sdk is importable, else ``cli``."""
    if engine == "auto":
        return "sdk" if sdk_available() else "cli"
    return engine


def _dim(s: str, tty: bool) -> str:
    return f"\x1b[2m{s}\x1b[0m" if tty else s


def _collect_feature_ids(value: Any) -> list[str]:
    """Recursively harvest ``feature_id`` values from a tool-input payload."""
    out: list[str] = []
    if isinstance(value, dict):
        fid = value.get("feature_id")
        if isinstance(fid, str) and fid:
            out.append(fid)
        for v in value.values():
            out.extend(_collect_feature_ids(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_collect_feature_ids(v))
    return list(dict.fromkeys(out))


class RealizeMonitor:
    """Streamed agent events → one compact terminal line each, plus the
    activity and lease signals. SDK-free (duck-typed messages) so it is
    unit-testable."""

    def __init__(self, root_dir: str, codoc_dir: str, *,
                 printer: Printer = print, tty: bool | None = None) -> None:
        self.root_dir = str(root_dir)
        self.codoc_dir = str(codoc_dir)
        self.printer = printer
        self.tty = sys.stdout.isatty() if tty is None else tty
        self.writes: set[str] = set()
        self.reflections = 0
        self.errored = False
        self.result_text = ""
        self._started = time.monotonic()
        self._last_heartbeat = 0.0  # monotonic ts of the last lease renewal

    # -- helpers ---------------------------------------------------------

    def _rel(self, path: str) -> str | None:
        from codoc.claude.hook import _rel

        return _rel(path, self.root_dir)

    def _line(self, glyph: str, verb: str, detail: str, *, quiet: bool = False) -> None:
        text = f"  {glyph} {verb:<8}{detail}"
        self.printer(_dim(text, self.tty) if quiet else text)

    def _maybe_heartbeat(self, *, _clock: Callable[[], float] = time.monotonic) -> None:
        """Renew the realizing lease from the pass's own tool activity, at most
        once per :data:`_HEARTBEAT_INTERVAL_SECONDS`. Any tool call proves the
        pass is alive. No-op unless status.json already says ``realizing``."""
        now = _clock()
        if now - self._last_heartbeat < _HEARTBEAT_INTERVAL_SECONDS:
            return
        self._last_heartbeat = now
        try:
            renew_realizing(self.codoc_dir)
        except Exception:  # noqa: BLE001 — the heartbeat is advisory, never break realize
            pass

    def _record_touch(self, name: str, tool_input: dict) -> None:
        """Record the touch as the interactive PreToolUse hook would. Idempotent
        with the in-session hook."""
        from codoc.claude.hook import _handle_tool

        try:
            # phase="pre": a streamed tool_use block arrives when the tool is
            # REQUESTED, matching the interactive PreToolUse hook semantics.
            _handle_tool({"tool_name": name, "tool_input": tool_input},
                         self.codoc_dir, phase="pre")
        except Exception:  # noqa: BLE001 — signals never break the run
            pass

    # -- event entrypoints --------------------------------------------------

    def on_tool_use(self, name: str, tool_input: dict | None) -> None:
        tool_input = tool_input or {}
        # Any tool activity (including the quiet tools below) is evidence the
        # pass is alive: renew the lease before the early returns.
        self._maybe_heartbeat()
        if name in _QUIET_TOOLS:
            return

        if name in _WRITE_TOOLS or name in _READ_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            rel = self._rel(path)
            if rel is None:
                return
            self._record_touch(name, {**tool_input, "file_path": path})
            if name in _WRITE_TOOLS:
                self.writes.add(rel)
                self._line("●", "edit", rel)
            else:
                self._line("◦", "read", rel, quiet=True)
            return

        if name.startswith("mcp__codoc__"):
            fids = _collect_feature_ids(tool_input)
            if fids:
                mark_feature_phase(self.codoc_dir, fids, PHASE_REFLECTING)
            self.reflections += 1
            caused = tool_input.get("caused_by") or ""
            tail = f"  ⟨{caused}⟩" if caused else ""
            self._line("⊙", "reflect", name.removeprefix("mcp__codoc__") + tail)
            return

        if name == "WebFetch":
            self._line("⇣", "fetch", tool_input.get("url") or "")
            return

        if name == "Bash":
            desc = tool_input.get("description") or (tool_input.get("command") or "")[:60]
            self._line("$", "run", desc, quiet=True)
            return

    def handle_message(self, msg: Any) -> None:
        """Duck-typed: AssistantMessage carries ``content`` blocks (a tool-use
        block has ``name`` + ``input``); ResultMessage carries the outcome."""
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                name = getattr(block, "name", None)
                if isinstance(name, str):
                    inp = getattr(block, "input", None)
                    self.on_tool_use(name, inp if isinstance(inp, dict) else {})
        if type(msg).__name__ == "ResultMessage":
            self.errored = bool(getattr(msg, "is_error", False))
            self.result_text = str(getattr(msg, "result", "") or "")

    def summary(self) -> str:
        elapsed = int(time.monotonic() - self._started)
        if self.errored:
            tail = f" — {self.result_text}" if self.result_text else ""
            return f"  ✗ failed · {elapsed}s{tail}"
        return (f"  ✓ done · {len(self.writes)} file(s) written · "
                f"{self.reflections} reflection(s) · {elapsed}s")


async def consume_stream(monitor: RealizeMonitor, stream: Any) -> None:
    """Drain the SDK message stream into the monitor. An exception mid-stream
    (SDK error, broken pipe, rate limit) marks the run failed instead of
    propagating."""
    try:
        async for msg in stream:
            monitor.handle_message(msg)
    except Exception as exc:  # noqa: BLE001 — the queue file survives; report it
        monitor.errored = True
        monitor.result_text = monitor.result_text or f"{type(exc).__name__}: {exc}"


async def _run(root_dir: str, codoc_dir: str, *, permission_mode: str,
               printer: Printer) -> int:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        cwd=root_dir,
        permission_mode=permission_mode,
        # Load the repo's .mcp.json (codoc MCP server), .claude/settings.json
        # (the codoc hooks) and commands (/codoc:sync) — the SDK loads no
        # filesystem settings unless asked.
        setting_sources=["user", "project", "local"],
    )

    monitor = RealizeMonitor(root_dir, codoc_dir, printer=printer)
    # Name this pass, in status.json and in the environment the agent inherits.
    # Without the token that agent reads the lease written here, concludes
    # another pass owns the queue, and stands down. A pass the daemon started
    # arrives already named.
    inherited = os.environ.get(REALIZE_OWNER_ENV)
    owner = inherited or uuid.uuid4().hex
    os.environ[REALIZE_OWNER_ENV] = owner
    try:
        write_realizing(codoc_dir, detail=_DETAIL, owner=owner)
    except Exception:  # noqa: BLE001 — status is advisory
        pass

    printer("codoc realize · /codoc:sync · claude-agent-sdk")
    # Mark the epoch as a realize pass's for the spawned CLI (the SessionStart
    # hook reads this); restored afterwards so a foreground `codoc realize`
    # doesn't leak the mutation into its process.
    prev_origin = os.environ.get("CODOC_EPOCH_ORIGIN")
    os.environ.setdefault("CODOC_EPOCH_ORIGIN", "loop_b")
    try:
        await consume_stream(monitor, query(prompt="/codoc:sync", options=options))
    except Exception:  # noqa: BLE001 — a SYNCHRONOUS raise from query() (invalid
        # options, auth failure) lands here; mark failed, never propagate.
        monitor.errored = True
    finally:
        if prev_origin is None:
            os.environ.pop("CODOC_EPOCH_ORIGIN", None)
        else:
            os.environ["CODOC_EPOCH_ORIGIN"] = prev_origin
        # The token names a pass that has ended. Left set, it would exempt a
        # later realize in this same process from a lease it does not own.
        if inherited is None:
            os.environ.pop(REALIZE_OWNER_ENV, None)
    printer(monitor.summary())
    return 1 if monitor.errored else 0


def run_sdk_realize(root_dir: str, codoc_dir: str | None = None, *,
                    permission_mode: str = "acceptEdits",
                    printer: Printer = print) -> int:
    """Implement the queued ``realize.md`` via the Claude Agent SDK (blocking).

    Returns a process-style exit code (0 ok / 1 failed / 2 unavailable)."""
    import asyncio

    codoc = codoc_dir or str(Path(root_dir) / CODOC_DIR)
    if not (Path(codoc) / REALIZE).exists():
        printer("codoc realize · nothing queued (.codoc/realize.md absent)")
        return 0
    if not sdk_available():
        printer("codoc realize · claude-agent-sdk not installed "
                "(pip install 'codoc[sdk]') — falling back is the caller's call")
        return 2
    return asyncio.run(_run(root_dir, codoc, permission_mode=permission_mode,
                            printer=printer))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else "."
    return run_sdk_realize(root)


if __name__ == "__main__":
    raise SystemExit(main())
