#!/usr/bin/env node
/**
 * CLI: parse a test case file, run tree diff, and dispatch operation.
 * Usage: npx tsx src/cli/parse-test-case.ts <path-to-test_cases.md> [test-name]
 *   If test-name omitted, runs first test (add_patch_endpoint).
 */

import { readFileSync } from 'fs';
import {
  parseTreeBlock,
  extractTreeBlockFromTestCase,
  parseOperationBlock,
  extractOperationBlock,
  diffTrees,
  diffResultToOperation,
  dispatch,
  parseCodebaseBlock,
} from '../index.js';

function extractTestCases(content: string): Array<{ name: string; content: string }> {
  const tests: Array<{ name: string; content: string }> = [];
  const parts = content.split(/\n=== TEST:\s*/).filter(Boolean);
  for (const part of parts) {
    const firstLine = part.indexOf('\n');
    const nameLine = firstLine === -1 ? part : part.slice(0, firstLine);
    const name = nameLine.replace(/=+\s*$/, '').trim();
    const body = firstLine === -1 ? '' : part.slice(firstLine + 1);
    if (name) tests.push({ name, content: body });
  }
  if (tests.length === 0 && content.includes('--- TREE (BEFORE) ---')) {
    tests.push({ name: 'inline', content });
  }
  return tests;
}

function main() {
  const path = process.argv[2] || 'test_cases.md';
  const testName = process.argv[3];
  const content = readFileSync(path, 'utf-8');
  const tests = extractTestCases(content);
  const target = testName ? tests.find(t => t.name === testName) : tests[0];
  if (!target) {
    console.error('Test not found. Available:', tests.map(t => t.name).join(', '));
    process.exit(1);
  }

  const beforeBlock = target.content.includes('--- TREE (BEFORE) ---')
    ? extractTreeBlockFromTestCase(target.content, 'BEFORE')
    : '';
  const afterBlock = target.content.includes('--- EXPECTED TREE (AFTER) ---')
    ? extractTreeBlockFromTestCase(target.content, 'AFTER')
    : '';
  const opBlock = extractOperationBlock(target.content);
  const codebaseStart = target.content.indexOf('--- CODEBASE (BEFORE) ---');
  const codebaseEnd = target.content.indexOf('--- TREE (BEFORE) ---');
  const codebaseBlock =
    codebaseStart >= 0 && codebaseEnd > codebaseStart
      ? target.content.slice(codebaseStart, codebaseEnd)
      : '';

  console.log('Test:', target.name);
  console.log('');

  const treeBefore = beforeBlock ? parseTreeBlock(beforeBlock) : null;
  const treeAfter = afterBlock ? parseTreeBlock(afterBlock) : null;
  const operation = opBlock ? parseOperationBlock(opBlock) : null;
  const codebase = codebaseBlock && codebaseBlock.includes('codebase:') ? parseCodebaseBlock(codebaseBlock) : null;

  if (treeBefore) {
    console.log('Tree (BEFORE): root feature =', treeBefore.root.feature, '| nodes =', countNodes(treeBefore.root), '| deps =', treeBefore.deps.length);
  }
  if (treeAfter) {
    console.log('Tree (AFTER):  root feature =', treeAfter.root.feature, '| nodes =', countNodes(treeAfter.root), '| deps =', treeAfter.deps.length);
  }
  if (operation) {
    console.log('Parsed operation:', operation.op, '| target:', operation.target);
  }
  if (codebase?.root.children?.length) {
    console.log('Codebase: root children =', codebase.root.children.length);
  }
  console.log('');

  if (treeBefore && treeAfter) {
    const diffs = diffTrees(treeBefore, treeAfter);
    console.log('Diff inferred', diffs.length, 'operation(s):');
    for (const d of diffs) {
      console.log(' -', d.operation, d.details);
      const op = diffResultToOperation(d, treeBefore, treeAfter);
      if (op) {
        const result = dispatch(op, treeAfter, codebase ?? undefined);
        console.log('   ->', result.kind, result);
      }
    }
    console.log('');
  }

  if (operation && treeAfter) {
    const result = dispatch(operation, treeAfter, codebase ?? undefined);
    console.log('Dispatch parsed operation ->', result.kind);
    if ('plan' in result) console.log('Plan:', result.plan);
  }
}

function countNodes(n: { children: unknown[] }): number {
  return 1 + n.children.reduce((s, c) => s + countNodes(c as { children: unknown[] }), 0);
}

main();
