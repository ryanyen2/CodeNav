#!/usr/bin/env npx tsx
/**
 * CLI: compute tree edit operations and target modification areas from base and edited tree markdown.
 * Usage:
 *   npx tsx src/cli/tree-edit-targets.ts <base.md> <edited.md>
 *   Or pipe: base and edited as first and second blocks (separated by ---)
 * Output: JSON to stdout { operations: [...], error?: string }
 */

import fs from 'fs';
import { computeTreeEditTargets } from '../sync/tree-edit-targets.js';

function main(): void {
  const args = process.argv.slice(2);
  if (args.length >= 2) {
    const baseMd = fs.readFileSync(args[0], 'utf-8');
    const editedMd = fs.readFileSync(args[1], 'utf-8');
    const result = computeTreeEditTargets(baseMd, editedMd);
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.error ? 1 : 0);
    return;
  }

  // Stdin: two blocks separated by ---
  let input = '';
  process.stdin.setEncoding('utf-8');
  process.stdin.on('data', (chunk) => { input += chunk; });
  process.stdin.on('end', () => {
    const parts = input.split(/\n---+\n/);
    const baseMd = (parts[0] ?? '').trim();
    const editedMd = (parts[1] ?? '').trim();
    if (!baseMd || !editedMd) {
      console.log(JSON.stringify({ operations: [], error: 'Need base and edited tree blocks (separated by ---)' }));
      process.exit(1);
    }
    const result = computeTreeEditTargets(baseMd, editedMd);
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.error ? 1 : 0);
  });
}

main();
