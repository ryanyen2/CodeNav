#!/usr/bin/env node
/**
 * CLI: parse a semantic tree from stdin or a test case file.
 * Usage: npx tsx src/cli/parse-tree.ts [path-to-test_cases.md]
 *   If no path, reads from stdin. Looks for --- TREE (BEFORE) --- or first "- ~" block.
 */

import { readFileSync } from 'fs';
import { createInterface } from 'readline';
import { parseTreeBlock, extractTreeBlockFromTestCase } from '../parser/tree-parser.js';

async function main() {
  let content: string;
  const arg = process.argv[2];
  if (arg) {
    content = readFileSync(arg, 'utf-8');
    // If file has multiple sections, use first block that actually contains tree lines (not placeholder)
    let before = extractTreeBlockFromTestCase(content, 'BEFORE');
    let after = extractTreeBlockFromTestCase(content, 'AFTER');
    if (before && !before.match(/^\s*-\s+[~%$^/]/m)) {
      const beforeSections = content.split('--- TREE (BEFORE) ---');
      for (let i = 1; i < beforeSections.length; i++) {
        const block = beforeSections[i]!.split(/\n---/)[0]?.trim() ?? '';
        if (block.match(/^\s*-\s+[~%$^/]/m)) { before = block; break; }
      }
    }
    if (after && !after.match(/^\s*-\s+[~%$^/]/m)) {
      const afterSections = content.split('--- EXPECTED TREE (AFTER) ---');
      for (let i = 1; i < afterSections.length; i++) {
        const block = afterSections[i]!.split(/\n---/)[0]?.trim() ?? '';
        if (block.match(/^\s*-\s+[~%$^/]/m)) { after = block; break; }
      }
    }
    if (before && before.match(/^\s*-\s+[~%$^/]/m)) {
      console.log('--- Parsed TREE (BEFORE) ---');
      const treeBefore = parseTreeBlock(before);
      console.log(JSON.stringify({ root: serializeNode(treeBefore.root), deps: treeBefore.deps }, null, 2));
    }
    if (after && after.match(/^\s*-\s+[~%$^/]/m) && after !== before) {
      console.log('\n--- Parsed TREE (AFTER) ---');
      const treeAfter = parseTreeBlock(after);
      console.log(JSON.stringify({ root: serializeNode(treeAfter.root), deps: treeAfter.deps }, null, 2));
    }
    if ((!before || !before.match(/^\s*-\s+[~%$^/]/m)) && (!after || !after.match(/^\s*-\s+[~%$^/]/m))) {
      const tree = parseTreeBlock(content);
      if (tree.root.children?.length) {
        console.log(JSON.stringify({ root: serializeNode(tree.root), deps: tree.deps }, null, 2));
      }
    }
  } else {
    const rl = createInterface({ input: process.stdin });
    const lines: string[] = [];
    for await (const line of rl) lines.push(line);
    content = lines.join('\n');
    const tree = parseTreeBlock(content);
    console.log(JSON.stringify({ root: serializeNode(tree.root), deps: tree.deps }, null, 2));
  }
}

function serializeNode(n: { id: string; feature: string; sigil: string; metadata: object; contract: object; status: string; children: unknown[] }): object {
  return {
    id: n.id,
    sigil: n.sigil,
    feature: n.feature,
    metadata: n.metadata,
    contract: n.contract,
    status: n.status,
    children: n.children.map((c: unknown) => serializeNode(c as typeof n)),
  };
}

main().catch(e => {
  console.error(e);
  process.exit(1);
});
