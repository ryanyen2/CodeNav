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

from codoc.pipelines.indexing.gate import HEARING_BYTES, READ_CEILING_BYTES
from codoc.pipelines.indexing.survey import RepoSurvey, render_survey, survey_repo


def _repo(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _write(path: pathlib.Path, text: str, floor: int) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert path.stat().st_size > floor
    return path


def _beyond_ceiling(path: pathlib.Path) -> pathlib.Path:
    """A file past what codoc will read into memory at all."""
    return _write(path, "# pad\n" * (READ_CEILING_BYTES // 6 + 2), READ_CEILING_BYTES)


def _blob(path: pathlib.Path) -> pathlib.Path:
    """Large, parseable, and one enormous data literal — nothing to bind to."""
    body = "1, " * (HEARING_BYTES // 3 + 100)
    return _write(path, f"DATA = [{body}]\n", HEARING_BYTES)


def _generated_surface(path: pathlib.Path) -> pathlib.Path:
    """Large and made of ordinary definitions — altair's schema modules.

    The size a real one is (`test/altair`'s `core.py` is 1.6 MB of 923 classes),
    built here from the same shape so the hearing is asked the real question.
    """
    unit = (
        "class Gen{i}(SchemaBase):\n"
        '    """A generated schema class."""\n'
        "    def __init__(self, field=None, **kwds):\n"
        "        super().__init__(field=field, **kwds)\n\n"
    )
    text = "".join(unit.format(i=i) for i in range(HEARING_BYTES // 150 + 2_000))
    return _write(path, text, HEARING_BYTES)


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


def test_a_file_too_large_to_read_is_named(tmp_path):
    _repo(tmp_path, {"pkg/small.py": "x = 1\n"})
    _beyond_ceiling(tmp_path / "pkg" / "huge.py")

    survey = survey_repo(tmp_path)
    assert survey.indexed == 1
    assert [rel for rel, _ in survey.too_large] == ["pkg/huge.py"]
    # Named, not just counted: the threshold is somebody's to revisit, and they
    # cannot weigh it without knowing which file it cost them.
    assert "pkg/huge.py" in render_survey(survey)[0]


def test_a_large_file_made_of_ordinary_definitions_is_indexed(tmp_path):
    # The case a byte cap got wrong. `test/altair` ships two modules from one
    # generator, 1.20 MB and 1.60 MB; the cap indexed the first and made the second
    # invisible, and the second is the schema surface a reader most needs described.
    _repo(tmp_path, {"pkg/small.py": "x = 1\n"})
    _generated_surface(tmp_path / "pkg" / "schema.py")

    survey = survey_repo(tmp_path)
    assert survey.indexed == 2
    assert survey.unseen == 0
    assert render_survey(survey) == []


def test_a_large_file_that_is_one_data_literal_is_reported(tmp_path):
    # …and the shape the cap was written for is still turned away, on the ground
    # the cap was reaching for: the parse found nothing a feature could bind to.
    _repo(tmp_path, {"pkg/small.py": "x = 1\n"})
    _blob(tmp_path / "pkg" / "table.py")

    survey = survey_repo(tmp_path)
    assert survey.indexed == 1
    assert [rel for rel, _ in survey.unaddressable] == ["pkg/table.py"]
    assert "pkg/table.py" in render_survey(survey)[0]


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
    _beyond_ceiling(tmp_path / "huge.py")
    _blob(tmp_path / "table.py")
    real = tmp_path / "packages" / "shared"
    real.mkdir(parents=True)
    (real / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "shared").symlink_to(real, target_is_directory=True)

    lines = render_survey(survey_repo(tmp_path))
    assert len(lines) == 4  # answered differently, so never merged into one


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
