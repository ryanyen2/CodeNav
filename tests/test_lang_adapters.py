"""Tests for Python and TypeScript language adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.core.tree_walk import walk
from codoc.lang import detect_language, get_adapter, parses_cleanly


def fingerprint_chunk(source: str, adapter) -> str:
    """Whitespace-/comment-stable token fingerprint (tree_walk tokens_hash)."""
    return walk(source, adapter).tokens_hash


def test_detect_language_python() -> None:
    assert detect_language("/path/to/file.py") == "python"


def test_detect_language_typescript() -> None:
    assert detect_language("/path/to/file.ts") == "typescript"
    assert detect_language("/path/to/file.tsx") == "typescript"


def test_detect_language_unsupported_returns_none() -> None:
    assert detect_language("README.md") is None


def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_adapter("klingon")


def test_python_adapter_extracts_chunks(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    assert "tests/fixtures/sample_cli.py::create_parser" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum.from_string" in paths


def test_python_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_python_adapter_fingerprint_stable_under_whitespace(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    target = next(c for c in chunks if c.symbol_path.endswith("::create_parser"))
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = "\n\n# leading comment\n" + target.source.replace("\n", "\n  \n", 1) + "\n"
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


# ── What real Python puts where ───────────────────────────────────────────────
# The walk used to look only at a scope's direct children, and to key a chunk by
# qualified name alone. Both assumptions hold for a tidy sample file and fail on
# ordinary code: a fallback definition in an `except` branch had no address at
# all, and an overloaded function was indexed as its first empty stub. These pin
# the shapes that broke it — `test/altair` had 517 definitions landing on an
# already-occupied address before this, and has none now.

CONDITIONAL = '''"""Doc."""
import sys
from typing import TYPE_CHECKING, overload

PUBLIC = 1

if TYPE_CHECKING:
    class Shape:
        def area(self) -> float: ...

try:
    from fast import loads
except ImportError:
    def loads(text):
        return eval(text)

if sys.version_info >= (3, 12):
    def batched(items, n):
        return items
else:
    def batched(items, n):
        return [items]

@overload
def render(x: int) -> str: ...
@overload
def render(x: str) -> str: ...
def render(x):
    return str(x)

class Holder:
    @property
    def value(self):
        return self._v

    def unrelated(self):
        return 0

    @value.setter
    def value(self, v):
        self._v = v
'''


def python_chunks(source: str) -> dict:
    """Qualified name → chunk, for a single-file source."""
    adapter = get_adapter("python")
    return {
        c.symbol_path.split("::", 1)[1]: c
        for c in adapter.extract_chunks("m.py", source)
    }


def test_python_adapter_addresses_a_definition_inside_a_conditional() -> None:
    # A `def` in an `except` branch binds the module namespace exactly as one at
    # the top of the file does, so it is `m.py::loads` and nothing else.
    chunks = python_chunks(CONDITIONAL)
    assert "loads" in chunks
    assert "batched" in chunks
    assert "Shape" in chunks
    assert "Shape.area" in chunks


def test_python_adapter_keeps_the_condition_a_definition_exists_under() -> None:
    # One name defined under a guard is one entity, and the guard is part of what
    # it IS — "only when `fast` is missing" is not a detail a description may lose.
    chunks = python_chunks(CONDITIONAL)
    assert chunks["loads"].source.startswith("try:")
    assert "except ImportError:" in chunks["loads"].source
    batched = chunks["batched"].source
    assert batched.startswith("if sys.version_info")
    assert "else:" in batched  # both branches, not whichever came first


def test_python_adapter_gives_one_name_one_address() -> None:
    adapter = get_adapter("python")
    paths = [c.symbol_path for c in adapter.extract_chunks("m.py", CONDITIONAL)]
    assert len(paths) == len(set(paths))
    # …and that address holds the implementation, not the first stub of an
    # overload set. Indexing the stub is how a working function came to be
    # described as doing nothing at all.
    render = python_chunks(CONDITIONAL)["render"].source
    assert "return str(x)" in render
    assert render.count("@overload") == 2


def test_python_adapter_joins_a_split_definition_without_swallowing_neighbours() -> None:
    # A property's accessors sit either side of another method here. Spanning
    # first-to-last would put `unrelated` inside `value`, so editing `unrelated`
    # would report `value` as changed.
    value = python_chunks(CONDITIONAL)["Holder.value"].source
    assert "@property" in value and "@value.setter" in value
    assert "unrelated" not in value


def test_python_adapter_module_chunk_is_the_glue_and_nothing_else() -> None:
    # It used to span the first top-level statement to the last, which is to say
    # the whole file: every constant and every conditionally defined function was
    # inside it, so an edit anywhere changed the module's own fingerprint too.
    module = python_chunks(CONDITIONAL)["__module__"].source
    assert "import sys" in module
    assert "PUBLIC = 1" not in module
    assert "def loads" not in module
    assert "class Holder" not in module


def test_python_adapter_keeps_a_conditional_with_no_definitions_as_glue() -> None:
    source = "import sys\n\nif sys.platform == 'win32':\n    sys.setrecursionlimit(2000)\n"
    assert "setrecursionlimit" in python_chunks(source)["__module__"].source


def test_python_adapter_splits_a_guard_that_defines_several_names() -> None:
    # No single entity to hand the statement to, so each definition keeps its own
    # address rather than two names sharing one chunk.
    source = (
        "import sys\n\n"
        "if sys.platform == 'win32':\n"
        "    def a():\n        return 1\n"
        "    def b():\n        return 2\n"
    )
    chunks = python_chunks(source)
    assert chunks["a"].source.startswith("def a")
    assert chunks["b"].source.startswith("def b")


# The shapes below are lifted from the corpora in `test/`, where an ast oracle over
# 382 files found the gap they pin: every public module name the walk gave no address
# to was assigned under a guard. `def` had that rule from the start ("the guard IS the
# definition"); the declarations a compat shim actually writes did not.


DECLARED_UNDER_A_GUARD = '''"""A module."""
import sys
import time
from typing import TYPE_CHECKING

if sys.platform == "win32":
    preferred_clock = time.perf_counter
else:
    preferred_clock = time.time

try:
    is_urllib3_1 = int(urllib3_version.split(".")[0]) == 1
except (TypeError, AttributeError):
    is_urllib3_1 = True

if TYPE_CHECKING:
    type Rows = list[dict]
'''


def test_python_adapter_addresses_a_declaration_a_guard_selects() -> None:
    # `preferred_clock` is a name of this module however the branch went, so a
    # description citing it must have somewhere to point. It had none: the walk
    # consulted `_module_assignment_name` only for direct children of the module,
    # so the whole statement fell into the glue and the name disappeared.
    chunks = python_chunks(DECLARED_UNDER_A_GUARD)
    assert "preferred_clock" in chunks
    assert "is_urllib3_1" in chunks
    assert "Rows" in chunks  # a PEP 695 alias under `if TYPE_CHECKING` too


def test_python_adapter_keeps_the_guard_a_declaration_was_selected_by() -> None:
    # The same rule the def case states: the guard is part of what the entity IS.
    # "perf_counter on Windows, time elsewhere" is the declaration; either branch
    # alone is a line of code with its reason removed.
    chunks = python_chunks(DECLARED_UNDER_A_GUARD)
    clock = chunks["preferred_clock"].source
    assert clock.startswith("if sys.platform")
    assert "perf_counter" in clock and "time.time" in clock  # both branches
    version = chunks["is_urllib3_1"].source
    assert version.startswith("try:")
    assert "except (TypeError, AttributeError):" in version


def test_python_adapter_leaves_a_declared_guard_out_of_the_glue() -> None:
    # It became a chunk, so it is no longer part of the module's own fingerprint —
    # otherwise changing the Windows branch would report the module as changed too.
    module = python_chunks(DECLARED_UNDER_A_GUARD)["__module__"].source
    assert "import sys" in module
    assert "preferred_clock" not in module
    assert "is_urllib3_1" not in module


MANY_NAMES_UNDER_A_GUARD = '''"""Optional dependencies, all probed at once."""
try:
    from urllib3.contrib import pyopenssl
except ImportError:
    pyopenssl = None
    OpenSSL = None
    cryptography = None
'''


def test_python_adapter_keeps_a_guard_declaring_several_names_as_glue() -> None:
    # Several names is the line, and it is drawn where the chunk would stop being
    # worth having: there is no single entity to hand the statement to, and the one
    # branch that assigns `pyopenssl` says `pyopenssl = None` — a fragment with no
    # guard and no siblings, which a describing pass can only read as setting a name
    # to None. In the glue they stay together, with the `try` that explains them.
    chunks = python_chunks(MANY_NAMES_UNDER_A_GUARD)
    assert "pyopenssl" not in chunks
    assert "OpenSSL" not in chunks
    module = chunks["__module__"].source
    assert "from urllib3.contrib import pyopenssl" in module
    assert "cryptography = None" in module


def test_python_adapter_keeps_a_script_entry_point_as_glue() -> None:
    # The names a `__main__` block binds are steps in a procedure, and an importer
    # never sees them at all — so nothing can cite them and no feature should bind
    # to one. They are on the several-names side of the line by arithmetic, which
    # is why no separate rule is needed for them.
    source = (
        '"""A script."""\n'
        "import argparse\n\n"
        'if __name__ == "__main__":\n'
        "    parser = argparse.ArgumentParser()\n"
        "    args = parser.parse_args()\n"
    )
    chunks = python_chunks(source)
    assert "args" not in chunks and "parser" not in chunks
    assert "parser.parse_args()" in chunks["__module__"].source


def test_python_adapter_does_not_address_an_iteration_variable() -> None:
    # A `for` target is not an assignment statement, so requests' module-level
    # `for package in (...)` contributes no name — and its body assigns three, which
    # puts the loop on the glue side where a temp belongs. A loop that leaves exactly
    # one public name IS read as declaring it, deliberately: the loop is then how the
    # value was computed, and the whole statement is a chunk worth describing.
    several = (
        "import sys\n\n"
        "for package in ('urllib3', 'idna'):\n"
        "    mod = sys.modules[package]\n"
        "    target = 'requests.packages.' + package\n"
        "    imported_mod = mod\n"
    )
    chunks = python_chunks(several)
    for name in ("package", "mod", "target", "imported_mod"):
        assert name not in chunks
    one = (
        "import os\n\n"
        "for candidate in ('a', 'b'):\n"
        "    chosen = os.path.exists(candidate)\n"
    )
    assert python_chunks(one)["chosen"].source.startswith("for candidate")


def test_python_adapter_still_gives_a_class_body_no_attribute_addresses() -> None:
    # The rule is module scope only. A class's attributes never had addresses — only
    # its methods and nested classes do — and a guarded one must not be the exception
    # that appears as `Config.cache` while its unguarded neighbours do not.
    source = (
        "from typing import TYPE_CHECKING\n\n"
        "class Config:\n"
        "    plain = 1\n"
        "    if TYPE_CHECKING:\n"
        "        cache = None\n"
        "    def get(self):\n        return self.plain\n"
    )
    chunks = python_chunks(source)
    assert "Config.get" in chunks
    assert "Config.cache" not in chunks and "Config.plain" not in chunks


def test_python_adapter_resolves_a_symbol_to_what_the_index_holds() -> None:
    # One traversal, so a link cannot point somewhere the index does not know.
    adapter = get_adapter("python")
    chunks = python_chunks(CONDITIONAL)
    for name in ("loads", "render", "Holder.value", "Shape.area"):
        assert adapter.resolve_symbol_path(CONDITIONAL, f"m.py::{name}") == (
            chunks[name].start_byte, chunks[name].end_byte,
        )
    assert adapter.resolve_symbol_path(CONDITIONAL, "m.py::nope") is None


def test_python_adapter_addresses_a_type_alias_in_either_spelling() -> None:
    # `Alias: TypeAlias = int` was already an addressable declaration; PEP 695
    # spells the same thing `type Alias = int`, and the newer syntax should not be
    # the one that vanishes into module glue.
    chunks = python_chunks(
        "type Alias = int | str\n"
        "Other: TypeAlias = int\n"
        "_Hidden: TypeAlias = int\n"
        "type _AlsoHidden = int\n"
    )
    assert "Alias" in chunks and "Other" in chunks
    assert "_Hidden" not in chunks and "_AlsoHidden" not in chunks


def test_python_adapter_reads_the_syntax_a_current_repo_contains() -> None:
    # The bundled grammar is old enough that this is worth stating rather than
    # assuming: a construct it cannot parse loses every definition in the damaged
    # region, silently.
    cases = {
        "generic_fn": "def first[T](xs: list[T]) -> T:\n    return xs[0]\n",
        "generic_cls": "class Box[T]:\n    def get(self) -> T: ...\n",
        "except_star": "def f():\n    try:\n        pass\n    except* ValueError:\n        pass\n",
        "unpack": "def g(*args: *tuple[int, str]) -> None:\n    return None\n",
        "async_gen": "async def h(s):\n    async with s as c:\n        async for r in c:\n            yield r\n",
    }
    for label, source in cases.items():
        assert parses_cleanly("m.py", source), label
        assert python_chunks(source), label


def test_typescript_module_chunk_excludes_the_declarations_between_the_glue() -> None:
    adapter = get_adapter("typescript")
    source = (
        "import { a } from './a';\n\n"
        "export function big() {\n    return a;\n}\n\n"
        "export const TAIL = 1;\n"
    )
    chunks = {c.symbol_path: c for c in adapter.extract_chunks("m.ts", source)}
    module = chunks["m.ts::__module__"].source
    assert "import { a }" in module
    assert "function big" not in module


def test_typescript_adapter_extracts_chunks(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    assert "tests/fixtures/sample_app.ts::Coordinator" in paths
    assert "tests/fixtures/sample_app.ts::Coordinator.query" in paths
    assert "tests/fixtures/sample_app.ts::makeOptions" in paths
    assert "tests/fixtures/sample_app.ts::ClientOptions" in paths


def test_typescript_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_typescript_adapter_fingerprint_stable_under_whitespace(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    assert chunks
    target = chunks[0]
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = target.source.replace("\n", "\n   \n")
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_typescript_adapter_reexport_barrel_module_chunk(fixtures_dir: Path) -> None:
    """A pure re-export barrel produces a single ``__module__`` chunk."""
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_index.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_index.ts", src)
    assert len(chunks) > 0
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))
    assert any(p.endswith("::__module__") for p in paths)
