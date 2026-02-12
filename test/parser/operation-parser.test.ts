/**
 * Tests for operation parser: all operation types, params, extract block,
 * and incremental/malformed input. Fixtures from test/fixtures/cases/.
 */

import * as t from 'tape';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  extractOperationBlock,
  parseOperationBlock,
} from '../../src/parser/operation-parser.js';

const FIXTURES_DIR = join(process.cwd(), 'test', 'fixtures', 'cases');

t.test('operation-parser: extractOperationBlock', (t) => {
  const content = `
--- DESCRIPTION ---
foo

--- OPERATION ---
op: AddNode
target: API/request handling
params:
  feature: "send PATCH request"

--- EXPECTED TREE (AFTER) ---
`;
  const block = extractOperationBlock(content);
  t.ok(block.includes('op: AddNode'));
  t.ok(block.includes('target: API/request handling'));
  t.ok(block.includes('feature: "send PATCH request"'));
  t.notOk(block.includes('--- EXPECTED TREE'));
  t.end();
});

t.test('operation-parser: parse from fixture add_patch_endpoint', (t) => {
  const content = readFileSync(join(FIXTURES_DIR, 'add_patch_endpoint.md'), 'utf-8');
  const block = extractOperationBlock(content);
  t.ok(block.length > 0, 'operation block extracted');
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'AddNode');
  t.equal(op!.target, 'API/request handling/core request module');
  t.equal(op!.params.feature, 'send PATCH request');
  t.end();
});

t.test('operation-parser: AddNode', (t) => {
  const block = `
op: AddNode
target: API/request handling/core request module
params:
  feature: "send PATCH request"
  contract:
    sig: (url, data=None, **kwargs) -> Response
    inv: delegates to request()
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'AddNode');
  t.equal(op!.target, 'API/request handling/core request module');
  t.equal(op!.params.feature, 'send PATCH request');
  t.equal((op!.params as { contract?: { sig?: string } }).contract?.sig, '(url, data=None, **kwargs) -> Response');
  t.end();
});

t.test('operation-parser: DeleteNode with strategy', (t) => {
  const block = `
op: DeleteNode
target: Utilities/string helpers/slugify text
params:
  strategy: redirect
  redirect_to: "python-slugify::slugify"
  redirect_import: "from slugify import slugify"
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'DeleteNode');
  t.equal(op!.target, 'Utilities/string helpers/slugify text');
  t.equal((op!.params as { strategy?: string }).strategy, 'redirect');
  t.equal((op!.params as { redirect_to?: string }).redirect_to, 'python-slugify::slugify');
  t.end();
});

t.test('operation-parser: MoveNode', (t) => {
  const block = `
op: MoveNode
target: Utilities/input validation/check email format
params:
  new_parent: Authentication/auth module
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'MoveNode');
  t.equal((op!.params as { new_parent?: string }).new_parent, 'Authentication/auth module');
  t.end();
});

t.test('operation-parser: EditFeature', (t) => {
  const block = `
op: EditFeature
target: DataProcessing/validation/check email format
params:
  new_feature: "validate email format and check domain MX record"
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'EditFeature');
  t.equal((op!.params as { new_feature?: string }).new_feature, 'validate email format and check domain MX record');
  t.end();
});

t.test('operation-parser: EditContract', (t) => {
  const block = `
op: EditContract
target: API/request handling/core request dispatcher
params:
  new_contract: {sig: (method, url, timeout=30, **kwargs) -> Response}
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'EditContract');
  const c = (op!.params as { new_contract?: { sig?: string } }).new_contract;
  t.ok(c?.sig?.includes('timeout=30'));
  t.end();
});

t.test('operation-parser: ReorderChildren', (t) => {
  const block = `
op: ReorderChildren
target: API/request handling/core request module
params:
  permutation: [2, 0, 1]
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'ReorderChildren');
  const perm = (op!.params as { permutation?: number[] | string }).permutation;
  t.ok(perm !== undefined, 'permutation present');
  if (Array.isArray(perm)) t.deepEqual(perm, [2, 0, 1]);
  else t.ok(String(perm).includes('2'), 'permutation value contains indices');
  t.end();
});

t.test('operation-parser: ExtractAndGroup', (t) => {
  const block = `
op: ExtractAndGroup
target:
  - Security/authentication/validate JWT token
  - Security/authentication/refresh OAuth token
params:
  group_feature: "manage token-based authentication flows"
  group_name: "token-based auth"
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'ExtractAndGroup');
  const targets = op!.target as string[];
  t.equal(targets.length, 2);
  t.equal(targets[0], 'Security/authentication/validate JWT token');
  t.equal((op!.params as { group_feature?: string }).group_feature, 'manage token-based authentication flows');
  t.equal((op!.params as { group_name?: string }).group_name, 'token-based auth');
  t.end();
});

t.test('operation-parser: SplitFunction with specs', (t) => {
  const block = `
op: SplitFunction
target: Utilities/data helpers/parse and validate config
params:
  specs:
    - feature: "parse config from YAML file"
      contract:
        sig: (path: str) -> dict
    - feature: "validate config against schema"
      contract:
        sig: (config: dict, schema: dict) -> bool
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'SplitFunction');
  const specs = (op!.params as { specs?: Array<{ feature: string; contract: { sig?: string } }> }).specs;
  t.equal(specs?.length, 2);
  t.equal(specs![0].feature, 'parse config from YAML file');
  t.equal(specs![0].contract.sig, '(path: str) -> dict');
  t.equal(specs![1].feature, 'validate config against schema');
  t.end();
});

t.test('operation-parser: MergeNodes', (t) => {
  const block = `
op: MergeNodes
target:
  - API/error handling/handle HTTP errors
  - API/error handling/handle connection errors
params:
  merged_feature: "handle all request errors"
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'MergeNodes');
  const targets = op!.target as string[];
  t.equal(targets.length, 2);
  t.equal((op!.params as { merged_feature?: string }).merged_feature, 'handle all request errors');
  t.end();
});

t.test('operation-parser: missing op returns null', (t) => {
  const block = `
target: some/path
params:
  feature: "x"
`;
  const op = parseOperationBlock(block);
  t.equal(op, null);
  t.end();
});

t.test('operation-parser: unknown op returns null', (t) => {
  const block = `
op: UnknownOp
target: x
`;
  const op = parseOperationBlock(block);
  t.equal(op, null);
  t.end();
});

t.test('operation-parser: empty block returns null', (t) => {
  t.equal(parseOperationBlock(''), null);
  t.end();
});

t.test('operation-parser: extractOperationBlock missing marker returns empty', (t) => {
  const content = '--- DESCRIPTION ---\nno operation section';
  t.equal(extractOperationBlock(content), '');
  t.end();
});

t.test('operation-parser: incremental - op line only', (t) => {
  const block = `op: AddNode`;
  const op = parseOperationBlock(block);
  t.ok(op, 'partial block still parses');
  t.equal(op!.op, 'AddNode');
  t.equal(op!.target, '');
  t.end();
});

t.test('operation-parser: target only no params', (t) => {
  const block = `
op: DeleteNode
target: Utilities/slugify
`;
  const op = parseOperationBlock(block);
  t.ok(op);
  t.equal(op!.op, 'DeleteNode');
  t.equal(op!.target, 'Utilities/slugify');
  t.end();
});
