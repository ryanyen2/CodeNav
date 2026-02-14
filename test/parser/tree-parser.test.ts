/**
 * Tests for tree parser: node classification, sigils, metadata, contracts,
 * deps, full block parse, and incremental/invalid input (char-by-char editing).
 * Fixtures are read from test/fixtures/cases/ (extracted from test_cases.md).
 */

import * as t from 'tape';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  parseTreeBlock,
  extractTreeBlockFromTestCase,
  findNodeByPath,
} from '../../src/parser/tree-parser.js';
import type { SemanticNode, DepEdge } from '../../src/types.js';

const FIXTURES_DIR = join(process.cwd(), 'test', 'fixtures', 'cases');

function nodeCount(root: SemanticNode): number {
  return 1 + root.children.reduce((s, c) => s + nodeCount(c), 0);
}

function collectFeatures(root: SemanticNode): string[] {
  const out: string[] = [];
  function walk(n: SemanticNode) {
    if (n.feature) out.push(n.feature);
    n.children.forEach(walk);
  }
  walk(root);
  return out;
}

t.test('tree-parser: valid sigils and artifact class', (t) => {
  const block = `
- / root dir [src/] #resolved
  - % a file [src/api.py] #resolved
    - $ a function [src/api.py] (get) {sig: (url) -> Response} #resolved
  - ^ a class [src/auth.py] (HTTPBasicAuth) {cls: methods=[__call__]} #resolved
  - ~ abstract group
`;
  const tree = parseTreeBlock(block);
  t.ok(tree.root, 'has root');
  t.equal(tree.root.sigil, '/', 'root sigil /');
  t.equal(tree.root.artifactClass, 'concrete-dir', 'root class concrete-dir');
  t.equal(tree.root.metadata.fpath, 'src/', 'root fpath');
  t.equal(tree.root.feature, 'root dir', 'root feature');

  const fileNode = tree.root.children[0]!;
  t.equal(fileNode.sigil, '%', 'file sigil %');
  t.equal(fileNode.artifactClass, 'concrete-file', 'file class concrete-file');
  t.equal(fileNode.metadata.fpath, 'src/api.py', 'file fpath');

  const funcNode = tree.root.children[0]!.children[0]!;
  t.equal(funcNode.sigil, '$', 'function sigil $');
  t.equal(funcNode.artifactClass, 'concrete-leaf', 'function class concrete-leaf');
  t.equal(funcNode.metadata.entity_name, 'get', 'entity_name get');
  t.equal(funcNode.metadata.type, 'function', 'type function');
  t.equal(funcNode.contract.sig, '(url) -> Response', 'contract sig');

  const classNode = tree.root.children[1]!;
  t.equal(classNode.sigil, '^', 'class sigil ^');
  t.equal(classNode.artifactClass, 'concrete-leaf', 'class class concrete-leaf');
  t.equal(classNode.metadata.entity_name, 'HTTPBasicAuth', 'class entity');

  const abstractNode = tree.root.children[2]!;
  t.equal(abstractNode.sigil, '~', 'abstract sigil ~');
  t.equal(abstractNode.artifactClass, 'abstract', 'abstract class');
  t.end();
});

t.test('tree-parser: all five sigils and statuses', (t) => {
  const block = `
- / dir [pkg/] #resolved
  - % file [pkg/a.py] #draft
    - $ fn [pkg/a.py] (foo) #unresolved
    - ^ cls [pkg/a.py] (Bar) #planned
  - ~ group #resolved
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.root.status, 'resolved');
  const file = tree.root.children[0]!;
  t.equal(file.status, 'draft');
  const fn = file.children[0]!;
  t.equal(fn.status, 'unresolved');
  const cls = file.children[1]!;
  t.equal(cls.status, 'planned');
  const group = tree.root.children[1]!;
  t.equal(group.status, 'resolved');
  t.end();
});

t.test('tree-parser: grounding [path] and entity (entity_name)', (t) => {
  const block = `
- % mod [src/utils/strings.py] #resolved
  - $ slug [src/utils/strings.py] (slugify) {sig: (text: str) -> str} #resolved
`;
  const tree = parseTreeBlock(block);
  const mod = tree.root;
  t.equal(mod.metadata.fpath, 'src/utils/strings.py');
  t.equal(mod.metadata.type, 'file');
  const leaf = mod.children[0]!;
  t.equal(leaf.metadata.fpath, 'src/utils/strings.py');
  t.equal(leaf.metadata.entity_name, 'slugify');
  t.equal(leaf.metadata.type, 'function');
  t.end();
});

t.test('tree-parser: directory path ends with /', (t) => {
  const block = `
- / auth [src/auth/] #resolved
  - % login [src/auth/login.py] #resolved
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.root.metadata.fpath, 'src/auth/');
  t.equal(tree.root.metadata.type, 'directory');
  t.end();
});

