#!/usr/bin/env -S uv run python
"""
Test all CodeNav API endpoints. Verifies response shape and status codes.

Usage (from server/, with API running on port 8001):
  uv run python scripts/test_api.py

Set CODENAV_API_BASE (default http://127.0.0.1:8001) and optionally CODENAV_TEST_PATH
(for analyze/sync/tree; default: test/draco relative to repo root).
Endpoints that need embeddings (analyze, sync, search) may return 422 when Ollama/embedder
is not running; the script asserts the intervention response shape in that case.
"""

import os
import sys
from pathlib import Path

import requests

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent

BASE_URL = os.environ.get("CODENAV_API_BASE", "http://127.0.0.1:8001").rstrip("/")
TEST_PATH = os.environ.get("CODENAV_TEST_PATH", str(REPO_ROOT / "test" / "draco"))
TIMEOUT = 30
# Tree edit can be slow first time (npx/tsx cold start); allow up to 60s
TREE_EDIT_TIMEOUT = 60

# Minimal tree markdown for tree_edit (no embedder needed)
BASE_TREE_MD = """- ~ root
  - % foo.py
    - $ do something (run)

deps:
  (run) --invokes--> (ext:bar)
"""

EDITED_TREE_MD = """- ~ root
  - % foo.py
    - $ do something else (run)
    - $ new func (helper)

deps:
  (run) --invokes--> (helper)
"""


def ok(name: str, res: requests.Response, want_status: int = 200) -> bool:
    if res.status_code == want_status:
        print(f"  OK   {name} -> {res.status_code}")
        return True
    print(f"  FAIL {name} -> {res.status_code} (expected {want_status})")
    try:
        print(f"       body: {res.text[:300]}")
    except Exception:
        pass
    return False


def test_health():
    """GET /health -> 200, body has status healthy."""
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    if not ok("GET /health", r):
        return False
    data = r.json()
    if data.get("status") != "healthy":
        print(f"       expected status=healthy, got {data.get('status')}")
        return False
    return True


def test_root():
    """GET / -> 200, body has endpoints."""
    r = requests.get(f"{BASE_URL}/", timeout=5)
    if not ok("GET /", r):
        return False
    data = r.json()
    if "endpoints" not in data:
        print("       expected 'endpoints' in body")
        return False
    return True


def test_status_no_param():
    """GET /semantic_tree/status (no query) -> 200."""
    r = requests.get(f"{BASE_URL}/semantic_tree/status", timeout=5)
    if not ok("GET /semantic_tree/status", r):
        return False
    data = r.json()
    if "index_size" not in data:
        print("       expected index_size in body")
        return False
    return True


def test_status_with_index_path():
    """GET /semantic_tree/status?index_path=... -> 200."""
    r = requests.get(
        f"{BASE_URL}/semantic_tree/status",
        params={"index_path": "/nonexistent/path"},
        timeout=5,
    )
    if not ok("GET /semantic_tree/status?index_path=...", r):
        return False
    data = r.json()
    if data.get("index_size", -1) != 0:
        print(f"       expected index_size=0 for missing index, got {data.get('index_size')}")
        return False
    return True


def test_tree_no_state():
    """GET /semantic_tree/tree?path=... (no state) -> 404."""
    r = requests.get(
        f"{BASE_URL}/semantic_tree/tree",
        params={"path": "/nonexistent/codebase/path"},
        timeout=5,
    )
    if not ok("GET /semantic_tree/tree (no state)", r, want_status=404):
        return False
    return True


def test_tree_edit():
    """POST /semantic_tree/tree_edit with base_tree_md + edited_tree_md -> 200, operations array."""
    r = requests.post(
        f"{BASE_URL}/semantic_tree/tree_edit",
        json={
            "base_tree_md": BASE_TREE_MD,
            "edited_tree_md": EDITED_TREE_MD,
        },
        timeout=TREE_EDIT_TIMEOUT,
    )
    if not ok("POST /semantic_tree/tree_edit", r):
        return False
    data = r.json()
    if "operations" not in data:
        print("       expected 'operations' in body")
        return False
    if not isinstance(data["operations"], list):
        print("       expected operations to be a list")
        return False
    return True


def test_tree_edit_missing_body():
    """POST /semantic_tree/tree_edit without base_tree_md or path -> 422 or 400."""
    r = requests.post(
        f"{BASE_URL}/semantic_tree/tree_edit",
        json={"edited_tree_md": "x"},
        timeout=5,
    )
    if r.status_code not in (400, 422):
        print(f"  FAIL POST /semantic_tree/tree_edit (no base) -> {r.status_code} (expected 400/422)")
        return False
    print(f"  OK   POST /semantic_tree/tree_edit (no base) -> {r.status_code}")
    return True


