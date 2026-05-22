#!/usr/bin/env python3
"""Minimal codoc MCP stdio server (JSON-RPC 2.0 over stdio, MCP spec)."""
from __future__ import annotations
import json
import os
import sys
import httpx
from typing import Any

PORT = int(os.environ.get("CODOC_SERVER_PORT", "8001"))
ROOT_DIR = os.environ.get("CODOC_ROOT_DIR", "")
BASE_URL = f"http://127.0.0.1:{PORT}"

TOOLS = [
    {
        "name": "list_pending_proposals",
        "description": "List all pending codoc proposals awaiting user review.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "accept_proposal",
        "description": "Accept a pending codoc proposal by its HLC string.",
        "inputSchema": {
            "type": "object",
            "properties": {"hlc": {"type": "string", "description": "HLC of the proposal to accept"}},
            "required": ["hlc"],
        },
    },
    {
        "name": "reject_proposal",
        "description": "Reject a pending codoc proposal by its HLC string.",
        "inputSchema": {
            "type": "object",
            "properties": {"hlc": {"type": "string", "description": "HLC of the proposal to reject"}},
            "required": ["hlc"],
        },
    },
    {
        "name": "show_feature",
        "description": "Show details for a codoc feature by slug path (slash-separated).",
        "inputSchema": {
            "type": "object",
            "properties": {"slug_path": {"type": "string"}},
            "required": ["slug_path"],
        },
    },
    {
        "name": "find_feature_owning_symbol",
        "description": "Find which codoc feature owns a given symbol in a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Repo-relative file path"},
                "symbol": {"type": "string", "description": "Symbol/function name to look up"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "get_feature_tree",
        "description": "Get the full codoc feature tree as a .codoc text document.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "reflect_now",
        "description": "Force immediate reflect on given files (bypass debounce).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "string", "description": "Comma-separated or JSON-array of repo-relative paths"}
            },
            "required": ["paths"],
        },
    },
]

RESOURCES = [
    {
        "uri": "codoc://tree",
        "name": "Codoc Feature Tree",
        "description": "The current codoc feature tree (_index.codoc)",
        "mimeType": "text/plain",
    },
]


def _get(path: str, **params) -> Any:
    params["root_dir"] = ROOT_DIR
    r = httpx.get(f"{BASE_URL}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> Any:
    if "root_dir" not in body:
        body["root_dir"] = ROOT_DIR
    r = httpx.post(f"{BASE_URL}{path}", json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def _tool_list_pending(args: dict) -> str:
    return json.dumps(_get("/tx/pending"), indent=2)


def _tool_accept(args: dict) -> str:
    return json.dumps(_post(f"/tx/{args['hlc']}/accept", {}))


def _tool_reject(args: dict) -> str:
    return json.dumps(_post(f"/tx/{args['hlc']}/reject", {}))


def _tool_show_feature(args: dict) -> str:
    return json.dumps(_get("/feature/by-slug", slug=args["slug_path"]), indent=2)


def _tool_find_feature_owning_symbol(args: dict) -> str:
    bindings = _get("/bindings/by-file", file=args["file"])
    symbol = args.get("symbol", "")
    if symbol:
        bindings = [b for b in bindings if symbol in b.get("anchor", {}).get("symbol_path", "")]
    return json.dumps(bindings, indent=2)


def _tool_get_feature_tree(args: dict) -> str:
    result = _get("/tree.codoc")
    return result.get("files", {}).get("_index.codoc", "")


def _tool_reflect_now(args: dict) -> str:
    raw = args.get("paths", "")
    if isinstance(raw, list):
        paths = raw
    else:
        try:
            paths = json.loads(raw)
        except Exception:
            paths = [p.strip() for p in raw.split(",") if p.strip()]
    return json.dumps(_post("/reflect/file", {"paths": paths}))


_TOOL_DISPATCH = {
    "list_pending_proposals": _tool_list_pending,
    "accept_proposal": _tool_accept,
    "reject_proposal": _tool_reject,
    "show_feature": _tool_show_feature,
    "find_feature_owning_symbol": _tool_find_feature_owning_symbol,
    "get_feature_tree": _tool_get_feature_tree,
    "reflect_now": _tool_reflect_now,
}


def _call_tool(name: str, args: dict) -> str:
    handler = _TOOL_DISPATCH.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        return handler(args)
    except httpx.HTTPError as e:
        return json.dumps({"error": str(e)})
    except KeyError as e:
        return json.dumps({"error": f"Missing required argument: {e}"})


def _read_resource(uri: str) -> str:
    if uri == "codoc://tree":
        try:
            result = _get("/tree.codoc")
            files = result.get("files", {})
            return files.get("_index.codoc", "")
        except Exception as e:
            return f"Error reading tree: {e}"
    return f"Unknown resource: {uri}"


def _respond(req_id: Any, result: Any) -> None:
    resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
    line = json.dumps(resp)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _error(req_id: Any, code: int, message: str) -> None:
    resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    line = json.dumps(resp)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def run() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            _error(None, -32700, "Parse error")
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "codoc", "version": "0.1.0"},
            })
        elif method == "tools/list":
            _respond(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            text = _call_tool(tool_name, tool_args)
            _respond(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        elif method == "resources/list":
            _respond(req_id, {"resources": RESOURCES})
        elif method == "resources/read":
            uri = params.get("uri", "")
            content = _read_resource(uri)
            _respond(req_id, {
                "contents": [{"uri": uri, "mimeType": "text/plain", "text": content}]
            })
        elif method == "notifications/initialized":
            pass  # No response needed for notifications
        elif method == "ping":
            _respond(req_id, {})
        else:
            if req_id is not None:
                _error(req_id, -32601, f"Method not found: {method}")
