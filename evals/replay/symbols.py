"""Symbol-level ground truth, derived by NAME.

File-level rename detection (:mod:`evals.replay.gitfacts`) turned out to miss
the case that matters most. Screening five repositories over 1200 commits found
29 file renames but 2069 modifications: in real development files are rarely
renamed, while functions move between files that both continue to exist. Git
sees that as two modifications. codoc sees it as a relocation, which is exactly
the behaviour under test — so a file-level ground truth would score codoc's best
class as though it never occurred.

This module recovers those moves, and it does so through a signal codoc does not
use. The two mechanisms are complementary by construction:

* codoc detects a move by identical ``tokens_hash`` — a comment-stripped,
  whitespace-normalized token stream that **ignores where the symbol lives**.
* codoc detects a rename by ``types_hash`` uniqueness — an AST node-type
  sequence that **erases every identifier**.
* this module matches on the **qualified symbol name alone**, and never looks at
  a body.

So agreement between them is evidence, not tautology. Parsing goes through the
standard library's ``ast``, an implementation independent of the tree-sitter
grammars codoc indexes with.

Python only. The TypeScript corpus falls back to file-level truth, which is a
stated limit rather than an oversight.
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _show_many(repo: Path, refs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Contents of many ``(sha, path)`` blobs in one git invocation.

    One ``git show`` per file was costing more than the Loop A pass it was
    scoring: an altair commit touches dozens of files, each needing the blob at
    both the parent and the child, so a single commit could spawn a hundred
    subprocesses. ``cat-file --batch`` answers the whole list over one pipe.

    Missing paths are simply absent from the result — a file that did not exist
    on one side of the commit is the normal case, not an error.
    """
    if not refs:
        return {}
    query = "".join(f"{sha}:{path}\n" for sha, path in refs)
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=query.encode(), capture_output=True,
    )
    out, i, result = proc.stdout, 0, {}
    for sha, path in refs:
        nl = out.find(b"\n", i)
        if nl == -1:
            break
        header = out[i:nl].decode("utf-8", "replace")
        if header.endswith(("missing", "ambiguous")):
            i = nl + 1
            continue
        try:
            size = int(header.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            break
        body = out[nl + 1: nl + 1 + size]
        result[(sha, path)] = body.decode("utf-8", "replace")
        i = nl + 1 + size + 1   # blob, then the trailing newline git appends
    return result


# Parsing dominates once the blob fetch is batched, and the same content is
# parsed twice in a sequential replay: a file's child blob at commit N is its
# parent blob at commit N+1. Keyed by (sha, path), which is immutable.
_SYMBOL_CACHE: dict[tuple[str, str], set[str]] = {}
_CACHE_MAX = 20_000


MODULE_CHUNK = "__module__"


def qualified_symbols(source: str) -> set[str]:
    """Qualified names of everything codoc would chunk in ``source``.

    The GRANULARITY here matches codoc's chunker; the MATCHING SIGNAL does not,
    and that distinction is what keeps this ground truth independent. Scoring
    codoc on entities this module refuses to model would measure the gap between
    two chunkers rather than whether attribution survived — the first pass did
    exactly that and reported 13 phantom failures on a file rename, all of them
    module-level ``TypeVar`` assignments and the per-file ``__module__`` chunk
    that this walker did not emit. How a chunk is then tracked across commits
    stays wholly separate: codoc compares content and AST-shape hashes, this
    module compares names and never looks at a body.

    Three rules, each mirroring an observed chunker behaviour:

    * classes and functions, dotted through class nesting (``Class.method``)
    * module-level assignments, which carry constants and ``TypeVar``s
    * one ``__module__`` chunk per file, standing for the module itself

    Definitions nested inside a *function* are deliberately excluded — codoc
    does not chunk closures, so emitting them would invent symbols that vanish
    and reappear with no binding ever attached.

    A file that does not parse yields nothing rather than raising: mid-history
    commits do contain syntax errors, and one bad commit must not end a replay.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()

    found: set[str] = {MODULE_CHUNK}

    def walk(node: ast.AST, prefix: str, *, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if in_function:
                    continue
                name = f"{prefix}{child.name}"
                found.add(name)
                walk(child, name + ".", in_function=True)
            elif isinstance(child, ast.ClassDef):
                if in_function:
                    continue
                name = f"{prefix}{child.name}"
                found.add(name)
                walk(child, name + ".", in_function=False)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)) and not prefix:
                # Module level only. A class attribute is part of its class's
                # chunk, not a chunk of its own.
                targets = (
                    child.targets if isinstance(child, ast.Assign) else [child.target]
                )
                for t in targets:
                    if isinstance(t, ast.Name):
                        found.add(t.id)
            elif isinstance(child, (ast.If, ast.Try, ast.With)):
                # Definitions guarded by `if TYPE_CHECKING:` or a try/except
                # import shim are still definitions and still move. These do not
                # open a namespace, so the prefix carries through unchanged.
                walk(child, prefix, in_function=in_function)

    walk(tree, "", in_function=False)
    return found


@dataclass
class SymbolFacts:
    """Symbol-level movement across one commit."""

    moved: dict[str, tuple[str, str]] = field(default_factory=dict)
    # qualified name → (old file, new file)
    vanished: dict[str, str] = field(default_factory=dict)   # name → file it left
    appeared: dict[str, str] = field(default_factory=dict)   # name → file it entered

    @property
    def move_count(self) -> int:
        return len(self.moved)


def symbol_facts(
    repo: Path, parent: str, sha: str, paths: set[str],
    renamed: dict[str, str] | None = None,
) -> SymbolFacts:
    """Detect symbols that moved between files across one commit.

    ``paths`` is every indexed path the commit touched, on both sides of any
    rename. A symbol is a move when it leaves exactly one file and enters
    exactly one other in the same commit; ambiguous cases (the same name leaving
    two files, or entering two) are left out rather than guessed, because a
    wrong ground truth is worse than a missing one.

    ``renamed`` resolves the two ways that rule breaks on real refactors:

    * **A rename that also redistributes.** flask's ``sansio`` split renamed
      ``scaffold.py`` and moved part of its contents to other modules in the
      same commit. Assuming every symbol landed at the renamed path is simply
      false, and it produced 11 phantom failures.
    * **Several files renamed at once.** Three ``tests/typing/*`` files moved
      together, each defining ``app``; that name then left three files and
      entered three, so the ambiguity guard discarded all of them. The same
      applies to ``__module__``, which every file defines.

    Both are fixed by settling renamed pairs first: a symbol present in the old
    path before and the new path after moved between exactly those two, whatever
    else shares its name. Only the leftovers go through ambiguity matching.
    """
    renamed = renamed or {}
    py = sorted(p for p in paths if p.endswith(".py"))
    wanted = [(c, p) for p in py for c in (parent, sha)
              if (c, p) not in _SYMBOL_CACHE]
    for key, src in _show_many(repo, wanted).items():
        if len(_SYMBOL_CACHE) > _CACHE_MAX:
            _SYMBOL_CACHE.clear()
        _SYMBOL_CACHE[key] = qualified_symbols(src)

    before: dict[str, set[str]] = {}
    after: dict[str, set[str]] = {}
    for p in py:
        if (parent, p) in _SYMBOL_CACHE:
            before[p] = _SYMBOL_CACHE[(parent, p)]
        if (sha, p) in _SYMBOL_CACHE:
            after[p] = _SYMBOL_CACHE[(sha, p)]

    facts = SymbolFacts()

    # Renamed pairs settle first and take their symbols out of the pools, so a
    # name shared across several renamed files cannot make itself ambiguous.
    claimed_from: dict[str, set[str]] = {}
    claimed_to: dict[str, set[str]] = {}
    for old, new in renamed.items():
        for name in before.get(old, set()) & after.get(new, set()):
            facts.moved[name] = (old, new)
            claimed_from.setdefault(name, set()).add(old)
            claimed_to.setdefault(name, set()).add(new)

    left: dict[str, list[str]] = {}
    entered: dict[str, list[str]] = {}
    for p, names in before.items():
        for n in names - after.get(p, set()):
            if p in claimed_from.get(n, ()):
                continue
            left.setdefault(n, []).append(p)
    for p, names in after.items():
        for n in names - before.get(p, set()):
            if p in claimed_to.get(n, ()):
                continue
            entered.setdefault(n, []).append(p)

    for name, from_files in left.items():
        to_files = entered.get(name, [])
        if len(from_files) == 1 and len(to_files) == 1:
            facts.moved[name] = (from_files[0], to_files[0])
        elif not to_files:
            facts.vanished[name] = from_files[0]
    for name, to_files in entered.items():
        if name not in facts.moved and name not in left:
            facts.appeared[name] = to_files[0]
    return facts


def symbol_of(symbol_path: str) -> str:
    """The qualified name inside a codoc ``symbol_path``.

    codoc addresses a chunk as ``path/to/file.py::Class.method``. Ground truth
    is keyed on the part after the separator so the two can be compared while a
    move changes the part before it.
    """
    return symbol_path.split("::", 1)[1] if "::" in symbol_path else symbol_path
