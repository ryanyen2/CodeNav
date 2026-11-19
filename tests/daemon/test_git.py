"""The daemon against everyday git: each test drives a real repository with
codoc's own `.codoc/.gitignore` and a `Daemon` (no model, no index).

The store is a function of the logs on disk (model.md I6): a batch that finds
HEAD moved, or its session file taken, rebuilds from disk before it applies
anything. The daemon's own writes never stop git from pulling, checking out or
popping a stash (concurrency.md, Git)."""
import json
import subprocess

import pytest

from codoc.calculus import ops as K
from codoc.workspace.layout import write_gitignore
from codoc.daemon.daemon import Daemon
from codoc.index.changes import ChangeSet
from codoc.workspace import Workspace
from codoc.workspace import inbox as I
from codoc.workspace.inbox import Submission
from tests.helpers import T, seed_loop

SEED = "Retries once before giving up."


@pytest.fixture
def git_env(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(k, v)


def _git(cwd, *argv, check=True):
    return subprocess.run(["git", *argv], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, git_env):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "c.py").write_text("def send(): ...\n")
    write_gitignore(root / ".codoc")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root, lambda *argv, check=True: _git(root, *argv, check=check)


def daemon(root):
    return Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)


def type_(d: Daemon, words: str, cid: str) -> None:
    """The editor host appends one keystroke batch to `inbox/ide.jsonl`,
    anchored on the text it shows; the daemon drains it."""
    last = list(d.ws.store.blocks["p1"].text().live_refs())[-1]
    I.submit(d.codoc_dir, "ide", [Submission(cid=cid, kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(last), "text": words})])
    d.process_batch([str(I.inbox_dir(d.codoc_dir) / "ide.jsonl")])


def ack_of(d: Daemon, cid: str) -> dict:
    lines = (I.inbox_dir(d.codoc_dir) / "ide.ack.jsonl").read_text().splitlines()
    return next(a for a in map(json.loads, lines) if a["cid"] == cid)


@pytest.mark.parametrize("take", [["stash", "-u"], ["clean", "-fdq", ".codoc/log"]], ids=["stash -u", "clean"])
def test_words_typed_after_git_takes_the_session_file_survive_a_rebuild(repo, take):
    """`git stash -u`, `git clean -fd` or VS Code's "Discard All Changes" takes
    the open session file. The next batch rebuilds from disk and opens a new
    session: words anchored on what git took are refused by name, and words
    typed after that land in a file a rebuild reads."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    git("add", "-A")
    git("commit", "-qm", "docs")
    type_(d, " One.", "c1")                   # HEAD moved: a new session; its file appears, untracked
    git(*take)
    type_(d, " Two.", "c2")                   # anchored after " One.", which git took
    type_(d, " Three.", "c3")                 # anchored on the tree the daemon rebuilt
    assert ack_of(d, "c2")["conflict"] == "unknown_anchor"
    d.ws.close()
    replayed = Workspace(d.codoc_dir, readonly=True)
    assert T(replayed.store, "p1") == T(d.ws.store, "p1") == SEED + " Three."


def test_a_session_file_git_brings_back_wakes_the_daemon_and_is_read(repo):
    """`git stash -u`, then `git stash pop`: the session file comes back with
    HEAD where it was. Its arrival starts a batch, and the batch reads it, so
    the editor shows the restored words and nobody types them twice."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    git("add", "-A")
    git("commit", "-qm", "docs")
    type_(d, " One.", "c1")
    typed = d.ws.writer.path
    git("stash", "-u")
    d.process_batch([])
    git("stash", "pop")
    assert d.wakes(None, str(typed))
    d.process_batch([str(typed)])
    assert T(d.ws.store, "p1") == SEED + " One."


def test_git_taking_or_putting_back_the_daemons_own_session_file_wakes_it(repo):
    """The daemon's appends to its own session file do not start a batch, but
    `git stash -u` taking that file does, and so does `git stash pop` putting
    it back, so the editor stops showing the stashed words at once and shows
    them again on the pop."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    git("add", "-A")
    git("commit", "-qm", "docs")
    type_(d, " One.", "c1")
    own = d.ws.writer.path
    assert not d.wakes(None, str(own))           # its own append
    git("stash", "-u")
    assert d.wakes(None, str(own))               # git took it
    git("stash", "pop")
    assert d.wakes(None, str(own))               # git put a file back at its name
    d.process_batch([str(own)])
    assert T(d.ws.store, "p1") == SEED + " One."


def test_commit_then_checkout_back_before_the_daemon_looks(repo):
    """`git checkout -b feat`, type, `git commit -am wip && git checkout main`:
    the first batch after sees main's sha, the one it saw last, but not the
    branch it saw last, and the checkout took its session file."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    d.process_batch([])
    git("add", "-A")
    git("commit", "-qm", "docs")
    d.process_batch([])
    git("checkout", "-qb", "feat")
    type_(d, " Feature words.", "f1")
    git("add", "-A")
    git("commit", "-qm", "wip")
    git("checkout", "-q", "main")
    d.process_batch([str(root / "c.py")])
    on_main = Workspace(d.codoc_dir, readonly=True)
    assert T(d.ws.store, "p1") == T(on_main.store, "p1") == SEED


