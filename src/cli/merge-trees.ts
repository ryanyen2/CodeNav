#!/usr/bin/env npx tsx
/**
 * CLI: forward merge prior tree + re-encoded tree (policy: code wins grounded, user wins underspec).
 * Usage: npx tsx src/cli/merge-trees.ts <prior.md> <reencoded.md>
 * Output: JSON to stdout { merged_md: string, merge_summary: { ... }, error?: string }
 */

import fs from 'fs';
import { parseTreeBlock } from '../parser/tree-parser.js';
import { forwardMerge } from '../sync/absorb.js';
import { treeToMarkdown } from '../sync/tree-serializer.js';

function main(): void {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.log(
      JSON.stringify({
        merged_md: '',
        merge_summary: null,
        error: 'Usage: merge-trees.ts <prior.md> <reencoded.md>',
      })
    );
    process.exit(1);
    return;
  }
  try {
    const priorMd = fs.readFileSync(args[0], 'utf-8');
    const reencodedMd = fs.readFileSync(args[1], 'utf-8');
    const prior = parseTreeBlock(priorMd);
    const reencoded = parseTreeBlock(reencodedMd);
    const { mergedRoot, mergeSummary } = forwardMerge(prior.root, reencoded.root);
    const mergedMd = treeToMarkdown({ root: mergedRoot, deps: reencoded.deps ?? [] });
    console.log(
      JSON.stringify({
        merged_md: mergedMd,
        merge_summary: mergeSummary,
      })
    );
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    console.log(
      JSON.stringify({
        merged_md: '',
        merge_summary: null,
        error: message,
      })
    );
    process.exit(1);
  }
}

main();
