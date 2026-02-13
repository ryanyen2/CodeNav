#!/usr/bin/env node
/**
 * Build codebase snapshot from a directory and output the "codebase:" block format.
 * Writes to test/fixtures/{name}-codebase-snapshot.txt so you can inspect the parsed output.
 *
 * Usage:
 *   npx tsx src/cli/parse-codebase.ts [directory]
 *   npm run parse:codebase [-- directory]
 *
 * Examples:
 *   npm run parse:codebase                    # test/requests -> requests-codebase-snapshot.txt
 *   npm run parse:codebase -- test/mosaic    # test/mosaic -> mosaic-codebase-snapshot.txt
 *   npm run parse:codebase -- test/draco     # test/draco -> draco-codebase-snapshot.txt
 *
 * Default directory: test/requests
 * Parses .py, .js, .ts, .jsx, .tsx (parser default).
 */

import { writeFileSync } from 'fs';
import { join } from 'path';
import { buildCodebaseSnapshotFromDirectory, codebaseSnapshotToBlock } from '../parser/codebase-parser.js';

const rootDir = process.cwd();
const dirArg = process.argv[2] || join(rootDir, 'test', 'requests');
const dir = dirArg.startsWith('/') ? dirArg : join(rootDir, dirArg);

// Derive snapshot name from directory path (e.g. test/mosaic -> mosaic, test/draco -> draco)
const name = dir.replace(/\/+$/, '').split(/[/\\]/).pop() || 'codebase';
const snap = buildCodebaseSnapshotFromDirectory(dir);
const block = codebaseSnapshotToBlock(snap);

console.log(block);

const outPath = join(rootDir, 'test', 'fixtures', `${name}-codebase-snapshot.txt`);
writeFileSync(outPath, block + '\n', 'utf-8');
console.error('\nWrote', outPath);
