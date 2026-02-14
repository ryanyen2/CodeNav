#!/usr/bin/env -S uv run python
"""
Call POST /semantic_tree/sync (force_full=True) to build the semantic tree and print it.
Run from server/:  uv run python scripts/call_analyze_and_show.py

Requires the API server to be running (e.g. uv run python main.py in another terminal).
Uses test/small_python_repo by default. Set CODENAV_ANALYZE_PATH to use another codebase.
"""

import os
import sys
from pathlib import Path

import requests

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
SMALL_REPO = REPO_ROOT / "test" / "small_python_repo"

BASE_URL = os.environ.get("CODENAV_API_BASE", "http://localhost:8001")
SYNC_URL = f"{BASE_URL}/semantic_tree/sync"
TIMEOUT = int(os.environ.get("CODENAV_ANALYZE_TIMEOUT", "120"))


def main() -> None:
    path = os.environ.get("CODENAV_ANALYZE_PATH", str(SMALL_REPO))
    if not Path(path).is_dir():
        print(f"Codebase path not found: {path}", file=sys.stderr)
        print("Default is test/small_python_repo. Set CODENAV_ANALYZE_PATH to override.", file=sys.stderr)
        sys.exit(1)

    repo_name = Path(path).name
    body = {
        "path": path,
        "repo_name": repo_name,
        "provider": "openai",
        "model": os.environ.get("CODENAV_LLM_MODEL", "gpt-4o-mini"),
        "format": "md",
        "force_full": True,
    }

    print("POST", SYNC_URL)
    print("path:", path)
    print("timeout:", TIMEOUT, "s\n")

    try:
        r = requests.post(SYNC_URL, json=body, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        print("Request timed out. Use a smaller codebase or set CODENAV_ANALYZE_TIMEOUT.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is the server running? (e.g. uv run python main.py)", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 422:
        data = r.json()
        if data.get("status") == "intervention_required":
            print("Intervention required:", data.get("step"), data.get("message"))
            sys.exit(2)
    r.raise_for_status()

    data = r.json()
    tree_md = data.get("tree_md")
    if not tree_md:
        print("Response missing tree_md:", list(data.keys()))
        sys.exit(1)

    print("--- Response ---")
    print("root_dir:", data.get("root_dir"))
    print("file_count:", data.get("file_count"))
    print("entity_count:", data.get("entity_count"))
    print()
    print("--- Semantic tree (tree_md) ---")
    print(tree_md)
    print("--- end ---")


if __name__ == "__main__":
    main()
