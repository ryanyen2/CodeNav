"""The MCP server process."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from codoc.daemon import client
from codoc.workspace import Workspace
from codoc.workspace import log as L
from tests.helpers import T, seed

REPO = Path(__file__).resolve().parents[2]


def test_a_stopped_server_leaves_a_whole_session_log(tmp_path):
    """Claude Code stops the server with signals. The session log it leaves
    replays whole."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    before = set(L.list_sessions(cd))
    # The server's own `main`, with the stdio loop replaced by one write and a wait.
    script = f"""
import time
from codoc.daemon import client
from codoc.mcp import server
def run():
    client.ingest({str(cd)!r}, client="agent-claude-code", bid="p1", path="text",
                  base_text="Retries once.", new_md="Retries twice.")
    print("ready", flush=True)
    time.sleep(60)
server.mcp.run = run
server.main()
"""
    p = subprocess.Popen([sys.executable, "-c", script], cwd=REPO, stdout=subprocess.PIPE)
    assert p.stdout.readline().strip() == b"ready"
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
    [log] = set(L.list_sessions(cd)) - before
    assert L.read_session(log).valid and L.read_session(log).ops


def test_a_signal_during_an_apply_stops_the_server_after_the_batch(tmp_path):
    """FastMCP runs a tool in a worker thread, so the stop signal can arrive
    while it applies. The log ends with the whole batch."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    before = set(L.list_sessions(cd))
    script = f"""
import threading, time
from codoc.daemon import client
from codoc.mcp import server
from codoc.workspace import log as L
append = L.SessionWriter.append
def slow(self, op, sub=None):
    append(self, op, sub)
    print("mid", flush=True)
    time.sleep(0.3)
L.SessionWriter.append = slow
def run():
    threading.Thread(target=client.ingest, args=({str(cd)!r},), kwargs=dict(
        client="agent-claude-code", bid="p1", path="text", base_text="Retries once.",
        new_md="Retries twice, then gives up.")).start()
    time.sleep(60)
server.mcp.run = run
server.main()
"""
    p = subprocess.Popen([sys.executable, "-c", script], cwd=REPO, stdout=subprocess.PIPE)
    assert p.stdout.readline().strip() == b"mid"
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)
    [log] = set(L.list_sessions(cd)) - before
    # The same write with no signal: the whole batch.
    ref = tmp_path / "ref" / ".codoc"
    ws = Workspace(ref); seed(ws); ws.close()
    client.ingest(ref, client="agent-claude-code", bid="p1", path="text", base_text="Retries once.",
                  new_md="Retries twice, then gives up.")
    client.release(ref)
    assert T(Workspace(cd, readonly=True).store, "p1") == T(Workspace(ref, readonly=True).store, "p1")


def test_sigint_then_sigterm_stop_the_real_server_once(tmp_path):
    """Claude Code sends SIGINT, then SIGTERM about 100 ms later. The second
    signal must not run the stop again during interpreter shutdown, where it
    aborted on stdin's buffer lock."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    before = set(L.list_sessions(cd))
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    env.pop("CODOC_AGENT", None)
    errf = tmp_path / "stderr"
    p = subprocess.Popen([sys.executable, "-m", "codoc.mcp.server"], cwd=tmp_path, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf.open("wb"))

    def call(msg: dict) -> None:
        p.stdin.write((json.dumps({"jsonrpc": "2.0", **msg}) + "\n").encode())
        p.stdin.flush()

    def answer(rid: int) -> dict:
        while (d := json.loads(p.stdout.readline())).get("id") != rid:
            pass
        return d
    call({"id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
    answer(1)
    call({"method": "notifications/initialized"})
    call({"id": 2, "method": "tools/call", "params": {"name": "codoc_reflect", "arguments": {
        "ops": [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice."}]}]}}})
    assert "error" not in answer(2)
    p.send_signal(signal.SIGINT)
    time.sleep(0.1)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)                  # stdin stays open, as Claude Code leaves it
    err = errf.read_text()
    assert p.returncode in (0, -signal.SIGINT, -signal.SIGTERM) and "Fatal Python error" not in err, err
    [log] = set(L.list_sessions(cd)) - before
    assert L.read_session(log).valid
