from codoc.index.lang.base import LanguageAdapter
from codoc.index.lang.python import PythonAdapter
from codoc.index.lang.typescript import TypeScriptAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the appropriate adapter for the given language name."""
    adapters = {
        "python": PythonAdapter,
        "typescript": TypeScriptAdapter,
        "tsx": lambda: TypeScriptAdapter("tsx"),
    }
    make = adapters.get(language.lower())
    if make is None:
        raise ValueError(
            f"No adapter for language: {language!r}. Supported: {list(adapters)}"
        )
    return make()


EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
}


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    from pathlib import Path
    return EXTENSIONS.get(Path(file_path).suffix.lower())
