#!/usr/bin/env node
/**
 * Extract test case blocks from test_cases.md into test/fixtures/cases/<name>.md.
 * Run from repo root: node scripts/extract-test-cases.mjs
 */

import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const mdPath = join(root, 'test_cases.md');
const outDir = join(root, 'test', 'fixtures', 'cases');

const content = readFileSync(mdPath, 'utf-8');
const blocks = content.split(/^```/m);

mkdirSync(outDir, { recursive: true });

let count = 0;
for (const block of blocks) {
  const trimmed = block.trim();
  const match = trimmed.match(/^=== TEST:\s*(\S+)\s*===/);
  if (!match) continue;
  const name = match[1];
  if (name.startsWith('<')) continue;
  const outPath = join(outDir, `${name}.md`);
  writeFileSync(outPath, trimmed, 'utf-8');
  count++;
  console.log('Wrote', outPath);
}
console.log('Extracted', count, 'test cases.');
