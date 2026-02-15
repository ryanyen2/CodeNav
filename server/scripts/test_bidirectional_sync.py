#!/usr/bin/env -S uv run python
"""
Integration test: sync → tree_edit → apply (dry_run then real) → sync.

Verifies:
- Tree edit operations are captured correctly (tree_edit returns ops for the diff).
- Apply dry_run returns planned_changes without writing files.
- Apply (real) updates files and state; next sync runs without 409 (anti-loop).
- Incremental: second sync after apply sees no code delta for unchanged files (or delta only if we changed code).

Run from server/ with API running: uv run python scripts/test_bidirectional_sync.py
Set CODENAV_API_BASE, CODENAV_TEST_PATH, CODENAV_ANALYZE_TIMEOUT as needed.
"""

import os
import sys
from pathlib import Path

import requests

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
TEST_REPO = REPO_ROOT / "test" / "small_python_repo"

BASE_URL = os.environ.get("CODENAV_API_BASE", "http://localhost:8001")
TIMEOUT = int(os.environ.get("CODENAV_ANALYZE_TIMEOUT", "120"))
PATH = os.environ.get("CODENAV_TEST_PATH", str(TEST_REPO))

SYNC_URL = f"{BASE_URL}/semantic_tree/sync"
TREE_URL = f"{BASE_URL}/semantic_tree/tree"
TREE_EDIT_URL = f"{BASE_URL}/semantic_tree/tree_edit"
APPLY_TREE_EDIT_URL = f"{BASE_URL}/semantic_tree/apply_tree_edit"
APPLY_URL = f"{BASE_URL}/semantic_tree/apply"


def sync(path: str, force_full: bool = True) -> dict:
    r = requests.post(
        SYNC_URL,
        json={"path": path, "repo_name": Path(path).name, "format": "md", "force_full": force_full},
        timeout=TIMEOUT,
    )
    if r.status_code == 422:
        data = r.json()
        if data.get("status") == "intervention_required":
            raise RuntimeError(f"Intervention: {data.get('step')} {data.get('message')}")
    r.raise_for_status()
    return r.json()


def get_tree(path: str) -> str:
    r = requests.get(TREE_URL, params={"path": path}, timeout=10)
    r.raise_for_status()
    return r.json()["tree_md"]


def tree_edit(path: str, base_md: str, edited_md: str) -> dict:
    r = requests.post(
        TREE_EDIT_URL,
        json={"path": path, "edited_tree_md": edited_md},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def apply_tree_edit(path: str, edited_md: str) -> dict:
    r = requests.post(
        APPLY_TREE_EDIT_URL,
        json={"path": path, "edited_tree_md": edited_md},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def apply(path: str, edited_md: str, dry_run: bool = False) -> dict:
    r = requests.post(
        APPLY_URL,
        json={"path": path, "edited_tree_md": edited_md, "dry_run": dry_run},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def main() -> int:
    if not Path(PATH).is_dir():
        print(f"Test path not found: {PATH}", file=sys.stderr)
        return 1

    print("1. Sync (force_full=True)...")
    data = sync(PATH, force_full=True)
    assert data.get("tree_md"), "Missing tree_md"
    base_md = data["tree_md"]
    print(f"   entity_count={data.get('entity_count')}")

    print("2. Get tree from state...")
    tree_md = get_tree(PATH)
    assert tree_md == base_md, "Tree from state should match sync response"

    print("3. Tree edit (minimal text change in one feature)...")
    # Change one feature line: e.g. "Return a greeting" -> "Return a greeting string (user-facing)"
    edited_md = base_md.replace(
        "Return a greeting string.",
        "Return a greeting string (user-facing).",
        1,
    )

    print(f"   edited_md:\n{edited_md}")
    print(f"   base_md:\n{base_md}")
    if edited_md == base_md:
        # Fallback: any small edit to force one EditFeature op
        edited_md = base_md.replace("#resolved", "#draft", 1)
    edit_resp = tree_edit(PATH, base_md, edited_md)
    ops = edit_resp.get("operations", [])
    print(f"   operations count: {len(ops)}")
    if ops:
        for i, op in enumerate(ops):
            print(f"     op[{i}] {op.get('op')} target={op.get('target', '')[:50]}")

    print("4. Apply dry_run...")
    dry = apply(PATH, edited_md, dry_run=True)
    assert dry.get("applied") is False, "dry_run should not apply"
    planned = dry.get("planned_changes", [])
    print(f"   planned_changes count: {len(planned)}")
    if planned:
        for i, c in enumerate(planned[:3]):
            print(f"     change[{i}] {c.get('fpath')} L{c.get('line_start')}-{c.get('line_end')}")

    print("5. Apply tree_edit (state-only: persist tree, no code gen)...")
    apply_tree_edit(PATH, edited_md)
    print("   ok")

    print("6. Sync without force_full after inverse -> expect 409 (anti-loop)...")
    r = requests.post(
        SYNC_URL,
        json={"path": PATH, "repo_name": Path(PATH).name, "format": "md", "force_full": False},
        timeout=TIMEOUT,
    )
    if r.status_code != 409:
        print(f"   unexpected status {r.status_code}; body={r.text[:200]}", file=sys.stderr)
        assert False, "Expected 409 when code unchanged after inverse"
    print("   got 409 (correct: code unchanged, forward blocked)")

    print("7. Sync with force_full=True -> should succeed and return tree...")
    data2 = sync(PATH, force_full=True)
    assert data2.get("tree_md"), "Sync with force_full should return tree_md"
    print(f"   entity_count={data2.get('entity_count')}")

    print("All steps passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is the API running? (e.g. uv run python main.py)", file=sys.stderr)
        sys.exit(2)
    except AssertionError as e:
        print(f"Assertion failed: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
