"""The bound on the coverage figure.

`codoc status` reports how many INDEXED chunks a feature covers, which is a claim
about the index and not about the repo. These tests pin the other half: what the
walk never saw, and — just as important — what it saw being deliberately left out
and therefore must stay quiet about. A survey that cried about `node_modules/` on
every status would be read the way every noisy warning is read, and the one line
that matters (a language codoc cannot parse) would go with it.
"""
from __future__ import annotations

import pathlib

from codoc.pipelines.indexing.cocoindex_app import _MAX_FILE_BYTES
from codoc.pipelines.indexing.survey import RepoSurvey, render_survey, survey_repo


def _repo(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _oversize(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# pad\n" * (_MAX_FILE_BYTES // 6 + 2), encoding="utf-8")
    assert path.stat().st_size > _MAX_FILE_BYTES
    return path


def test_a_repo_codoc_reads_whole_reports_nothing(tmp_path):
    survey = survey_repo(_repo(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/core.py": "def run():\n    return 1\n",
        "web/app.ts": "export const x = 1\n",
        "README.md": "# docs\n",
        "data/rows.json": "{}\n",
    }))
    assert survey.indexed == 3
    assert survey.unseen == 0
    # Silence is the point: an advisory that fires on a healthy repo is noise.
    assert render_survey(survey) == []


def test_source_in_a_language_no_adapter_reads_is_counted(tmp_path):
    survey = survey_repo(_repo(tmp_path, {
        "main.py": "x = 1\n",
        "cmd/serve.go": "package main\n",
        "cmd/util.go": "package main\n",
        "scripts/build.sh": "echo hi\n",
    }))
    assert survey.unreadable == {".go": 2, ".sh": 1}
    assert survey.unseen == 3
    line = render_survey(survey)[0]
    # Ranked by count so the biggest gap leads, and named by extension because the
    # answer differs per language, not per file.
    assert "2 .go, 1 .sh" in line


def test_data_and_docs_are_not_a_gap_in_the_view_of_the_code(tmp_path):
    survey = survey_repo(_repo(tmp_path, {
        "main.py": "x = 1\n",
        "notes.md": "hi\n",
        "schema.json": "{}\n",
        "pyproject.toml": "[project]\n",
        "uv.lock": "\n",
        "logo.svg": "<svg/>\n",
    }))
    assert survey.unreadable == {}
    assert render_survey(survey) == []


def test_a_file_over_the_index_cap_is_named(tmp_path):
    _repo(tmp_path, {"pkg/small.py": "x = 1\n"})
    _oversize(tmp_path / "pkg" / "generated.py")

    survey = survey_repo(tmp_path)
    assert survey.indexed == 1
    assert [rel for rel, _ in survey.oversize] == ["pkg/generated.py"]
    # Named, not just counted: the threshold is somebody's to revisit, and they
    # cannot weigh it without knowing which file it cost them.
    assert "pkg/generated.py" in render_survey(survey)[0]


def test_a_directory_excluded_on_purpose_stays_quiet(tmp_path):
    _repo(tmp_path, {
        "main.py": "x = 1\n",
        "node_modules/dep/index.js": "module.exports = 1\n",
        "node_modules/dep/typed.ts": "export const x = 1\n",
        "build/out.py": "x = 1\n",
        ".git/hooks/pre-commit.sh": "echo\n",
    })
    survey = survey_repo(tmp_path)
    assert survey.indexed == 1
    assert survey.unreadable == {}  # the .js under node_modules is nobody's gap
    assert render_survey(survey) == []


def test_a_gitignored_directory_stays_quiet_too(tmp_path):
    _repo(tmp_path, {
        ".gitignore": "generated/\n",
        "main.py": "x = 1\n",
        "generated/models.py": "x = 1\n",
        "generated/helper.go": "package main\n",
    })
    survey = survey_repo(tmp_path)
    assert survey.indexed == 1
    assert survey.unreadable == {}


def test_a_symlinked_directory_holding_source_is_reported(tmp_path):
    real = tmp_path / "packages" / "shared"
    real.mkdir(parents=True)
    (real / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    _repo(tmp_path, {"app/main.py": "x = 1\n"})
    (tmp_path / "app" / "shared").symlink_to(real, target_is_directory=True)

    survey = survey_repo(tmp_path)
    assert survey.symlinked_dirs == ["app/shared"]
    # The link is refused for loop protection; the real directory is still walked
    # where it lives, so its file is indexed once and not lost.
    assert survey.indexed == 2
    assert "app/shared" in render_survey(survey)[-1]


def test_a_symlinked_directory_with_no_source_is_not_reported(tmp_path):
    real = tmp_path / "assets"
    real.mkdir()
    (real / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
    _repo(tmp_path, {"main.py": "x = 1\n"})
    (tmp_path / "static").symlink_to(real, target_is_directory=True)

    assert survey_repo(tmp_path).symlinked_dirs == []


def test_a_symlink_under_an_excluded_path_stays_quiet(tmp_path):
    real = tmp_path / "packages" / "shared"
    real.mkdir(parents=True)
    (real / "lib.py").write_text("x = 1\n", encoding="utf-8")
    _repo(tmp_path, {"main.py": "x = 1\n"})
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "shared").symlink_to(real, target_is_directory=True)

    # Exclusion is tested before the symlink guard, so a link inside a directory
    # somebody excluded on purpose is not codoc declining to follow anything.
    assert survey_repo(tmp_path).symlinked_dirs == []


def test_each_kind_of_blindness_gets_its_own_line(tmp_path):
    _repo(tmp_path, {"main.py": "x = 1\n", "cmd/serve.go": "package main\n"})
    _oversize(tmp_path / "generated.py")
    real = tmp_path / "packages" / "shared"
    real.mkdir(parents=True)
    (real / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "shared").symlink_to(real, target_is_directory=True)

    lines = render_survey(survey_repo(tmp_path))
    assert len(lines) == 3  # answered differently, so never merged into one


def test_long_lists_are_summarised_rather_than_dumped():
    survey = RepoSurvey(symlinked_dirs=[f"pkg/{i}" for i in range(7)])
    line = render_survey(survey, max_names=3)[0]
    assert "pkg/0, pkg/1, pkg/2, +4 more" in line
    assert "pkg/5" not in line


def test_the_walk_stops_at_the_runaway_guard(tmp_path):
    _repo(tmp_path, {f"f{i}.py": "x = 1\n" for i in range(20)})
    survey = survey_repo(tmp_path, max_entries=5)
    # A guard, not a policy: it bounds the cost of an advisory, and the partial
    # figure it returns is still a lower bound on what codoc saw.
    assert survey.indexed <= 5
