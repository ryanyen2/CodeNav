"""SDK realize runner (codoc/loop/sdk_realize.py) — the event monitor and the
engine selection. All tests are SDK-free: the monitor is duck-typed and the
engine choice is patched, so nothing here needs claude-agent-sdk installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codoc.loop import autorealize
from codoc.loop.activity import read_activity
from codoc.loop.sdk_realize import (
    RealizeMonitor,
    _collect_feature_ids,
    consume_stream,
    format_realize_detail,
)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    codoc = root / ".codoc"
    codoc.mkdir(parents=True)
    (codoc / "tree.bindings.json").write_text(json.dumps({
        "version": 4,
        "by_file": {"src/colors.py": [
            {"symbol": "src/colors.py::PALETTE", "feature_id": "f-0000aaaa",
             "feature_title": "Color palette"},
        ]},
        # The `features` slice resolves a directive's feature_id → title for the
        # live realize-progress detail (status.json).
        "features": {"f-0000aaaa": {"title": "Color palette", "parent_id": None}},
    }))
    return str(root), str(codoc)


def _monitor(repo, lines):
    root, codoc = repo
    return RealizeMonitor(root, codoc, printer=lines.append, tty=False)


# -- terminal readout + activity signals --------------------------------------

def test_edit_prints_compact_line_and_marks_editing(repo):
    root, codoc = repo
    lines: list[str] = []
    m = _monitor(repo, lines)

    m.on_tool_use("Edit", {"file_path": str(Path(root) / "src/colors.py")})

    assert lines == ["  ● edit    src/colors.py  · Color palette"]
    assert m.writes == {"src/colors.py"}
    data = read_activity(codoc)
    assert data["touched"]["src/colors.py"]["mode"] == "write"
    assert data["touched"]["src/colors.py"]["feature_ids"] == ["f-0000aaaa"]
    assert data["features"]["f-0000aaaa"]["phase"] == "editing"


def test_read_is_quiet_and_recorded_as_read(repo):
    root, codoc = repo
    lines: list[str] = []
    m = _monitor(repo, lines)

    m.on_tool_use("Read", {"file_path": str(Path(root) / "src/colors.py")})

    assert lines == ["  ◦ read    src/colors.py"]
    assert m.writes == set()
    assert read_activity(codoc)["touched"]["src/colors.py"]["mode"] == "read"


def test_codoc_reflect_marks_reflecting_and_echoes_caused_by(repo):
    root, codoc = repo
    lines: list[str] = []
    m = _monitor(repo, lines)

    m.on_tool_use("mcp__codoc__codoc_reflect", {
        "caused_by": "d-1a2b3c4d",
        "ops": [{"kind": "attach", "feature_id": "f-0000aaaa",
                 "binds": ["src/colors.py::PALETTE"]}],
    })

    assert m.reflections == 1
    assert lines == ["  ⊙ reflect codoc_reflect  ⟨d-1a2b3c4d⟩"]
    assert read_activity(codoc)["features"]["f-0000aaaa"]["phase"] == "reflecting"


def test_fetch_run_and_quiet_tools(repo):
    lines: list[str] = []
    m = _monitor(repo, lines)

    m.on_tool_use("WebFetch", {"url": "https://example.com/tokens"})
    m.on_tool_use("Bash", {"description": "Run tests", "command": "pytest -q"})
    m.on_tool_use("TodoWrite", {"todos": []})
    m.on_tool_use("Grep", {"pattern": "x"})

    assert lines == [
        "  ⇣ fetch   https://example.com/tokens",
        "  $ run     Run tests",
    ]


def test_file_outside_the_repo_is_ignored(repo, tmp_path):
    lines: list[str] = []
    m = _monitor(repo, lines)
    m.on_tool_use("Edit", {"file_path": str(tmp_path / "elsewhere.py")})
    assert lines == [] and m.writes == set()


def test_handle_message_duck_types_blocks_and_result(repo):
    lines: list[str] = []
    m = _monitor(repo, lines)
    root = repo[0]

    msg = SimpleNamespace(content=[
        SimpleNamespace(text="thinking…"),  # TextBlock — no name → skipped
        SimpleNamespace(name="Edit", input={"file_path": str(Path(root) / "src/colors.py")}),
    ])
    m.handle_message(msg)
    assert m.writes == {"src/colors.py"}

    result = type("ResultMessage", (), {"is_error": True, "result": "boom"})()
    m.handle_message(result)
    assert m.errored is True
    assert "✗ failed" in m.summary() and "boom" in m.summary()


def test_summary_counts_writes_and_reflections(repo):
    lines: list[str] = []
    m = _monitor(repo, lines)
    root = repo[0]
    m.on_tool_use("Write", {"file_path": str(Path(root) / "src/colors.py")})
    m.on_tool_use("mcp__codoc__codoc_attach", {"feature_id": "f-0000aaaa"})
    s = m.summary()
    assert "✓ done" in s and "1 file(s) written" in s and "1 reflection(s)" in s


def test_notebook_edit_maps_notebook_path(repo):
    root, _codoc = repo
    lines: list[str] = []
    m = _monitor(repo, lines)
    m.on_tool_use("NotebookEdit", {"notebook_path": str(Path(root) / "src/colors.py")})
    assert m.writes == {"src/colors.py"}


def test_consume_stream_marks_failure_instead_of_raising(repo):
    """An SDK exception mid-stream must mark the run failed (so the caller's
    status recovery runs) rather than propagate and strand status=realizing."""
    import asyncio

    lines: list[str] = []
    m = _monitor(repo, lines)
    root = repo[0]

    async def broken_stream():
        yield SimpleNamespace(content=[
            SimpleNamespace(name="Write", input={"file_path": str(Path(root) / "src/colors.py")})])
        raise RuntimeError("socket dropped")

    asyncio.run(consume_stream(m, broken_stream()))

    assert m.writes == {"src/colors.py"}  # events before the failure still landed
    assert m.errored is True
    assert "socket dropped" in m.result_text
    assert "✗ failed" in m.summary()


def test_synchronous_query_raise_recovers_status(repo, monkeypatch):
    """A SYNCHRONOUS raise from query() (invalid options / auth failure, evaluated
    before the stream loop) must NOT strand status at 'realizing' — _run recovers
    status in a finally and returns a failure code rather than propagating."""
    import asyncio
    import sys
    import types
    from codoc.loop.sdk_realize import _run

    root, codoc = repo
    (Path(codoc) / "realize.md").write_text('### 1. STEER FEATURE: "x"\n  do the thing\n')

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeAgentOptions = lambda **kw: object()

    def _raise(**kw):
        raise RuntimeError("invalid permission mode")

    fake.query = _raise
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)

    # Call _run directly — it carries the fix; run_sdk_realize's sdk_available()
    # guard would reject the spec-less fake module before reaching it.
    rc = asyncio.run(_run(root, codoc, permission_mode="acceptEdits", printer=lambda *a, **k: None))

    assert rc == 1  # marked failed, not propagated
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["state"] != "realizing"  # status recovered, not stranded


def test_collect_feature_ids_is_recursive_and_deduped():
    assert _collect_feature_ids({
        "feature_id": "f-1",
        "ops": [{"feature_id": "f-2"}, {"nested": {"feature_id": "f-1"}}],
    }) == ["f-1", "f-2"]


# -- live realize-progress detail (status.json) -------------------------------
#
# The IDE parses status.detail with `parseRealizeProgress` in
# vscode-codoc/src/providers/tree-editor.ts. The regex is ANCHORED to the
# "implementing N/M" head (§6 robustness fix), so a stray "d/d" elsewhere in a
# detail string is never misread as progress:
#   /^\s*implementing\s+(\d+)\s*\/\s*(\d+)(?:\s*[:\-]\s*(.*))?/i
# These tests pin our producer (format_realize_detail / RealizeMonitor) to that
# exact shape. _parse_realize_progress below is the Python mirror of that regex.

import re

_TS_RE = re.compile(r"^\s*implementing\s+(\d+)\s*/\s*(\d+)(?:\s*[:\-]\s*(.*))?",
                    re.IGNORECASE)


def _parse_realize_progress(detail: str):
    """Python mirror of the TS `parseRealizeProgress` regex — the host parser
    our detail string must satisfy."""
    m = _TS_RE.search(detail or "")
    if not m:
        return None
    return {"done": int(m.group(1)), "total": int(m.group(2)),
            "current": (m.group(3) or "").strip()}


def test_parse_realize_progress_ignores_stray_slash_detail():
    """The anchored parser does NOT misread a non-progress detail that happens to
    carry a "d/d" (a path, a date, "N change(s) ready ... /codoc:sync")."""
    assert _parse_realize_progress("3 change(s) ready to implement — run /codoc:sync") is None
    assert _parse_realize_progress("edited src/a/2.py") is None
    assert _parse_realize_progress("implementing 2/5: x") == {"done": 2, "total": 5, "current": "x"}


def test_format_realize_detail_matches_the_ts_parser():
    detail = format_realize_detail(2, 5, "Color palette")
    assert detail == "implementing 2/5: Color palette"
    # The string the IDE actually consumes — round-trips through the host regex.
    assert _parse_realize_progress(detail) == {"done": 2, "total": 5,
                                               "current": "Color palette"}


def test_format_realize_detail_degrades_without_a_title():
    detail = format_realize_detail(1, 3, "")
    assert detail == "implementing 1/3"
    # Still parses — the title capture group is optional in the TS regex.
    assert _parse_realize_progress(detail) == {"done": 1, "total": 3, "current": ""}
    # A whitespace-only title is treated as absent (no dangling ": ").
    assert format_realize_detail(1, 3, "   ") == "implementing 1/3"


def _seed_manifest(codoc, directives):
    from codoc.loop.edits import Directive, write_manifest

    (Path(codoc) / "realize.md").write_text(
        "".join(f'### {i}. AMEND FEATURE\n  body\n' for i in range(1, len(directives) + 1)))
    write_manifest(codoc, [Directive(**d) for d in directives])


def test_reflect_advances_progress_and_writes_parseable_detail(repo):
    """As each directive's codoc_reflect lands, status.detail reports
    done/total: <feature title> in the shape parseRealizeProgress consumes."""
    root, codoc = repo
    _seed_manifest(codoc, [
        {"id": "d-aaaa1111", "feature_id": "f-0000aaaa", "kind": "amend"},
        {"id": "d-bbbb2222", "feature_id": "f-0000aaaa", "kind": "amend"},
    ])
    lines: list[str] = []
    m = _monitor(repo, lines)
    assert m._total == 2

    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["state"] == "realizing"
    assert state["detail"] == "implementing 1/2: Color palette"
    assert _parse_realize_progress(state["detail"])["current"] == "Color palette"

    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-bbbb2222"})
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["detail"] == "implementing 2/2: Color palette"


def test_progress_is_idempotent_per_directive_and_clamps(repo):
    """A directive that reflects twice is counted once; done never exceeds total."""
    root, codoc = repo
    _seed_manifest(codoc, [{"id": "d-aaaa1111", "feature_id": "f-0000aaaa", "kind": "amend"}])
    m = _monitor(repo, [])

    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})
    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})  # repeat
    assert m._done == 1
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["detail"] == "implementing 1/1: Color palette"


def test_no_manifest_leaves_detail_alone(repo):
    """Zero directives (no manifest) → no per-directive progress is written; the
    reflect path must not crash and must not emit a status.json."""
    root, codoc = repo
    m = _monitor(repo, [])
    assert m._total == 0
    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-zzzz9999"})
    assert m._done == 0
    assert not (Path(codoc) / "status.json").exists()  # static detail untouched


def test_unknown_feature_id_falls_back_to_the_id(repo):
    """A directive whose feature_id is absent from the sidecar degrades the title
    to the feature id (graceful, still parses)."""
    root, codoc = repo
    _seed_manifest(codoc, [{"id": "d-cccc3333", "feature_id": "f-ffffffff", "kind": "amend"}])
    m = _monitor(repo, [])
    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-cccc3333"})
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["detail"] == "implementing 1/1: f-ffffffff"
    assert _parse_realize_progress(state["detail"])["current"] == "f-ffffffff"


def test_empty_caused_by_never_advances_progress(repo):
    """Every codoc MCP tool defaults ``caused_by=""`` and bookkeeping reflect/attach
    calls flow through ``_advance_progress`` too — an untagged call must NOT count as
    directive progress (regression: the falsy-string path used to bypass the dedup
    guard and bump ``_done`` to N/N while the agent was still on directive 1)."""
    root, codoc = repo
    _seed_manifest(codoc, [
        {"id": "d-aaaa1111", "feature_id": "f-0000aaaa", "kind": "amend"},
        {"id": "d-bbbb2222", "feature_id": "f-0000aaaa", "kind": "amend"},
    ])
    m = _monitor(repo, [])
    assert m._total == 2

    # A reflect with no caused_by, and a plain bookkeeping MCP tool (also caused_by=""):
    m.on_tool_use("mcp__codoc__codoc_reflect", {"ops": []})
    m.on_tool_use("mcp__codoc__codoc_attach", {"feature_id": "f-0000aaaa"})
    assert m._done == 0
    assert not (Path(codoc) / "status.json").exists()  # no spurious progress written

    # A genuinely tagged reflect still advances exactly one.
    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})
    assert m._done == 1
    assert json.loads((Path(codoc) / "status.json").read_text())["detail"] == \
        "implementing 1/2: Color palette"


def test_total_counts_only_handed_off_directives(repo):
    """A DRAFT directive (``handed_off=False``) lives in the manifest but is not in
    realize.md and won't realize this epoch — it must not inflate the denominator,
    else the avatar can never reach done/done."""
    root, codoc = repo
    _seed_manifest(codoc, [
        {"id": "d-aaaa1111", "feature_id": "f-0000aaaa", "kind": "amend"},
        {"id": "d-bbbb2222", "feature_id": "f-0000aaaa", "kind": "amend",
         "handed_off": False},  # a held draft — not realizable this epoch
    ])
    m = _monitor(repo, [])
    assert m._total == 1  # only the handed-off directive

    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})
    assert json.loads((Path(codoc) / "status.json").read_text())["detail"] == \
        "implementing 1/1: Color palette"


def test_total_tracks_directives_appended_mid_epoch(repo):
    """The realize queue APPENDS, never clobbers (CLAUDE.md). The denominator is
    re-read on each landing, so a directive queued after the monitor started still
    grows ``_total`` instead of the progress sticking past 'done'."""
    from codoc.loop.edits import Directive, write_manifest

    root, codoc = repo
    _seed_manifest(codoc, [{"id": "d-aaaa1111", "feature_id": "f-0000aaaa", "kind": "amend"}])
    m = _monitor(repo, [])
    assert m._total == 1

    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-aaaa1111"})
    assert json.loads((Path(codoc) / "status.json").read_text())["detail"] == \
        "implementing 1/1: Color palette"

    # Loop B appends a second handed-off directive mid-epoch.
    write_manifest(codoc, [
        Directive(id="d-aaaa1111", feature_id="f-0000aaaa", kind="amend"),
        Directive(id="d-bbbb2222", feature_id="f-0000aaaa", kind="amend"),
    ])
    m.on_tool_use("mcp__codoc__codoc_reflect", {"caused_by": "d-bbbb2222"})
    assert json.loads((Path(codoc) / "status.json").read_text())["detail"] == \
        "implementing 2/2: Color palette"


# -- realizing-lease heartbeat (review #12) -----------------------------------

def test_tool_activity_heartbeats_the_realizing_lease(repo):
    """A live pass's own tool activity renews the realizing lease, so a single
    long directive (many tool calls, no intervening reflect) never lets it decay
    to awaiting_impl mid-pass and invite a second /codoc:sync onto the queue."""
    import os
    import time

    from codoc.loop import status

    root, codoc = repo
    (Path(codoc) / "realize.md").write_text('### 1. AMEND FEATURE\n  body\n')
    status.write_status(codoc, status.REALIZING, detail="implementing 1/1: Color palette")
    old = time.time() - (status.REALIZING_LEASE_SECONDS - 5)  # nearly expired
    os.utime(status.status_path(codoc), (old, old))

    m = _monitor(repo, [])
    m.on_tool_use("Bash", {"command": "pytest -q", "description": "run tests"})

    # Lease clock reset; detail preserved (heartbeat never blanks live progress).
    assert status.realizing_is_fresh(codoc) is True
    assert json.loads(status.status_path(codoc).read_text())["detail"] == \
        "implementing 1/1: Color palette"


def test_heartbeat_is_throttled(repo):
    """The heartbeat writes at most once per interval — a burst of tool calls does
    not churn status.json on every action."""
    from codoc.loop import status

    root, codoc = repo
    (Path(codoc) / "realize.md").write_text('### 1. AMEND FEATURE\n  body\n')
    status.write_status(codoc, status.REALIZING, detail="implementing 1/1: x")
    m = _monitor(repo, [])

    writes = {"n": 0}
    orig = status.touch_realizing_lease

    def counting(cd):
        writes["n"] += 1
        return orig(cd)

    from codoc.loop import sdk_realize
    sdk_realize.status_mod.touch_realizing_lease = counting
    try:
        clock = {"t": 1000.0}
        m._maybe_heartbeat(_clock=lambda: clock["t"])          # first → fires
        m._maybe_heartbeat(_clock=lambda: clock["t"] + 5)      # +5s → throttled
        m._maybe_heartbeat(_clock=lambda: clock["t"] + 30)     # +30s → throttled
        m._maybe_heartbeat(_clock=lambda: clock["t"] + 70)     # +70s → fires again
    finally:
        sdk_realize.status_mod.touch_realizing_lease = orig

    assert writes["n"] == 2


# -- engine selection -----------------------------------------------------------

def test_auto_engine_prefers_sdk_when_available(repo):
    root, codoc = repo
    fake = object()
    with patch("codoc.loop.sdk_realize.sdk_available", return_value=True), \
         patch.object(autorealize.subprocess, "Popen", return_value=fake) as popen:
        proc = autorealize.spawn_realize(root, codoc)
    assert proc is fake
    cmd = popen.call_args[0][0]
    assert cmd[1:] == ["-m", "codoc.loop.sdk_realize", root]
    state = json.loads((Path(codoc) / "status.json").read_text())
    assert state["state"] == "realizing" and "sdk" in state["detail"]


def test_auto_engine_falls_back_to_cli(repo):
    root, codoc = repo
    fake = object()
    with patch("codoc.loop.sdk_realize.sdk_available", return_value=False), \
         patch.object(autorealize, "find_claude", return_value="/usr/bin/claude"), \
         patch.object(autorealize.subprocess, "Popen", return_value=fake) as popen:
        proc = autorealize.spawn_realize(root, codoc)
    assert proc is fake
    assert popen.call_args[0][0] == ["/usr/bin/claude", "-p", "/codoc:sync"]


def test_sdk_engine_unavailable_returns_none(repo):
    root, codoc = repo
    with patch("codoc.loop.sdk_realize.sdk_available", return_value=False):
        assert autorealize.spawn_realize(root, codoc, engine="sdk") is None
