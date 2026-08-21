"""Which settings files earn a place in the index.

The rule under test is evidence, not naming: a settings file is indexed when the
repo's own code names it, so `pyproject.toml` stays out of a tree even though it is
the most prominent `.toml` in most repos, and a fixture nobody reads stays out too.
"""
from __future__ import annotations

import pathlib

import pytest
from cocoindex.resources.file import PatternFilePathMatcher

from codoc.pipelines.indexing import settings_scan
from codoc.pipelines.indexing.cocoindex_app import _EXCLUDED_PATTERNS, _INCLUDED_PATTERNS


def _matcher() -> PatternFilePathMatcher:
    """The indexer's own matcher, widened to see settings candidates."""
    return PatternFilePathMatcher(
        included_patterns=_INCLUDED_PATTERNS + settings_scan.CANDIDATE_PATTERNS,
        excluded_patterns=_EXCLUDED_PATTERNS,
    )


def _repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _scan(tmp_path: pathlib.Path) -> settings_scan.SettingsScan:
    return settings_scan.scan(tmp_path, _matcher())


def test_a_settings_file_the_code_opens_is_indexed(tmp_path):
    root = _repo(tmp_path, {
        "tally/summary.py": 'RULES = Path(__file__).parent / "rules.toml"\n',
        "tally/rules.toml": "[periods]\nmonth = \"made\"\n",
    })
    assert _scan(root).read_by_code == ["tally/rules.toml"]


def test_a_settings_file_nobody_reads_is_not(tmp_path):
    """The count is the whole report: a repo has many of these and none is news."""
    root = _repo(tmp_path, {
        "tally/summary.py": "value = 1\n",
        "tally/fixtures/sample.toml": "[x]\ny = 1\n",
    })
    scan = _scan(root)
    assert scan.read_by_code == []
    assert scan.unreferenced == 1


def test_the_packaging_manifest_is_never_a_decision(tmp_path):
    """Even though the repo's own tooling mentions it by name."""
    root = _repo(tmp_path, {
        "tools/release.py": 'MANIFEST = "pyproject.toml"\n',
        "pyproject.toml": '[project]\nname = "tally"\n',
    })
    scan = _scan(root)
    assert scan.read_by_code == []
    assert scan.unreferenced == 0  # not a candidate at all, so not counted as skipped


def test_a_mention_in_a_comment_still_counts(tmp_path):
    """It is somebody saying this file matters to this code, which is the question."""
    root = _repo(tmp_path, {
        "tally/summary.py": "# thresholds live in rules.toml\nvalue = 1\n",
        "tally/rules.toml": "[periods]\nmonth = \"made\"\n",
    })
    assert _scan(root).read_by_code == ["tally/rules.toml"]


def test_a_path_prefix_finds_the_file_it_names(tmp_path):
    """`config/rules.toml` in the source names the same file the walk found."""
    root = _repo(tmp_path, {
        "app.py": 'CONFIG = "config/rules.toml"\n',
        "config/rules.toml": "[periods]\nmonth = \"made\"\n",
    })
    assert _scan(root).read_by_code == ["config/rules.toml"]


def test_a_file_only_another_settings_file_mentions_is_not_read_by_code(tmp_path):
    """Code is the evidence. One config naming another says nothing about whether
    this codebase acts on it."""
    root = _repo(tmp_path, {
        "app.py": "value = 1\n",
        "deploy.toml": 'includes = "rules.toml"\n',
        "rules.toml": "[periods]\nmonth = \"made\"\n",
    })
    scan = _scan(root)
    assert scan.read_by_code == []
    assert scan.unreferenced == 2


def test_an_excluded_directory_is_as_invisible_here_as_in_the_walk(tmp_path):
    root = _repo(tmp_path, {
        "app.py": 'CONFIG = "rules.toml"\n',
        "node_modules/pkg/rules.toml": "[x]\ny = 1\n",
    })
    assert _scan(root).read_by_code == []


def test_a_repo_with_no_candidates_reads_nothing(tmp_path):
    """The cheap path: with nothing to look for, no source file is opened."""
    root = _repo(tmp_path, {"app.py": "value = 1\n"})
    opened: list[pathlib.Path] = []
    real = pathlib.Path.read_text

    def counting(self, *args, **kwargs):  # noqa: ANN001, ANN202
        opened.append(self)
        return real(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pathlib.Path, "read_text", counting)
        assert _scan(root).read_by_code == []
    assert [p for p in opened if p.suffix == ".py"] == []


def test_the_search_stops_once_every_candidate_is_accounted_for(tmp_path):
    """A repo reads its settings file near the top of the module that owns it, so
    the scan must not go on reading the rest of the codebase to prove it."""
    files = {"a_reader.py": 'CONFIG = "rules.toml"\n', "rules.toml": "[x]\ny = 1\n"}
    files.update({f"pkg/mod_{n:03d}.py": "value = 1\n" for n in range(30)})
    root = _repo(tmp_path, files)
    read: list[str] = []
    real = pathlib.Path.read_text

    def counting(self, *args, **kwargs):  # noqa: ANN001, ANN202
        read.append(self.name)
        return real(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pathlib.Path, "read_text", counting)
        assert _scan(root).read_by_code == ["rules.toml"]
    assert len(read) < 31


def test_a_format_this_process_cannot_parse_is_reported_not_dropped(tmp_path, monkeypatch):
    """A file skipped for a missing dependency and a file nobody reads are different
    facts about the same silence, and only the first is actionable."""
    monkeypatch.setattr("codoc.settings_files._yaml", None)
    root = _repo(tmp_path, {
        "app.py": 'CONFIG = "deploy.yaml"\n',
        "deploy.yaml": "queue:\n  workers: 4\n",
    })
    scan = _scan(root)
    assert scan.read_by_code == []
    assert scan.unreadable == ["deploy.yaml"]


def test_yaml_is_indexed_when_the_parser_is_there(tmp_path):
    pytest.importorskip("yaml")
    root = _repo(tmp_path, {
        "app.py": 'CONFIG = "deploy.yaml"\n',
        "deploy.yaml": "queue:\n  workers: 4\n",
    })
    assert _scan(root).read_by_code == ["deploy.yaml"]


def test_a_symlinked_directory_is_not_followed(tmp_path):
    """The same loop protection the walk has: a link may point at its own ancestor."""
    root = _repo(tmp_path, {
        "app.py": 'CONFIG = "rules.toml"\n',
        "pkg/rules.toml": "[x]\ny = 1\n",
    })
    (root / "mirror").symlink_to(root, target_is_directory=True)
    assert _scan(root).read_by_code == ["pkg/rules.toml"]
