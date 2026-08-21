from codoc.lang.base import LanguageAdapter
from codoc.lang.python import PythonAdapter
from codoc.lang.typescript import TypeScriptAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the appropriate adapter for the given language name."""
    adapters = {
        "python": PythonAdapter,
        "typescript": TypeScriptAdapter,
        "tsx": TypeScriptAdapter,
    }
    cls = adapters.get(language.lower())
    if cls is None:
        raise ValueError(
            f"No adapter for language: {language!r}. Supported: {list(adapters)}"
        )
    return cls()


def parses_cleanly(file_path: str, source: str) -> bool:
    """True if *source* parses with no error and nothing missing.

    A file that does not parse is not evidence about what it contains. tree-sitter
    recovers what it can and drops the rest, so the chunks it yields are a LOWER
    BOUND on the file's entities, never an inventory — a definition inside a
    damaged region simply is not there. Callers that would read an absent chunk as
    a DELETED entity have to know the difference; see
    ``loop/diff._hold_unparseable_removals``.

    Unsupported for a file no adapter claims: nothing parsed it, so there is no
    parse to call clean. Says False, and the caller treats that as "cannot tell".
    """
    language = detect_language(file_path)
    if language is None:
        return False
    tree = get_adapter(language).parse(source)
    cursor = tree.walk()
    while True:
        node = cursor.node
        if node.type == "ERROR" or node.is_missing:
            return False
        # `has_error` is true for any ancestor of an error, so a subtree without it
        # cannot contain one and is skipped whole — the walk costs the damaged path
        # only, not the file.
        if node.has_error and cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return True


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    ext_map = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
    }
    from pathlib import Path
    suffix = Path(file_path).suffix.lower()
    return ext_map.get(suffix)