def test_a_commit_does_not_leave_a_tracked_log_modified(repo):
    """After a commit the daemon's next batch starts a new session, so what is
    typed after it goes to a new file and the committed one is left alone, and
    a checkout of a branch without it works."""
    root, git = repo
    git("branch", "older")
    d = daemon(root)
    seed_loop(d.ws)
    git("add", "-A")
    git("commit", "-qm", "docs")
    type_(d, " After.", "c1")
    assert git("status", "--porcelain", "--untracked-files=no").stdout == ""
    co = git("checkout", "-q", "older", check=False)
    assert co.returncode == 0, co.stderr


def test_git_stash_pop_succeeds_after_the_person_typed(repo):
    """Code stashed before a pull, a few words typed, `git stash pop`: the
    export the daemon rewrites is not tracked, so the pop has nothing of
    codoc's to refuse over."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    d.process_batch([])
    git("add", "-A")
    git("commit", "-qm", "docs")
    type_(d, " One.", "c1")
    (root / "c.py").write_text("def send(): retry()\n")
    git("stash")
    type_(d, " Two.", "c2")
    pop = git("stash", "pop", check=False)
    assert pop.returncode == 0, pop.stderr


def test_switching_branches_with_unsaved_doc_edits(repo):
    """Words typed and not committed are in an untracked session file, which a
    checkout carries across, as it does uncommitted code."""
    root, git = repo
    d = daemon(root)
    seed_loop(d.ws)
    d.process_batch([])
    git("add", "-A")
    git("commit", "-qm", "docs")
    git("checkout", "-qb", "teammate")
    type_(d, " Theirs.", "t1")
    git("add", "-A")
    git("commit", "-qm", "their docs")
    git("checkout", "-q", "main")
    d.process_batch([])
    type_(d, " Mine.", "m1")
    co = git("checkout", "-q", "teammate", check=False)
    assert co.returncode == 0, co.stderr


def test_checking_out_a_branch_from_before_codoc_and_back(repo):
    """On a branch that predates the docs the daemon rebuilds and publishes;
    the export it leaves there does not stop the checkout back."""
    root, git = repo
    git("branch", "release-1.0")
    d = daemon(root)
    seed_loop(d.ws)
    d.process_batch([])
    git("add", "-A")
    git("commit", "-qm", "docs")
    d.process_batch([])
    git("checkout", "-q", "release-1.0")
    d.process_batch([str(root / "c.py")])
    back = git("checkout", "-q", "main", check=False)
    assert back.returncode == 0, back.stderr


def test_git_pull_with_unsaved_doc_edits_when_a_teammate_pushed_docs(tmp_path, git_env):
    """Alice types and pushes; Bob has typed and not committed, and pulls. His
    words are in an untracked session file, and the rebuild after the pull
    holds both people's."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    alice = tmp_path / "alice"
    _git(tmp_path, "clone", "-q", str(remote), str(alice))
    (alice / ".codoc").mkdir()
    write_gitignore(alice / ".codoc")
    (alice / "c.py").write_text("def send(): ...\n")
    a = daemon(alice)
    seed_loop(a.ws)
    a.process_batch([])
    _git(alice, "add", "-A")
    _git(alice, "commit", "-qm", "docs")
    _git(alice, "push", "-q", "origin", "main")
    bob = tmp_path / "bob"
    _git(tmp_path, "clone", "-q", str(remote), str(bob))
    b = daemon(bob)
    b.process_batch([])

    type_(a, " Alice.", "a1")
    _git(alice, "add", "-A")
    _git(alice, "commit", "-qm", "alice docs")
    _git(alice, "push", "-q", "origin", "main")
    type_(b, " Bob.", "b1")
    pull = _git(bob, "pull", "-q", "--no-rebase", "origin", "main", check=False)
    assert pull.returncode == 0, pull.stderr
    b.process_batch([])
    assert T(b.ws.store, "p1") in (SEED + " Alice. Bob.", SEED + " Bob. Alice.")


def test_a_workspace_set_up_to_track_the_export_is_told_how_to_stop(repo):
    """A `.codoc/.gitignore` an older codoc wrote still tracks `tree.codoc`.
    The daemon's start writes the current one, and since git already tracks
    the file, says how to untrack it rather than running git itself."""
    root, git = repo
    gitignore = root / ".codoc" / ".gitignore"
    gitignore.write_text(gitignore.read_text().replace("!config.json\n", "!config.json\n!tree.codoc\n"))
    (root / ".codoc" / "tree.codoc").write_text("# Client\n")
    git("add", "-A")
    git("commit", "-qm", "an older codoc")
    said: list[str] = []
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=said.append)
    d.start()
    d.ws.close()
    assert "!tree.codoc" not in gitignore.read_text()
    assert any("git rm --cached .codoc/tree.codoc" in line for line in said)
    assert git("ls-files", ".codoc/tree.codoc").stdout.strip() == ".codoc/tree.codoc"

