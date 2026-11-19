"""Run the realize queue through the Claude Agent SDK.

``claude_agent_sdk.query("/codoc:realize")`` streams every agent message back, so
codoc reacts to each action as it happens:

* **terminal**: one compact line per action (edit / read / reflect / fetch /
  run), so the person can see what the agent is doing without reading the
  diff. Plain text, dim ANSI only on a tty.
* **activity.json**: every file touch reopens the agent epoch and records the
  file, as the interactive pre-tool hook does (``daemon.epoch``).
* **status.json**: the pass renews the realizing lease from its own tool
  activity. ``autorealize.start_realize`` takes the lease and starts this
  module as ``python -m codoc.claude.sdk_realize <root> [<permission mode>]``,
  for ``codoc realize`` and for the daemon.

The SDK import is guarded: :func:`sdk_available` gates engine selection, and
everything event-side is duck-typed, so tests need no SDK installed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

from codoc.daemon.epoch import reopen_epoch, repo_relative, touch_epoch
from codoc.daemon.publish import renew_realizing
from codoc.workspace.layout import CODOC_DIR, REALIZE

Printer = Callable[[str], None]

_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_READ_TOOLS = frozenset({"Read"})
# Tools that are pure orchestration noise in a compact readout.
_QUIET_TOOLS = frozenset({"TodoWrite", "Task", "Glob", "Grep", "AskUserQuestion"})

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
        return repo_relative(path, self.codoc_dir)

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
        except Exception:  # noqa: BLE001 (the heartbeat is advisory, never break realize)
            pass

    def _record_touch(self, rel: str) -> None:
        """Record the touch as the interactive pre-tool hook does: a streamed
        tool-use block arrives when the tool is requested, before it writes."""
        try:
            reopen_epoch(self.codoc_dir, None)
            touch_epoch(self.codoc_dir, rel)
        except Exception:  # noqa: BLE001 (signals never break the run)
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
            self._record_touch(rel)
            if name in _WRITE_TOOLS:
                self.writes.add(rel)
                self._line("●", "edit", rel)
            else:
                self._line("◦", "read", rel, quiet=True)
            return

        if name.startswith("mcp__codoc__"):
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
            tail = f": {self.result_text}" if self.result_text else ""
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
    except Exception as exc:  # noqa: BLE001 (the queue file survives; report it)
        monitor.errored = True
        monitor.result_text = monitor.result_text or f"{type(exc).__name__}: {exc}"


async def _run(root_dir: str, codoc_dir: str, *, permission_mode: str,
               printer: Printer) -> int:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        cwd=root_dir,
        permission_mode=permission_mode,
        # Load the codoc MCP server (registered at local scope for this path),
        # the hooks in .claude/settings.local.json and the /codoc:realize command.
        # The SDK loads no filesystem settings unless asked.
        setting_sources=["user", "project", "local"],
    )

    monitor = RealizeMonitor(root_dir, codoc_dir, printer=printer)
    printer("codoc realize · /codoc:realize · claude-agent-sdk")
    try:
        await consume_stream(monitor, query(prompt="/codoc:realize", options=options))
    except Exception:  # noqa: BLE001
        # A synchronous raise from query() (invalid options, auth failure) lands
        # here; mark failed, never propagate.
        monitor.errored = True
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
                "(pip install 'codoc[sdk]'). The caller decides whether to fall back.")
        return 2
    return asyncio.run(_run(root_dir, codoc, permission_mode=permission_mode,
                            printer=printer))


def main(argv: list[str] | None = None) -> int:
    """``python -m codoc.claude.sdk_realize [<root> [<permission mode>]]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    return run_sdk_realize(args[0] if args else ".", permission_mode=args[1] if len(args) > 1 else "acceptEdits")


if __name__ == "__main__":
    raise SystemExit(main())
