"""The command.

    scribe convert fixtures/report.txt        write report.md beside it
    scribe convert fixtures/report.txt -      write to stdout
    scribe check fixtures/                    convert everything, report nothing

    --config PATH   use this scribe.toml instead of looking for one
    --no-report     do not write the note saying what the conversion did

`check` exists so a change can be run over the whole corpus in one go. It writes
nothing, which is what makes it safe to run against a directory you care about.

Writing to a file also writes `<name>.report.md` beside it: a short note on what
the conversion did to that document. Writing to stdout does not, because the
point of stdout is to pipe the Markdown somewhere.

Settings come from the nearest `scribe.toml` at or above the document, and are
looked up per document, so one corpus can hold documents that need different
rules. `check` reports which config it found, since a run that silently used the
wrong one would be hard to notice.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config, report
from .convert import convert


def _load_config(explicit: str | None, near: Path) -> config.Config:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise config.ConfigError(f"no such config file: {path}")
        return config.load(path)
    return config.discover(near)


def _convert_one(path: Path, out: str | None, conf: config.Config, write_report: bool) -> int:
    raw = path.read_text(encoding="utf-8", errors="replace")
    result = convert(raw, conf.for_document(path.name))
    if out == "-":
        sys.stdout.write(result.markdown)
        return 0

    target = path.with_suffix(".md")
    target.write_text(result.markdown, encoding="utf-8")
    print(f"{path.name}: {result.summary()}")
    if write_report and result.settings.report.write:
        note = report.name_for(target)
        note.write_text(report.render(result, path, target), encoding="utf-8")
        print(f"{path.name}: wrote {note.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    # Flags are pulled out wherever they appear, so the positional arguments
    # below can stay where they were.
    write_report = True
    if "--no-report" in args:
        args.remove("--no-report")
        write_report = False

    conf_path: str | None = None
    if "--config" in args:
        at = args.index("--config")
        if at + 1 >= len(args):
            print("--config needs a path", file=sys.stderr)
            return 2
        conf_path = args[at + 1]
        del args[at : at + 2]

    if not args:
        print("nothing to do", file=sys.stderr)
        return 2

    command, rest = args[0], args[1:]
    try:
        if command == "convert":
            if not rest:
                print("convert needs a file", file=sys.stderr)
                return 2
            path = Path(rest[0])
            if not path.is_file():
                print(f"no such file: {path}", file=sys.stderr)
                return 2
            conf = _load_config(conf_path, path)
            return _convert_one(path, rest[1] if len(rest) > 1 else None, conf, write_report)

        if command == "check":
            root = Path(rest[0] if rest else "fixtures")
            found = sorted(root.glob("*.txt"))
            if not found:
                print(f"nothing to check in {root}", file=sys.stderr)
                return 2
            conf = _load_config(conf_path, root)
            for path in found:
                result = convert(
                    path.read_text(encoding="utf-8", errors="replace"),
                    conf.for_document(path.name),
                )
                print(f"{path.name}: {result.summary()}")
            where = conf.path if conf.path else "no scribe.toml found, using defaults"
            print(f"checked {len(found)} documents ({where})")
            return 0
    except config.ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
