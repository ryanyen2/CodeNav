"""Whether a daemon owns a checkout: the lock on watch.pid, not the pid in it."""
import subprocess
import sys

from codoc.daemon.pidfile import daemon_running, hold_pidfile
from codoc.workspace.layout import PIDFILE
from tests.helpers import another_daemon


def test_a_pidfile_naming_some_other_live_program_is_not_a_daemon(tmp_path):
    """After a reboot the pid a killed daemon left can belong to another
    program. Taken for a daemon, it would send every write to an inbox nobody
    drains and keep `codoc watch` from starting."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (cd / PIDFILE).write_text(str(other.pid))
        assert not daemon_running(cd)
    finally:
        other.kill()
        other.wait()


def test_a_daemon_is_alive_while_it_holds_the_lock(tmp_path):
    """A second daemon cannot take the lock, and once the first is gone,
    however it went, the file it left behind names no daemon."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    with another_daemon(cd) as pid:
        assert daemon_running(cd) and (cd / PIDFILE).read_text() == str(pid)
        assert not hold_pidfile(cd)
    assert (cd / PIDFILE).exists() and not daemon_running(cd)
