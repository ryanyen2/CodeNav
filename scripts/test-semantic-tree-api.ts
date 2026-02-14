/**
 * Integration test: call semantic tree analyze API and verify response is parseable by parseTreeBlock.
 * Uses test/requests as the codebase. Run with: npx tsx scripts/test-semantic-tree-api.ts
 *
 * Prerequisites:
 * - Server running (e.g. from server/: uv run python main.py or uv run uvicorn api.api:app --port 8001)
 * - OPENAI_API_KEY in server/.env
 */

import { resolve } from "path";
import { parseTreeBlock } from "../src/parser/tree-parser.js";

const BASE_URL = process.env.CODENAV_API_BASE ?? "http://localhost:8001";
const ANALYZE_URL = `${BASE_URL}/semantic_tree/analyze`;

function countNodes(node: { children?: unknown[] }): number {
  let n = 1;
  for (const c of node.children ?? []) n += countNodes(c as { children?: unknown[] });
  return n;
}

async function main(): Promise<void> {
  const codebasePath = resolve(process.cwd(), "test", "requests");
  const body = {
    path: codebasePath,
    repo_name: "requests",
    provider: "openai",
    model: "gpt-5-mini",
    format: "md",
  };

  console.log("POST", ANALYZE_URL);
  console.log("body.path:", body.path);

  const controller = new AbortController();
  const timeoutMs = Number(process.env.CODENAV_ANALYZE_TIMEOUT_MS) || 600_000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const res = await fetch(ANALYZE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  clearTimeout(timeoutId);

  if (res.status === 422) {
    const data = (await res.json()) as { status?: string; step?: string; message?: string };
    if (data.status === "intervention_required") {
      console.error("Intervention required (fix and retry):");
      console.error("  step:", data.step);
      console.error("  message:", data.message);
      process.exit(2);
    }
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Analyze failed ${res.status}: ${text}`);
  }

  const data = (await res.json()) as {
    tree_md?: string;
    tree_json?: unknown;
    root_dir: string;
    file_count: number;
    entity_count: number;
  };

  if (!data.tree_md) {
    throw new Error("Response missing tree_md (use format=md)");
  }

  const tree = parseTreeBlock(data.tree_md);
  const nodeCount = countNodes(tree.root);

  console.log("OK — tree parseable by parseTreeBlock()");
  console.log("root_dir:", data.root_dir);
  console.log("file_count:", data.file_count);
  console.log("entity_count:", data.entity_count);
  console.log("tree nodes:", nodeCount);
  console.log("deps:", tree.deps.length);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
