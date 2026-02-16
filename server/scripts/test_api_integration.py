#!/usr/bin/env -S uv run python
"""
Integration test: sync → tree edit → apply (dry_run) with full logging of operations,
context, and diff. Optionally simulate code edit and verify server sees delta.

Verifies:
- Tree edit returns operations that match the user's edit (correct targets: fpath, entity_name, line_range).
- Apply dry_run returns planned_changes and unified_diff; diff is valid (file paths, line context).
- Prompts and generation can be inspected by running the server with CODENAV_LOG_PROMPTS=1.

Usage (from server/, API running on 8001):
  uv run python scripts/test_api_integration.py [path]
  CODENAV_LOG_PROMPTS=1 uv run python main.py   # in another terminal for prompt/generation logs

Env: CODENAV_API_BASE, CODENAV_TEST_PATH, CODENAV_ANALYZE_TIMEOUT.
First run of tree_edit (or apply) may be slow (npx/tsx cold start); timeouts are 30–120s.
"""

import json
import os
import sys
from pathlib import Path

import requests

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent

BASE_URL = os.environ.get("CODENAV_API_BASE", "http://127.0.0.1:8001").rstrip("/")
TIMEOUT = int(os.environ.get("CODENAV_ANALYZE_TIMEOUT", "120"))
DEFAULT_PATH = REPO_ROOT / "test" / "small_python_repo"

SYNC_URL = f"{BASE_URL}/semantic_tree/sync"
TREE_URL = f"{BASE_URL}/semantic_tree/tree"
TREE_EDIT_URL = f"{BASE_URL}/semantic_tree/tree_edit"
APPLY_URL = f"{BASE_URL}/semantic_tree/apply"
APPLY_TREE_EDIT_URL = f"{BASE_URL}/semantic_tree/apply_tree_edit"


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


def apply(
    path: str,
    edited_md: str,
    dry_run: bool = True,
    diff_format: str = "unified_diff",
    base_tree_md: str | None = None,
) -> dict:
    body: dict = {
        "path": path,
        "edited_tree_md": edited_md,
        "dry_run": dry_run,
        "diff_format": diff_format,
    }
    if base_tree_md is not None:
        body["base_tree_md"] = base_tree_md
    r = requests.post(APPLY_URL, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CODENAV_TEST_PATH", str(DEFAULT_PATH))
    if not Path(path).is_dir():
        print(f"Path not found: {path}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("CodeNav API integration test (tree edit + apply dry_run)")
    print(f"  BASE_URL={BASE_URL}  PATH={path}")
    print("  For prompt/generation logs, run server with: CODENAV_LOG_PROMPTS=1 uv run python main.py")
    print("=" * 60)

    # 1. Sync
    print("\n1. Sync (force_full=True)...")
    try:
        data = sync(path, force_full=True)
    except requests.RequestException as e:
        print(f"   FAIL: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   body: {e.response.text[:500]}")
        return 2
    base_md = data.get("tree_md") or ""
    if not base_md:
        print("   No tree_md in response")
        return 3
    print(f"   entity_count={data.get('entity_count')}  tree_md length={len(base_md)}")
    print("   Tree snippet (first 600 chars):")
    print("   " + "\n   ".join(base_md[:600].splitlines()))

    # 2. Simulate user editing the tree (change one feature line)
    print("\n2. Simulate user tree edit (change one feature text)...")
    edited_md = base_md
    # Try to find a feature line (e.g. "Return a greeting" or "read dataset") and tweak it
    for old_phrase, new_phrase in [
        ("Return a greeting", "Return a greeting string (user-facing)"),
        ("read dataset", "read dataset from disk"),
        ("add two numbers", "add two numbers and return sum"),
    ]:
        if old_phrase in base_md:
            edited_md = base_md.replace(old_phrase, new_phrase, 1)
            print(f"   Replaced: {old_phrase!r} -> {new_phrase!r}")
            break
    if edited_md == base_md:
        # Fallback: change first $ feature line we can find
        lines = base_md.splitlines()
        for i, line in enumerate(lines):
            if " $ " in line or " ^ " in line:
                lines[i] = line.rstrip() + " (edited)"
                edited_md = "\n".join(lines)
                print(f"   Fallback: appended ' (edited)' to line {i+1}")
                break
    if edited_md == base_md:
        print("   WARNING: could not produce a tree edit; using base_md with one word changed")
        edited_md = base_md.replace("#resolved", "#draft", 1)

    # 3. Tree edit (server computes operations)
    print("\n3. POST /tree_edit (context preparation: base vs edited -> operations)...")
    try:
        edit_resp = tree_edit(path, base_md, edited_md)
    except requests.RequestException as e:
        print(f"   FAIL: {e}")
        return 4
    ops = edit_resp.get("operations", [])
    print(f"   operations count: {len(ops)}")
    for i, op in enumerate(ops):
        print(f"   op[{i}] op={op.get('op')!r} target={op.get('target', '')[:60]!r}")
        for j, t in enumerate(op.get("targets") or []):
            print(f"      target[{j}] fpath={t.get('fpath')} entity_name={t.get('entity_name')} line_range={t.get('line_range')}")
        if op.get("params"):
            print(f"      params={json.dumps(op.get('params'), default=str)[:120]}")
    if not ops:
        print("   WARNING: no operations (tree diff may be empty or TS tree-edit-targets returned none)")

    # 4. Apply dry_run with unified_diff (send base_tree_md so only our edit produces ops)
    print("\n4. POST /apply (dry_run=True, diff_format=unified_diff, base_tree_md=base from sync)...")
    try:
        apply_resp = apply(
            path, edited_md, dry_run=True, diff_format="unified_diff", base_tree_md=base_md
        )
    except requests.RequestException as e:
        print(f"   FAIL: {e}")
        return 5
    planned = apply_resp.get("planned_changes", [])
    unified_diff = apply_resp.get("unified_diff") or ""
    print(f"   applied={apply_resp.get('applied')}  planned_changes count={len(planned)}")
    for i, c in enumerate(planned[:5]):
        print(f"   change[{i}] fpath={c.get('fpath')} L{c.get('line_start')}-{c.get('line_end')} new_content_len={len(c.get('new_content') or '')}")
    if len(planned) > 5:
        print(f"   ... and {len(planned) - 5} more")
    print("   unified_diff (full, to verify file paths and line context):")
    if unified_diff:
        for line in unified_diff.splitlines():
            print("   | " + line)
    else:
        print("   (empty — no changes or diff_format not requested)")

    # 5. Sanity: diff should reference real files from the codebase when there are planned changes
    if planned and path:
        root = Path(path)
        fpaths_in_diff = {p.get("fpath") for p in planned if p.get("fpath")}
        for fpath in list(fpaths_in_diff)[:3]:
            full = root / fpath
            exists = full.is_file()
            print(f"   file_exists {fpath}: {exists}")

    print("\n" + "=" * 60)
    print("Done. Check that: (1) operations match the edit, (2) planned_changes have correct fpath/line_range,")
    print("(3) unified_diff shows expected file paths and line hunks. Run with CODENAV_LOG_PROMPTS=1 for prompts.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is the API running? (e.g. uv run python main.py)", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as e:
        print(f"RuntimeError: {e}", file=sys.stderr)
        sys.exit(3)
