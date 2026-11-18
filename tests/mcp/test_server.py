"""The MCP server process."""
import signal
import subprocess
import sys
from pathlib import Path

from codoc.workspace import Workspace
from codoc.workspace import log as L
from tests.helpers import seed

REPO = Path(__file__).resolve().parents[2]


def test_a_stopped_server_closes_its_session_log(tmp_path):
    """Claude Code stops the server with signals. Its session log must get the
    `closed` trailer, or a fold never archives it."""
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
    assert L.read_session(log).closed
