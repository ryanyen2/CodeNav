"""Does the walk give an address to every entity the interpreter can see?

The unit tests next door pin the rules one shape at a time, against source written to
exercise them. This one asks the same question of real code, with `ast` as the oracle —
the parser that decides what a Python module actually declares — over every corpus in
`test/` plus codoc's own source and test suite. That is close to 400 files and roughly a
second, and it is what found the gap the guarded-declaration rule closes: a public name
assigned inside a module-level `try` or `if` had no address, so nothing could cite
`requests.compat.is_urllib3_1` or `requests.sessions.preferred_clock` at all.

A conformance test rather than a golden file, because the interesting failure is in
BOTH directions. An address the oracle expects and the walk does not hand out is an
entity no description can point at. An address the walk hands out and the oracle does
not know is a name that does not exist, which a citation would resolve to nothing.

`GLUE` records what is deliberately left unaddressed, by name, with the reason. It is a
short list on purpose: an entry there is a decision, and the test fails if one of them
starts getting an address, because that is a change in what the tree can cite.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from codoc.lang import get_adapter

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOTS = ("test", "codoc", "tests")
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git"}

_TRANSPARENT: tuple[type[ast.AST], ...] = (
    ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While,
    ast.Match, ast.match_case, ast.ExceptHandler,
)
if hasattr(ast, "TryStar"):  # 3.11+
    _TRANSPARENT = _TRANSPARENT + (ast.TryStar,)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Names the walk is expected NOT to address, and why. Each is a module-level guard
# declaring several public names at once, where any single branch is a fragment: see
# `test_python_adapter_keeps_a_guard_declaring_several_names_as_glue`.
GLUE = {
    # requests' three-way optional-dependency probe. The only branch that assigns
    # these says `= None`.
    "test/requests/help.py": {"OpenSSL", "cryptography", "pyopenssl"},
    # A loop body that aliases submodules into `requests.packages`; these are its
    # iteration temps.
    "test/requests/packages.py": {"imported_mod", "mod", "target"},
    # `if __name__ == "__main__"` blocks. An importer never binds these at all.
    "test/nanochat/dataset.py": {
        "args", "ids_to_download", "num", "parser", "results", "successful",
    },
    "test/nanochat/engine.py": {
        "autocast_ctx", "bos_token_id", "chunk", "device_type", "engine",
        "generated_tokens", "kwargs", "prompt_tokens", "reference_ids", "stream",
        "t0", "t1", "token",
    },
    "test/nanochat/report.py": {"args", "parser"},
}


def _declared_name(node: ast.AST) -> str | None:
    """The single public module name *node* declares by assignment, if any."""
    name = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        name = node.target.id
    elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias) \
            and isinstance(node.name, ast.Name):
        name = node.name.id
    return name if name and not name.startswith("_") else None


def _branches(node: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        inner = getattr(node, field, None)
        if isinstance(inner, list):
            out.extend(n for n in inner if isinstance(n, ast.AST))
    return out


def _same_scope(body: list[ast.AST], module_level: bool) -> tuple[set[str], set[str]]:
    """`(definition names, assigned names)` *body* contributes to its own scope."""
    defs: set[str] = set()
    assigned: set[str] = set()
    for node in body:
        if isinstance(node, _DEFS):
            defs.add(node.name)
        elif isinstance(node, _TRANSPARENT):
            inner_defs, inner_assigned = _same_scope(_branches(node), module_level)
            defs |= inner_defs
            assigned |= inner_assigned
        elif module_level:
            name = _declared_name(node)
            if name:
                assigned.add(name)
    return defs, assigned


def expected(tree: ast.Module) -> set[str]:
    """Every address the walk should hand out for *tree*, by the documented rules."""
    out: set[str] = set()

    def scope(body: list[ast.AST], prefix: str, module_level: bool) -> None:
        for node in body:
            if isinstance(node, _DEFS):
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                out.add(qualified)
                if isinstance(node, ast.ClassDef):
                    scope(node.body, qualified, False)
                continue
            if isinstance(node, _TRANSPARENT):
                branches = _branches(node)
                defs, assigned = _same_scope(branches, module_level)
                if defs:
                    # Definitions inside a guard belong to this scope. Whether the
                    # guard itself becomes the chunk (one name) or each definition
                    # keeps its own (several) changes the SOURCE, not the addresses.
                    scope(branches, prefix, module_level)
                elif module_level and len(assigned) == 1:
                    out.add(next(iter(assigned)))
                continue
            if module_level:
                name = _declared_name(node)
                if name:
                    out.add(name)

    scope(tree.body, "", True)
    return out


def _python_files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root in ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if not SKIP_DIRS.isdisjoint(path.parts):
                continue
            found.append(path)
    return found


def test_the_walk_addresses_what_the_interpreter_declares() -> None:
    files = _python_files()
    # A guard on the guard: if the corpora go missing this test would pass by
    # measuring nothing, which is the one way a conformance check lies.
    assert len(files) > 200, f"expected the corpora under {ROOTS}, found {len(files)}"

    adapter = get_adapter("python")
    missing: list[str] = []
    unknown: list[str] = []
    read = 0
    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # not this test's subject; see tests/loop/test_unparseable.py
        read += 1
        rel = path.relative_to(REPO).as_posix()
        want = expected(tree) - GLUE.get(rel, set())
        got = {
            chunk.symbol_path.split("::", 1)[1]
            for chunk in adapter.extract_chunks(str(path), source)
        }
        got.discard("__module__")
        missing += [f"{rel}::{name}" for name in sorted(want - got)]
        unknown += [f"{rel}::{name}" for name in sorted(got - want)]

    assert read > 200
    assert not missing, (
        f"{len(missing)} entities with no address, so nothing can cite them: "
        + ", ".join(missing[:12])
    )
    assert not unknown, (
        f"{len(unknown)} addresses for names the interpreter does not declare: "
        + ", ".join(unknown[:12])
    )


@pytest.mark.parametrize("rel", sorted(GLUE))
def test_every_deliberate_omission_is_still_one(rel: str) -> None:
    """A name in GLUE that starts getting an address is a decision to re-take."""
    path = REPO / rel
    if not path.is_file():
        pytest.skip(f"{rel} is not in this checkout")
    source = path.read_text(encoding="utf-8", errors="replace")
    got = {
        chunk.symbol_path.split("::", 1)[1]
        for chunk in get_adapter("python").extract_chunks(str(path), source)
    }
    now_addressed = GLUE[rel] & got
    assert not now_addressed, (
        f"{sorted(now_addressed)} are addressed now; if that is intended, remove them "
        f"from GLUE — the tree can cite them, and this file says it cannot"
    )
