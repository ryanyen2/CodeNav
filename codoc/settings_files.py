"""A settings file as addressable chunks, so a decision that moved into one can be named.

(The repo's OWN settings are `codoc/config.py` and `.codoc/config.json`; this module is
about the settings files of the codebase being described.)

A codebase moves a constant out of a module and into `rules.toml` precisely BECAUSE the
value matters to somebody who is not reading the source — which is the same reason a
feature tree exists. So the moment a decision becomes worth configuring is the moment
codoc goes quiet about it: the indexer walked `**/*.py` and `**/*.ts`, so the pass that
described the feature had the code that READS the setting and never the file that SETS
it. It described the mechanism, because the mechanism was all it was shown:

    The month threshold is read from rules.toml.

A settings file has no functions, so this is not a tree-sitter adapter and does not live
in `codoc/lang/` (which is programming languages). What it shares with an adapter is the
only thing the pipeline needs — a file becomes NAMED, ADDRESSABLE pieces:

- **A chunk is a section, and a nested section is its member.** `[periods]` is
  `rules.toml::periods` and `[periods.week]` is `rules.toml::periods.week`, which is the
  same owner/member relation a class and its methods have. Everything downstream that
  reads a dotted symbol path — the prompt's binding rule, the per-pass budget's split by
  top-level owner — therefore works on settings unchanged.
- **Keys before the first section are the file's own**, under `::__module__`, the name
  the code walk already uses for a module's top-level statements.
- **The comments come with the section.** In code the reasoning is in the docstring; in
  a settings file it is in the `#` lines above the key, and it is the part a description
  most wants to quote. A run of comment lines directly above a section belongs to it.

**Identity is the parsed key/value pairs, not the text** (`hashes`). Fingerprinting is
what decides whether Loop A wakes at all, so a formatter that reorders two keys or
reflows a comment must not read as a policy change, while `month = "made"` becoming
`month = "posted"` must. Sorting the flattened pairs gives exactly that. The two signals
keep the meaning they have for code: `tokens_hash` covers keys AND values, so a changed
value is a change; `types_hash` covers the key paths alone, so it is the file's shape —
a renamed section moves both, a re-tuned value only the first.

**A fragment that does not parse falls back to its words**, the way `core/tree_walk`
falls back when a parse fails. A settings file is small and hand-edited, so a
half-written one is a state a person passes through, and the answer to it is the same as
for a half-written function: hash what is there and let the next save correct it.

Four formats, because they are what a Python repo's decisions actually live in. TOML,
JSON and INI are read by the standard library. YAML needs PyYAML, which codoc does not
depend on, so a repo without it has no YAML support rather than a broken import — and
`available_formats` reports that, since a file the walk skipped and a file that does not
exist must not look the same (see `pipelines/indexing/survey.py`).
"""
from __future__ import annotations

import configparser
import hashlib
import json
import pathlib
import re
import tomllib

from codoc.lang.base import Chunk

try:  # PyYAML is not a codoc dependency — see the module docstring.
    import yaml as _yaml
except ImportError:  # pragma: no cover — exercised by available_formats()
    _yaml = None

#: file extension → the format name used everywhere else in this module
FORMATS: dict[str, str] = {
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".ini": "ini",
    ".cfg": "ini",
}

#: the format names themselves — what an indexed row carries in its `language`
#: column, so a reader with a row and no filename can still tell a settings chunk
#: from a code one. Static rather than :func:`available_formats`, which reports what
#: THIS process can parse: a YAML row indexed on a machine with PyYAML is still a
#: settings row here.
FORMAT_NAMES: frozenset[str] = frozenset(FORMATS.values())

# Files with a settings extension that are not a decision anybody authored: package
# metadata, lock files, and tool manifests. They are machinery — generated or dictated
# by a tool — and a tree that described them would spend its first nodes on the build.
# This is a backstop and not the selection rule: which settings files enter the index is
# decided by the code that READS them, not by their extension.
NOT_INTENT: frozenset[str] = frozenset({
    "pyproject.toml", "setup.cfg", "poetry.lock", "uv.lock", "pipfile.lock",
    "cargo.toml", "cargo.lock", "package.json", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "composer.json", "composer.lock",
    "tsconfig.json", "jsconfig.json", ".eslintrc.json", ".prettierrc.json",
})

#: the name a file's own top-level keys take, matching the code walk's module chunk
MODULE_CHUNK = "__module__"

_TOML_SECTION = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")
_INI_SECTION = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*$")
_YAML_KEY = re.compile(r"^([A-Za-z_][\w.\-]*)\s*:")
_COMMENT = re.compile(r"^\s*[#;]")


