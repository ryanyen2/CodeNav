"""The command.

    scribe convert fixtures/report.txt        write report.md beside it
    scribe convert fixtures/report.txt -      write to stdout
    scribe check fixtures/                    convert everything, report nothing

`check` exists so a change can be run over the whole corpus in one go. It writes
nothing, which is what makes it safe to run against a directory you care about.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .convert import convert


def _convert_one(path: Path, out: str | None) -> int:
    raw = path.read_text(encoding="utf-8", errors="replace")
    result = convert(raw)
    if out == "-":
        sys.stdout.write(result.markdown)
    else:
        target = path.with_suffix(".md")
        target.write_text(result.markdown, encoding="utf-8")
    print(f"{path.name}: {result.summary()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    command, rest = args[0], args[1:]
    if command == "convert":
        if not rest:
            print("convert needs a file", file=sys.stderr)
            return 2
        path = Path(rest[0])
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 2
        return _convert_one(path, rest[1] if len(rest) > 1 else None)

    if command == "check":
        root = Path(rest[0] if rest else "fixtures")
        found = sorted(root.glob("*.txt"))
        if not found:
            print(f"nothing to check in {root}", file=sys.stderr)
            return 2
        for path in found:
            result = convert(path.read_text(encoding="utf-8", errors="replace"))
            print(f"{path.name}: {result.summary()}")
        print(f"checked {len(found)} documents")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
