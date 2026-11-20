"""The hub's claim on a checkout: the lock on serve.lock, not the pid in it."""
import json
import subprocess
import sys

import pytest

from codoc.serve.supervise import DaemonSupervisor, OwnershipError
from codoc.workspace.layout import SERVE_LOCK


def test_a_serve_lock_naming_some_other_live_program_claims_nothing(tmp_path):
    """After a crash or a reboot the pid in serve.lock can belong to another
    program. The next hub starts, and supervises a daemon, without anyone
    deleting the file by hand."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    spawned: list[str] = []
    try:
        (cd / SERVE_LOCK).write_text(json.dumps({"pid": other.pid, "started_at": 0}))
        with DaemonSupervisor(str(tmp_path), str(cd), spawn=spawned.append):
            pass
    finally:
        other.kill()
        other.wait()
    assert spawned == [str(tmp_path)]


def test_one_hub_owns_a_checkout_until_it_stops(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    first = DaemonSupervisor(str(tmp_path), str(cd), spawn=lambda root: None)
    first.start()
    with pytest.raises(OwnershipError):
        DaemonSupervisor(str(tmp_path), str(cd), spawn=lambda root: None).start()
    first.stop()
    with DaemonSupervisor(str(tmp_path), str(cd), spawn=lambda root: None):
        pass