def available_formats() -> set[str]:
    """The formats this process can actually read.

    YAML drops out when PyYAML is absent. Callers report the difference rather than
    treating an unreadable file as an absent one.
    """
    return {f for f in FORMATS.values() if f != "yaml" or _yaml is not None}


def detect_format(file: str) -> str | None:
    """The settings format of *file* by extension, or None if it is not one."""
    fmt = FORMATS.get(pathlib.PurePosixPath(file).suffix.lower())
    return fmt if fmt in available_formats() else None


def is_settings_file(file: str) -> bool:
    """True for a settings file that could hold an authored decision.

    Extension known, parser present, and not one of the manifests in `NOT_INTENT`.
    Says nothing about whether this repo's code reads it — that is the selection rule,
    and it lives with the walk.
    """
    name = pathlib.PurePosixPath(file).name.lower()
    return detect_format(file) is not None and name not in NOT_INTENT


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

def extract_chunks(file: str, source: str) -> list[Chunk]:
    """*source* as named sections, in the order they appear in the file.

    One chunk per section, its comments included, plus a `::__module__` chunk for the
    keys that precede the first section. A file with no sections at all is that one
    chunk — the honest answer for a flat settings file, and still addressable,
    quotable and bindable.
    """
    fmt = detect_format(file)
    if fmt is None or not source.strip():
        return []
    lines = source.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    starts = _uniquify(_section_starts(source, fmt))

    def chunk(name: str, start_line: int, end_line: int) -> Chunk | None:
        start, end = offsets[start_line], offsets[end_line]
        if not source[start:end].strip():
            return None
        return Chunk(symbol_path=f"{file}::{name}", file=file,
                     start_byte=start, end_byte=end, source=source[start:end])

    if not starts:
        one = chunk(MODULE_CHUNK, 0, len(lines))
        return [one] if one else []

    # A section begins at the comment run directly above its header, not at the
    # header: the reasoning for a setting is written above it, and a section quoted
    # without that sentence is quoted without its explanation. A blank line ends the
    # run — that is where a person stopped writing about this section — and the
    # previous header floors it, so no run is claimed twice.
    firsts: list[int] = []
    for index, (line_no, _name) in enumerate(starts):
        floor = starts[index - 1][0] + 1 if index else 0
        first = line_no
        while first > floor and _COMMENT.match(lines[first - 1]):
            first -= 1
        firsts.append(first)

    # Two sections on one line is minified JSON, and a line is the unit here — so the
    # file is not addressable BY SECTION and the honest answer is the file itself.
    # Splitting it would hand a reader a chunk named after one key holding all of them.
    if len(set(firsts)) != len(firsts):
        one = chunk(MODULE_CHUNK, 0, len(lines))
        return [one] if one else []

    out: list[Chunk] = []
    # JSON's own opening brace is not a decision anybody made, and every top-level key
    # in it is already a section, so only the other formats can have keys of their own
    # ahead of the first section.
    if fmt != "json" and _has_content(lines[:firsts[0]]):
        out.append(chunk(MODULE_CHUNK, 0, firsts[0]))
    # Where the LAST section ends. JSON's closing brace closes the document and not
    # the member above it, so a chunk that swallowed it would not parse as the member
    # it is — and every other format ends its last section at the end of the file.
    last = _json_close(lines) if fmt == "json" else len(lines)
    for index, (_line_no, name) in enumerate(starts):
        end_line = firsts[index + 1] if index + 1 < len(starts) else last
        out.append(chunk(name, firsts[index], end_line))
    return [c for c in out if c is not None]


def resolve_symbol_path(source: str, symbol_path: str) -> tuple[int, int] | None:
    """Byte range of the section *symbol_path* names, or None.

    This is what keeps a binding alive across an edit: the section moved down the file,
    the feature still points at it.
    """
    file, _, name = symbol_path.rpartition("::")
    for chunk in extract_chunks(file, source):
        if chunk.symbol_path == symbol_path or (not file and name == chunk.symbol_path):
            return chunk.start_byte, chunk.end_byte
    return None


def _section_starts(source: str, fmt: str) -> list[tuple[int, str]]:
    """(line index, section name) for every section header, in file order."""
    lines = source.splitlines()
    if fmt == "toml":
        return [(i, _dotted(m.group(1))) for i, line in enumerate(lines)
                if (m := _TOML_SECTION.match(line))]
    if fmt == "ini":
        return [(i, m.group(1)) for i, line in enumerate(lines)
                if (m := _INI_SECTION.match(line))]
    if fmt == "yaml":
        return [(i, m.group(1)) for i, line in enumerate(lines)
                if (m := _YAML_KEY.match(line))]
    if fmt == "json":
        return _json_key_lines(source)
    return []


