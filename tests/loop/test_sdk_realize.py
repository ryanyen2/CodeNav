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
from codoc.loop.sdk_realize import RealizeMonitor, _collect_feature_ids


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


def test_collect_feature_ids_is_recursive_and_deduped():
    assert _collect_feature_ids({
        "feature_id": "f-1",
        "ops": [{"feature_id": "f-2"}, {"nested": {"feature_id": "f-1"}}],
    }) == ["f-1", "f-2"]


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
