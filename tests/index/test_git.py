"""Commit subjects as rationale evidence (no git binary needed)."""
from __future__ import annotations

from codoc.doclang import char_budget
from codoc.index import git


def test_a_short_subject_is_measured_in_its_own_script(tmp_path, monkeypatch):
    """Eight Han characters state a reason; seven Latin ones ("fix bug") do not."""
    subjects = ["修复缓存过期竞态", "fix bug"]
    log = "".join(f"\x1e{i:040x}\x1f{s}\x1f\x1f\n\nf.py\n" for i, s in enumerate(subjects))
    monkeypatch.setattr(git, "_run_git_log", lambda _root: log)
    got = git.commit_rationales(tmp_path, ["f.py"], rescale=char_budget)
    assert [c["subject"] for c in got] == ["修复缓存过期竞态"]