t.test('tree-parser: multiple contract keys', (t) => {
  const block = `
- $ node [f.py] (fn) {sig: (x: int) -> bool} {inv: x >= 0} {cls: N/A} {exp: fn} #resolved
`;
  const tree = parseTreeBlock(block);
  const n = tree.root;
  t.equal(n.contract.sig, '(x: int) -> bool');
  t.equal(n.contract.inv, 'x >= 0');
  t.equal(n.contract.cls, 'N/A');
  t.equal(n.contract.exp, 'fn');
  t.end();
});

t.test('tree-parser: dependency edges', (t) => {
  const block = `
- ~ API
  - % core [api.py] #resolved
    - $ get [api.py] (get) #resolved
    - $ request [api.py] (request) #resolved
deps:
  (get) --invokes--> (request)
  (post) --invokes--> (request)
  (api) --imports--> (Session)
  (slugify) --inherits--> (ext:Base)
  (T) --type-refs--> (ext:Json)
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.deps.length, 5);
  const invokes = tree.deps.filter((d: DepEdge) => d.relation === 'invokes');
  t.equal(invokes.length, 2);
  const imports = tree.deps.filter((d: DepEdge) => d.relation === 'imports');
  t.equal(imports.length, 1);
  const inherits = tree.deps.filter((d: DepEdge) => d.relation === 'inherits');
  t.equal(inherits.length, 1);
  const typeRefs = tree.deps.filter((d: DepEdge) => d.relation === 'type-refs');
  t.equal(typeRefs.length, 1);
  const extEdge = tree.deps.find((d: DepEdge) => d.to.startsWith('ext:'));
  t.ok(extEdge, 'has external dep');
  t.equal(extEdge!.toExternal, true);
  t.end();
});

t.test('tree-parser: hierarchy depth', (t) => {
  const block = `
- ~ L0
  - ~ L1
    - ~ L2
      - $ leaf [x.py] (f) #resolved
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.root.feature, 'L0');
  t.equal(tree.root.children[0]!.feature, 'L1');
  t.equal(tree.root.children[0]!.children[0]!.feature, 'L2');
  t.equal(tree.root.children[0]!.children[0]!.children[0]!.feature, 'leaf');
  t.end();
});

t.test('tree-parser: node ids use fpath and entity_name when present', (t) => {
  const block = `
- % f [a.py] #resolved
  - $ foo [a.py] (foo) #resolved
`;
  const tree = parseTreeBlock(block);
  const leaf = tree.root.children[0]!;
  t.ok(leaf.id.includes('a.py') && leaf.id.includes('foo'), 'id has fpath and entity');
  t.end();
});

t.test('tree-parser: findNodeByPath', (t) => {
  const block = `
- ~ API
  - ~ request handling
    - % core [api.py] #resolved
      - $ get [api.py] (get) #resolved
`;
  const tree = parseTreeBlock(block);
  const core = findNodeByPath(tree.root, 'request handling/core');
  t.ok(core, 'found core');
  t.equal(core!.feature, 'core');
  const get = findNodeByPath(tree.root, 'request handling/core/get');
  t.ok(get, 'found get');
  t.equal(get!.metadata.entity_name, 'get');
  const missing = findNodeByPath(tree.root, 'request handling/nonexistent');
  t.notOk(missing, 'missing returns null');
  t.end();
});

t.test('tree-parser: extractTreeBlockFromTestCase from fixture', (t) => {
  const content = readFileSync(join(FIXTURES_DIR, 'add_patch_endpoint.md'), 'utf-8');
  const before = extractTreeBlockFromTestCase(content, 'BEFORE');
  const after = extractTreeBlockFromTestCase(content, 'AFTER');
  t.ok(before.includes('- ~ API'), 'before has tree');
  t.ok(after.includes('send PATCH request'), 'after has new node');
  const treeBefore = parseTreeBlock(before);
  const treeAfter = parseTreeBlock(after);
  t.ok(nodeCount(treeBefore.root) >= 2, 'before: at least 2 nodes');
  t.ok(nodeCount(treeAfter.root) >= 3, 'after: at least 3 nodes');
  t.end();
});

t.test('tree-parser: odd indent rejected', (t) => {
  const block = `
- ~ OK
   - $ bad indent
`;
  const tree = parseTreeBlock(block);
  t.equal(nodeCount(tree.root), 1, 'only OK node parsed');
  t.end();
});