def _uniquify(starts: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """A repeated section name made addressable, in file order.

    An array of tables repeats its header by design (`[[servers]]` twice is two
    servers), and two chunks may not share a symbol path — `(file, symbol_path)` is
    the index's primary key and the binding's unique constraint, so a repeat would
    cost the whole file. The first entry keeps the plain name, so appending one
    renames nothing that was already bound.
    """
    seen: dict[str, int] = {}
    out: list[tuple[int, str]] = []
    for line_no, name in starts:
        count = seen.get(name, 0)
        seen[name] = count + 1
        out.append((line_no, name if not count else f"{name}[{count}]"))
    return out


def _json_key_lines(source: str) -> list[tuple[int, str]]:
    """Top-level keys of a JSON object, by the line each one opens on.

    A brace/bracket depth count rather than a parse, because a parse loses positions
    and the position is what makes the chunk addressable. Strings are skipped so a
    brace inside a value cannot move the depth.
    """
    out: list[tuple[int, str]] = []
    depth = 0
    line = 0
    i = 0
    while i < len(source):
        ch = source[i]
        if ch == "\n":
            line += 1
        elif ch == '"':
            start = i
            i += 1
            while i < len(source) and source[i] != '"':
                i += 2 if source[i] == "\\" else 1
            if depth == 1:
                rest = source[i + 1:]
                if rest.lstrip(" \t").startswith(":"):
                    out.append((line, source[start + 1:i]))
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        i += 1
    return out


def _json_close(lines: list[str]) -> int:
    """The line the JSON document closes on — structural, and nobody's decision."""
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip().startswith(("}", "]")):
            return index
    return len(lines)


def _dotted(name: str) -> str:
    """A TOML table name as a symbol path segment: quotes off, dots kept."""
    return ".".join(part.strip().strip("\"'") for part in name.split("."))


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _has_content(lines: list[str]) -> bool:
    return any(line.strip() and not _COMMENT.match(line) for line in lines)


# ---------------------------------------------------------------------------
# Identity + parse judgement
# ---------------------------------------------------------------------------

def hashes(source: str, fmt: str) -> tuple[str, str]:
    """(tokens_hash, types_hash) for a settings chunk or file.

    `tokens_hash` is the sorted flattened key/value pairs, so a reordered or
    reformatted file is unchanged and a re-tuned value is not. `types_hash` is the key
    paths alone — the file's shape, which a renamed or added section moves and a value
    change does not. Both are hex SHA-256, like the code walk's, because they are
    stored in the same columns and compared the same way.
    """
    pairs = _flatten(source, fmt)
    if pairs is None:
        # Half-written, mid-edit: hash the words, the way the code walk falls back
        # when a parse fails. The next save corrects it.
        tokens = " ".join(source.split())
        return (_sha(tokens), _sha(""))
    tokens = "\n".join(f"{k}={v}" for k, v in sorted(pairs))
    types = "\n".join(sorted(k for k, _v in pairs))
    return _sha(tokens), _sha(types)


def parses_cleanly(source: str, fmt: str) -> bool:
    """True if *source* is a complete, readable document of that format.

    A settings chunk parses on its own: a TOML table, a YAML block, an INI section.
    A JSON chunk is one member of an object, so it is judged inside braces.
    """
    return _load(source, fmt) is not None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(source: str, fmt: str):
    """The parsed document, or None if it does not parse. `{}` is a document."""
    try:
        if fmt == "toml":
            return tomllib.loads(source)
        if fmt == "json":
            text = source.strip()
            if not text.startswith("{") and not text.startswith("["):
                text = "{" + text.rstrip().rstrip(",") + "}"
            return json.loads(text)
        if fmt == "yaml":
            if _yaml is None:
                return None
            got = _yaml.safe_load(source)
            return got if got is not None else {}
        if fmt == "ini":
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(source)
            return {s: dict(parser[s]) for s in parser.sections()}
    except Exception:
        return None
    return None


def _flatten(source: str, fmt: str) -> list[tuple[str, str]] | None:
    """Every leaf of the document as (dotted key path, value), or None if unparsed."""
    doc = _load(source, fmt)
    if doc is None:
        return None
    pairs: list[tuple[str, str]] = []

    def walk(node, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{prefix}[{index}]")
        else:
            pairs.append((prefix, repr(node)))

    walk(doc, "")
    return pairs
