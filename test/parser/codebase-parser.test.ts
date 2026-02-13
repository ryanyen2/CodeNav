/**
 * Tests for codebase parser: parseCodebaseBlock (markdown), discoverCodebase (fs),
 * buildCodebaseSnapshotFromSource, buildCodebaseSnapshotFromDirectory (e.g. test/requests),
 * and incremental/invalid input. Fixtures from test/fixtures/cases/.
 */

import * as t from 'tape';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  parseCodebaseBlock,
  discoverCodebase,
  buildCodebaseSnapshotFromSource,
  buildCodebaseSnapshotFromDirectory,
  codebaseSnapshotToBlock,
} from '../../src/parser/codebase-parser.js';

const FIXTURES_DIR = join(process.cwd(), 'test', 'fixtures', 'cases');
const REQUESTS_DIR = join(process.cwd(), 'test', 'requests');
const MOSAIC_DIR = join(process.cwd(), 'test', 'mosaic');
const DRACO_DIR = join(process.cwd(), 'test', 'draco');

function countEntries(e: { children?: unknown[] }): number {
  if (!e.children?.length) return 1;
  return 1 + e.children.reduce((s, c) => s + countEntries(c as { children?: unknown[] }), 0);
}

t.test('codebase-parser: parseCodebaseBlock from fixture add_patch_endpoint', (t) => {
  const content = readFileSync(join(FIXTURES_DIR, 'add_patch_endpoint.md'), 'utf-8');
  const codebaseStart = content.indexOf('--- CODEBASE (BEFORE) ---');
  const codebaseEnd = content.indexOf('--- TREE (BEFORE) ---');
  const codebaseBlock = codebaseStart >= 0 && codebaseEnd > codebaseStart
    ? content.slice(codebaseStart, codebaseEnd)
    : '';
  const snap = parseCodebaseBlock(codebaseBlock);
  t.ok(snap);
  const requests = snap!.root.children!.find(c => c.path === 'requests')!;
  t.ok(requests);
  const api = requests.children!.find(c => c.path === 'requests/api.py')!;
  t.ok(api);
  t.ok(api.lines!.some(l => l.includes('request(')));
  t.ok(api.lines!.some(l => l.includes('get(')));
  t.ok(api.lines!.some(l => l.includes('post(')));
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock basic', (t) => {
  const text = `
codebase:
  src/
    api.py
      | def get(url, **kwargs): return request("GET", url, **kwargs)
      | def post(url, data=None, **kwargs): return request("POST", url, data=data, **kwargs)
      | def request(method, url, **kwargs): ...
    auth.py
      | class HTTPBasicAuth(AuthBase):
      |     def __call__(self, r): ...
`;
  const snap = parseCodebaseBlock(text);
  t.ok(snap);
  t.equal(snap!.root.kind, 'directory');
  t.equal(snap!.root.children!.length, 1);
  const src = snap!.root.children![0]!;
  t.equal(src.path, 'src');
  t.equal(src.kind, 'directory');
  t.equal(src.children!.length, 2);
  const api = src.children!.find(c => c.path === 'src/api.py')!;
  t.equal(api.kind, 'file');
  t.equal(api.lines!.length, 3);
  t.equal(api.lines![0], 'def get(url, **kwargs): return request("GET", url, **kwargs)');
  const auth = src.children!.find(c => c.path === 'src/auth.py')!;
  t.equal(auth.lines!.length, 2);
  t.ok(auth.lines![0].includes('HTTPBasicAuth'));
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock nested dirs', (t) => {
  const text = `
codebase:
  requests/
    __init__.py
      | from .api import get, post
    api.py
      | def request(method, url, **kwargs): ...
    utils/
      strings.py
        | def slugify(text): ...
`;
  const snap = parseCodebaseBlock(text);
  t.ok(snap);
  const requests = snap!.root.children![0]!;
  t.equal(requests.children!.length, 3);
  const utils = requests.children!.find(c => c.path === 'requests/utils')!;
  t.equal(utils.kind, 'directory');
  const strings = utils.children![0]!;
  t.equal(strings.path, 'requests/utils/strings.py');
  t.equal(strings.lines!.length, 1);
  t.equal(strings.lines![0], 'def slugify(text): ...');
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock stops at ---', (t) => {
  const text = `
codebase:
  src/
    a.py
      | def f(): pass
--- TREE (BEFORE) ---
`;
  const snap = parseCodebaseBlock(text);
  t.ok(snap);
  t.equal(snap!.root.children!.length, 1);
  const a = snap!.root.children![0]!.children![0]!;
  t.equal(a.lines!.length, 1);
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock no codebase: returns null', (t) => {
  t.equal(parseCodebaseBlock('  src/\n    a.py'), null);
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock empty after codebase:', (t) => {
  const snap = parseCodebaseBlock('codebase:\n');
  t.ok(snap);
  t.equal(snap!.root.children!.length, 0);
  t.end();
});