t.test('tree-parser: line without sigil ignored', (t) => {
  const block = `
- ~ A
  - X no sigil here
  - $ valid [f.py] (v) #resolved
`;
  const tree = parseTreeBlock(block);
  const features = collectFeatures(tree.root);
  t.ok(features.includes('valid'), 'valid node parsed');
  t.ok(!features.includes('no sigil'), 'invalid line not parsed as node');
  t.end();
});

t.test('tree-parser: incomplete line (no sigil yet) ignored', (t) => {
  const block = `
- ~ A
  - 
  - $ done [f.py] (done) #resolved
`;
  const tree = parseTreeBlock(block);
  const features = collectFeatures(tree.root);
  t.ok(features.includes('done'), 'valid node parsed despite incomplete line');
  t.end();
});

t.test('tree-parser: half-written sigil character ignored', (t) => {
  const block = `
- ~ A
  - ? half sigil
  - $ ok [f.py] (ok) #resolved
`;
  const tree = parseTreeBlock(block);
  const features = collectFeatures(tree.root);
  t.ok(features.includes('ok'), 'valid node parsed despite bad sigil');
  t.end();
});

t.test('tree-parser: empty block returns minimal tree', (t) => {
  const tree = parseTreeBlock('');
  t.ok(tree.root);
  t.equal(tree.root.id, '__empty');
  t.equal(tree.root.children.length, 0);
  t.equal(tree.deps.length, 0);
  t.end();
});

t.test('tree-parser: only blank lines and non-tree lines', (t) => {
  const block = `
some text
deps:
  (a) --invokes--> (b)
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.root.children.length, 0);
  t.equal(tree.deps.length, 1, 'deps still parsed');
  t.end();
});

t.test('tree-parser: unclosed [ path does not consume rest', (t) => {
  const block = `
- % file [unclosed
  - $ fn [f.py] (fn) #resolved
`;
  const tree = parseTreeBlock(block);
  const features = collectFeatures(tree.root);
  t.ok(features.includes('fn'), 'fn node parsed');
  t.ok(features.some(f => f.includes('file')), 'file node present');
  t.end();
});

t.test('tree-parser: single root without children', (t) => {
  const block = `- ~ Only`;
  const tree = parseTreeBlock(block);
  t.equal(tree.root.feature, 'Only');
  t.equal(tree.root.sigil, '~');
  t.equal(tree.root.children.length, 0);
  t.end();
});

t.test('tree-parser: dep line with comment', (t) => {
  const block = `
- $ a [x.py] (a) #resolved
deps:
  (a) --invokes--> (b)   # comment here
`;
  const tree = parseTreeBlock(block);
  t.equal(tree.deps.length, 1);
  t.equal(tree.deps[0]!.from, 'a');
  t.equal(tree.deps[0]!.to, 'b');
  t.end();
});

t.test('tree-parser: feature with brackets in text', (t) => {
  const block = `
- $ optional [f.py] (fn) {sig: (x: Optional[int]) -> None} #resolved
`;
  const tree = parseTreeBlock(block);
  const n = tree.root;
  t.ok(n.contract?.sig?.includes('Optional') ?? false, 'contract sig contains Optional');
  t.end();
});

t.test('tree-parser: backend tree_serializer format (API output)', (t) => {
  // Format produced by server api/semantic_tree/output/tree_serializer.tree_to_markdown()
  const block = `- ~   #resolved
  - ~ requests #resolved
    - ~ utils #resolved
      - % adapters [requests/adapters.py] #resolved
        - $ get [requests/adapters.py] (get) {sig: (url) -> Response} #resolved
deps:
  (get) --invokes--> (request)
`;
  const tree = parseTreeBlock(block);
  t.ok(tree.root, 'has root');
  t.equal(tree.root.sigil, '~', 'root sigil');
  t.ok(tree.root.children.length >= 1, 'has children');
  const area = tree.root.children[0]!;
  t.equal(area.feature, 'requests', 'area feature');
  const fileNode = area.children[0]?.children[0];
  t.ok(fileNode, 'has file node');
  t.equal(fileNode!.metadata.fpath, 'requests/adapters.py', 'file fpath');
  const leaf = fileNode!.children[0];
  t.ok(leaf, 'has leaf');
  t.equal(leaf!.metadata.entity_name, 'get', 'entity_name');
  t.equal(tree.deps.length, 1, 'one dep');
  t.equal(tree.deps[0]!.relation, 'invokes', 'dep relation');
  t.end();
});
