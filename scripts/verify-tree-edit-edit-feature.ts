#!/usr/bin/env npx tsx
/**
 * Verify that a single feature text change produces exactly one EditFeature op
 * (parser extracts (entity) from end of feature so stableId = fpath::entity).
 * Run from repo root: npx tsx scripts/verify-tree-edit-edit-feature.ts
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { computeTreeEditTargets } from '../src/sync/tree-edit-targets.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const basePath = path.join(repoRoot, 'test/fixtures/cases/edit_one_feature_base.md');
const editedPath = path.join(repoRoot, 'test/fixtures/cases/edit_one_feature_edited.md');

const baseMd = fs.readFileSync(basePath, 'utf-8');
const editedMd = fs.readFileSync(editedPath, 'utf-8');
const result = computeTreeEditTargets(baseMd, editedMd);

const editFeatureOps = result.operations.filter((o) => o.op === 'EditFeature');
const otherOps = result.operations.filter((o) => o.op !== 'EditFeature');

if (result.error) {
  console.error('Error:', result.error);
  process.exit(1);
}
if (otherOps.length > 0) {
  console.error('Expected only EditFeature; got:', result.operations.map((o) => o.op));
  process.exit(1);
}
if (editFeatureOps.length !== 1) {
  console.error('Expected exactly one EditFeature; got', editFeatureOps.length);
  process.exit(1);
}

console.log('OK: one feature edit → one EditFeature op');
process.exit(0);