t.test('codebase-parser: parseCodebaseBlock tabs normalized to spaces', (t) => {
  const text = 'codebase:\n\tsrc/\n\t\ta.py\n\t\t\t| def f(): ...';
  const snap = parseCodebaseBlock(text);
  t.ok(snap);
  const a = snap!.root.children![0]!.children![0]!;
  t.equal(a.lines!.length, 1);
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromSource Python', (t) => {
  const files = [
    { path: 'src/api.py', content: 'def get(url):\n  return request("GET", url)\ndef request(method, url):\n  ...' },
    { path: 'src/auth.py', content: 'class HTTPBasicAuth:\n  def __call__(self, r): ...' },
  ];
  const snap = buildCodebaseSnapshotFromSource(files);
  t.equal(snap.root.kind, 'directory');
  const src = snap.root.children!.find(c => c.path === 'src')!;
  t.ok(src);
  const api = src.children!.find(c => c.path === 'src/api.py')!;
  t.ok(api);
  t.equal(api.lines!.length, 2);
  t.ok(api.lines!.some(l => l.startsWith('def get(')));
  t.ok(api.lines!.some(l => l.startsWith('def request(')));
  const auth = src.children!.find(c => c.path === 'src/auth.py')!;
  t.ok(auth.lines!.some(l => l.startsWith('class HTTPBasicAuth')));
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromSource nested paths', (t) => {
  const files = [
    { path: 'pkg/sub/mod.py', content: 'def foo(): pass' },
  ];
  const snap = buildCodebaseSnapshotFromSource(files);
  const pkg = snap.root.children!.find(c => c.path === 'pkg')!;
  const sub = pkg.children!.find(c => c.path === 'pkg/sub')!;
  const mod = sub.children!.find(c => c.path === 'pkg/sub/mod.py')!;
  t.ok(mod);
  t.equal(mod.lines!.length, 1);
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromSource JS regex fallback', (t) => {
  const files = [
    { path: 'src/index.js', content: 'function main() {}\nclass App {}' },
  ];
  const snap = buildCodebaseSnapshotFromSource(files);
  const index = snap.root.children!.find(c => c.path === 'src')!.children!.find(c => c.path === 'src/index.js')!;
  t.ok(index);
  t.ok(index.lines!.length >= 1);
  t.ok(index.lines!.some(l => l.includes('main') || l.includes('App')));
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromDirectory parses test/requests', (t) => {
  const snap = buildCodebaseSnapshotFromDirectory(REQUESTS_DIR, { extensions: ['.py'] });
  t.ok(snap.root.kind === 'directory');
  t.ok(snap.root.children!.length >= 1);
  const api = snap.root.children!.find((c: { path: string }) => c.path === 'api.py');
  t.ok(api, 'has api.py (paths are relative to test/requests)');
  t.ok(api!.lines && api!.lines.length >= 1, 'api.py has declaration lines');
  const hasRequest = api!.lines!.some((l: string) => l.startsWith('def request('));
  const hasGet = api!.lines!.some((l: string) => l.startsWith('def get('));
  t.ok(hasRequest, 'api.py has request()');
  t.ok(hasGet, 'api.py has get()');
  t.end();
});