def test_analyze():
    """POST /semantic_tree/analyze -> 200 (tree_md) or 422 (intervention_required when embedder down)."""
    path = TEST_PATH
    if not Path(path).is_dir():
        print(f"  SKIP POST /semantic_tree/analyze (path not found: {path})")
        return True
    r = requests.post(
        f"{BASE_URL}/semantic_tree/analyze",
        json={"path": path, "format": "md"},
        timeout=60,
    )
    if r.status_code == 200:
        data = r.json()
        if "tree_md" not in data and "tree_json" not in data:
            print("       expected tree_md or tree_json in body")
            return False
        if "root_dir" not in data or "entity_count" not in data:
            print("       expected root_dir, entity_count in body")
            return False
        print(f"  OK   POST /semantic_tree/analyze -> 200 (entity_count={data.get('entity_count')})")
        return True
    if r.status_code == 422:
        data = r.json()
        if data.get("status") != "intervention_required":
            print(f"       expected intervention_required, got {data.get('status')}")
            return False
        print(f"  OK   POST /semantic_tree/analyze -> 422 intervention_required (step={data.get('step')})")
        return True
    print(f"  FAIL POST /semantic_tree/analyze -> {r.status_code}")
    print(f"       {r.text[:400]}")
    return False


def test_sync():
    """POST /semantic_tree/sync -> 200 or 422 (same as analyze when embedder down)."""
    path = TEST_PATH
    if not Path(path).is_dir():
        print(f"  SKIP POST /semantic_tree/sync (path not found: {path})")
        return True
    r = requests.post(
        f"{BASE_URL}/semantic_tree/sync",
        json={"path": path, "format": "md", "force_full": True},
        timeout=60,
    )
    if r.status_code == 200:
        data = r.json()
        if "tree_md" not in data and "tree_json" not in data:
            print("       expected tree_md or tree_json in body")
            return False
        print(f"  OK   POST /semantic_tree/sync -> 200")
        return True
    if r.status_code == 422:
        data = r.json()
        if data.get("status") != "intervention_required":
            print(f"       expected intervention_required, got {data.get('status')}")
            return False
        print(f"  OK   POST /semantic_tree/sync -> 422 intervention_required (step={data.get('step')})")
        return True
    print(f"  FAIL POST /semantic_tree/sync -> {r.status_code}")
    print(f"       {r.text[:400]}")
    return False


def test_apply_tree_edit_no_state():
    """POST /semantic_tree/apply_tree_edit without state -> 400."""
    r = requests.post(
        f"{BASE_URL}/semantic_tree/apply_tree_edit",
        json={"path": "/nonexistent", "edited_tree_md": EDITED_TREE_MD},
        timeout=5,
    )
    if r.status_code != 400:
        print(f"  FAIL POST /semantic_tree/apply_tree_edit (no state) -> {r.status_code} (expected 400)")
        return False
    print("  OK   POST /semantic_tree/apply_tree_edit (no state) -> 400")
    return True


def test_apply_no_state():
    """POST /semantic_tree/apply without state -> 400."""
    r = requests.post(
        f"{BASE_URL}/semantic_tree/apply",
        json={"path": "/nonexistent", "edited_tree_md": EDITED_TREE_MD},
        timeout=5,
    )
    if r.status_code != 400:
        print(f"  FAIL POST /semantic_tree/apply (no state) -> {r.status_code} (expected 400)")
        return False
    print("  OK   POST /semantic_tree/apply (no state) -> 400")
    return True


def test_search_no_index():
    """POST /semantic_tree/search with nonexistent index_path -> 404."""
    r = requests.post(
        f"{BASE_URL}/semantic_tree/search",
        json={"query": "hello", "index_path": "/nonexistent", "top_k": 5},
        timeout=10,
    )
    if r.status_code not in (400, 404):
        print(f"  FAIL POST /semantic_tree/search (no index) -> {r.status_code} (expected 400/404)")
        return False
    print(f"  OK   POST /semantic_tree/search (no index) -> {r.status_code}")
    return True


def main():
    fast_only = os.environ.get("CODENAV_FAST_TESTS", "").strip() in ("1", "true", "yes")
    print(f"CodeNav API tests — BASE_URL={BASE_URL} TEST_PATH={TEST_PATH}" + (" [fast only]" if fast_only else "") + "\n")
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
    except requests.RequestException as e:
        print(f"Could not reach API at {BASE_URL}: {e}")
        print("Start the server with: uv run python main.py")
        sys.exit(1)

    tests = [
        test_health,
        test_root,
        test_status_no_param,
        test_status_with_index_path,
        test_tree_no_state,
        test_tree_edit,
        test_tree_edit_missing_body,
    ]
    if not fast_only:
        tests.extend([
            test_analyze,
            test_sync,
        ])
    tests.extend([
        test_apply_tree_edit_no_state,
        test_apply_no_state,
        test_search_no_index,
    ])
    passed = 0
    for t in tests:
        try:
            if t():
                passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)


if __name__ == "__main__":
    main()
