from codoc.lang.base import LanguageAdapter
from codoc.lang.notebook import NotebookAdapter
from codoc.lang.python import PythonAdapter
from codoc.lang.typescript import TypeScriptAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the appropriate adapter for the given language name."""
    adapters = {
        "python": PythonAdapter,
        "notebook": NotebookAdapter,
        "typescript": TypeScriptAdapter,
        "tsx": TypeScriptAdapter,
    }
    cls = adapters.get(language.lower())
    if cls is None:
        raise ValueError(
            f"No adapter for language: {language!r}. Supported: {list(adapters)}"
        )
    return cls()


def parses_cleanly(file_path: str, source: str) -> bool | None:
    """True if *source* reads as a whole document; None if nothing here reads it.

    A file that does not parse is not evidence about what it contains. tree-sitter
    recovers what it can and drops the rest, so the chunks it yields are a LOWER
    BOUND on the file's entities, never an inventory — a definition inside a
    damaged region simply is not there. Callers that would read an absent chunk as
    a DELETED entity have to know the difference; see
    ``loop/diff._hold_unparseable_removals``.

    **None for a file no adapter claims**, because "nothing read it" and "it is
    damaged" are opposite answers and a bool cannot hold both. It said False, which
    every caller had to remember meant "cannot tell" — and the moment a second kind
    of readable file entered the index (settings files, which have no adapter and
    parse perfectly well) that False started reading as "damaged" and held removals
    from files that were fine.

    The verdict is the ADAPTER'S, not this function's. It used to be the tree-sitter
    parse and nothing else, which made a grammar gap indistinguishable from damage;
    Python's adapter now asks the running interpreter as well, and it is the adapter
    that knows which readers it has (`lang/python.PythonAdapter.reads_cleanly`).
    """
    language = detect_language(file_path)
    if language is None:
        return None
    return get_adapter(language).reads_cleanly(source)


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    ext_map = {
        ".py": "python",
        ".ipynb": "notebook",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
    }
    from pathlib import Path
    suffix = Path(file_path).suffix.lower()
    return ext_map.get(suffix)
