#!/usr/bin/env -S uv run python
"""
Call POST /semantic_tree/analyze with the small test codebase and print the semantic tree.
Run from server/:  uv run python scripts/call_analyze_and_show.py

Requires the API server to be running (e.g. uv run python main.py in another terminal).
Uses test/small_python_repo by default so the request finishes in under a minute.
"""

import json
import os
import sys
from pathlib import Path

import requests

# Repo root is parent of server/
SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
SMALL_REPO = REPO_ROOT / "test" / "small_python_repo"

BASE_URL = os.environ.get("CODENAV_API_BASE", "http://localhost:8001")
ANALYZE_URL = f"{BASE_URL}/semantic_tree/analyze"
TIMEOUT = int(os.environ.get("CODENAV_ANALYZE_TIMEOUT", "120"))


def main() -> None:
    path = os.environ.get("CODENAV_ANALYZE_PATH", str(SMALL_REPO))
    if not Path(path).is_dir():
        print(f"Codebase path not found: {path}", file=sys.stderr)
        print("Default is test/small_python_repo. Set CODENAV_ANALYZE_PATH to override.", file=sys.stderr)
        sys.exit(1)

    repo_name = "small_python_repo" if "small_python_repo" in path else Path(path).name
    body = {
        "path": path,
        "repo_name": repo_name,
        "provider": "openai",
        "model": "gpt-5-mini",
        "format": "md",
    }

    print("POST", ANALYZE_URL)
    print("path:", path)
    print("timeout:", TIMEOUT, "s\n")

    try:
        r = requests.post(ANALYZE_URL, json=body, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        print("Request timed out. Use a smaller codebase (e.g. test/small_python_repo) or set CODENAV_ANALYZE_TIMEOUT.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print("Connection failed. Is the server running? (e.g. uv run python main.py)", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)

    if r.status_code == 422:
        data = r.json()
        if data.get("status") == "intervention_required":
            print("Intervention required:")
            print("  step:", data.get("step"))
            print("  message:", data.get("message"))
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