t.test('codebase-parser: discoverCodebase structure', (t) => {
  const rootDir = join(process.cwd(), 'test');
  const snap = discoverCodebase(rootDir);
  t.ok(snap.root.kind === 'directory');
  t.ok(Array.isArray(snap.root.children));
  const requests = snap.root.children?.find(c => c.path === 'requests' || c.path?.includes('requests'));
  t.ok(requests, 'has requests dir from test/');
  t.end();
});

t.test('codebase-parser: incremental - no codebase: line yet', (t) => {
  const text = 'cod';
  t.equal(parseCodebaseBlock(text), null);
  t.end();
});

t.test('codebase-parser: incremental - codebase: then empty', (t) => {
  const snap = parseCodebaseBlock('codebase:\n  ');
  t.ok(snap);
  t.equal(snap!.root.children!.length, 0);
  t.end();
});

t.test('codebase-parser: incremental - partial file line', (t) => {
  const text = `
codebase:
  src/
    a.py
      | def f
`;
  const snap = parseCodebaseBlock(text);
  t.ok(snap);
  const a = snap!.root.children![0]!.children![0]!;
  t.equal(a.lines!.length, 1);
  t.equal(a.lines![0], 'def f');
  t.end();
});

t.test('codebase-parser: dir with trailing slash', (t) => {
  const text = `
codebase:
  src/
    lib/
      a.py
        | def x(): pass
`;
  const snap = parseCodebaseBlock(text);
  const lib = snap!.root.children![0]!.children!.find(c => c.path === 'src/lib')!;
  t.ok(lib);
  t.equal(lib.kind, 'directory');
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromDirectory test/mosaic (TS) → block → parseCodebaseBlock', (t) => {
  const snap = buildCodebaseSnapshotFromDirectory(MOSAIC_DIR);
  t.ok(snap.root.kind === 'directory');
  t.ok(snap.root.children!.length >= 1);
  const block = codebaseSnapshotToBlock(snap);
  t.ok(block.startsWith('codebase:\n'), 'block starts with codebase:');
  const roundTrip = parseCodebaseBlock(block);
  t.ok(roundTrip, 'round-trip parse succeeds');
  const src = roundTrip!.root.children!.find((c: { path: string }) => c.path === 'src');
  t.ok(src, 'has src/');
  const coordinator = src?.children?.find((c: { path: string }) => c.path === 'src/Coordinator.ts');
  t.ok(coordinator?.lines?.length, 'Coordinator.ts has declaration lines');
  t.ok(coordinator?.lines?.some((l: string) => l.includes('Coordinator') || l.includes('coordinator')), 'has Coordinator/coordinator');
  t.end();
});

t.test('codebase-parser: buildCodebaseSnapshotFromDirectory test/draco (Python) → block → parseCodebaseBlock', (t) => {
  const snap = buildCodebaseSnapshotFromDirectory(DRACO_DIR);
  t.ok(snap.root.kind === 'directory');
  t.ok(snap.root.children!.length >= 1);
  const block = codebaseSnapshotToBlock(snap);
  t.ok(block.startsWith('codebase:\n'), 'block starts with codebase:');
  const roundTrip = parseCodebaseBlock(block);
  t.ok(roundTrip, 'round-trip parse succeeds');
  const runPy = roundTrip!.root.children!.find((c: { path: string }) => c.path === 'run.py');
  t.ok(runPy, 'has run.py');
  t.ok(runPy!.lines?.length, 'run.py has declaration lines');
  t.ok(runPy!.lines?.some((l: string) => l.startsWith('def run(')), 'run.py has def run(');
  t.ok(runPy!.lines?.some((l: string) => l.startsWith('class Result')), 'run.py has class Result');
  t.end();
});
