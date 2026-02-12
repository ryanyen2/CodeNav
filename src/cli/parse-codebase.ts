#!/usr/bin/env node
/**
 * Build codebase snapshot from a directory (e.g. test/requests) and output
 * the "codebase:" block format. Writes to test/fixtures/requests-codebase-snapshot.txt
 * when run with default path so you can inspect the parsed output.
 *
 * Usage:
 *   npx tsx src/cli/parse-codebase.ts [directory]
 *   npm run parse:codebase [-- directory]
 *
 * Default directory: test/requests
 */

import { writeFileSync } from 'fs';
import { join } from 'path';
import { buildCodebaseSnapshotFromDirectory, codebaseSnapshotToBlock } from '../parser/codebase-parser.js';

const rootDir = process.cwd();
const dir = process.argv[2] || join(rootDir, 'test', 'requests');

const snap = buildCodebaseSnapshotFromDirectory(dir, { extensions: ['.py'] });
const block = codebaseSnapshotToBlock(snap);

console.log(block);

const outPath = join(rootDir, 'test', 'fixtures', 'requests-codebase-snapshot.txt');
writeFileSync(outPath, block + '\n', 'utf-8');
console.error('\nWrote', outPath);
