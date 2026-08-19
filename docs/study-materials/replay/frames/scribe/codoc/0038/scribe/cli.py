"""The command.

    scribe convert fixtures/report.txt        write report.md beside it
    scribe convert fixtures/report.txt -      write to stdout
    scribe check fixtures/                    convert everything, write nothing

    --config FILE   use this config instead of the scribe.toml beside the document
    --no-report     write only the Markdown, not the conversion report

`check` exists so a change can be run over the whole corpus in one go. It writes
nothing, which is what makes it safe to run against a directory you care about.

`convert` writes two files: the Markdown, and a short report beside it saying
what the conversion did. The report is named after the source rather than being a
fixed `report.md`, because a fixed name would collide with the Markdown of any
document actually called `report.txt` — which the fixtures include.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import report
from .convert import convert
from .settings import Config, ConfigError, find, load


def _report_path(source: Path) -> Path:
    """Where the conversion report goes: `report.txt` -> `report.report.md`."""
    return source.with_suffix(".report.md")


def _take_flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


def _take_option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    at = args.index(name)
    if at + 1 >= len(args):
        raise ConfigError(f"{name} needs a file")
    value = args[at + 1]
    del args[at : at + 2]
    return value


def _config_for(path: Path, given: str | None) -> Config:
    if given is None:
        return find(path)
    chosen = Path(given)
    if not chosen.is_file():
        raise ConfigError(f"no such config file: {chosen}")
    return load(chosen)


def _convert_one(path: Path, out: str | None, config: Config, write_report: bool) -> int:
    raw = path.read_text(encoding="utf-8", errors="replace")
    result = convert(raw, config.for_document(path.name))

    if out == "-":
        # Asked for stdout, so nothing is written to disk at all: a report file
        # would be a surprise from a command whose whole point is not to leave one.
        sys.stdout.write(result.markdown)
        print(f"{path.name}: {result.summary()}", file=sys.stderr)
        return 0

    target = path.with_suffix(".md")
    target.write_text(result.markdown, encoding="utf-8")
    written = [target.name]
    if write_report:
        note = _report_path(path)
        note.write_text(report.render(result, path, target, config), encoding="utf-8")
        written.append(note.name)
    print(f"{path.name}: {result.summary()} -> {', '.join(written)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    try:
        write_report = not _take_flag(args, "--no-report")
        given_config = _take_option(args, "--config")

        if not args:
            print("nothing to do", file=sys.stderr)
            return 2

        command, rest = args[0], args[1:]
        if command == "convert":
            if not rest:
                print("convert needs a file", file=sys.stderr)
                return 2
            path = Path(rest[0])
            if not path.is_file():
                print(f"no such file: {path}", file=sys.stderr)
                return 2
            config = _config_for(path, given_config)
            return _convert_one(
                path, rest[1] if len(rest) > 1 else None, config, write_report
            )

        if command == "check":
            root = Path(rest[0] if rest else "fixtures")
            found = sorted(root.glob("*.txt"))
            if not found:
                print(f"nothing to check in {root}", file=sys.stderr)
                return 2
            # Looked up once per document rather than once for the run, so a
            # document's settings always come from beside the document. With the
            # glob as it is that resolves to the same file every time, but it is
            # the document that decides, not the run, and nothing here has to
            # change the day `check` learns to walk into subdirectories.
            seen: list[str] = []
            for path in found:
                config = _config_for(path, given_config)
                result = convert(
                    path.read_text(encoding="utf-8", errors="replace"),
                    config.for_document(path.name),
                )
                print(f"{path.name}: {result.summary()}")
                where = str(config.path) if config.path else "defaults"
                if where not in seen:
                    seen.append(where)
            print(f"checked {len(found)} documents against {', '.join(seen)}")
            return 0
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
