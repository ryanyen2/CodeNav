/**
 * Parser for codebase snapshot (test_cases §3) and codebase discovery.
 *
 * Two input modes:
 * 1. parseCodebaseBlock(text) — parse the markdown "codebase:" block from test cases.
 * 2. buildCodebaseSnapshotFromSource(files) — analyze real source files (path + content),
 *    use AST (Babel for JS/TS) or declaration regex (Python, etc.) to produce the same
 *    codebase snapshot syntax (file tree + "| def fn(...)" style lines).
 */

import type { FileEntry, CodebaseSnapshot } from '../types.js';
import { createRequire } from 'module';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const require = createRequire(import.meta.url);

// Lazy-load @babel/parser for deep TS/JS extraction with full signatures
let babelParse: ((code: string, options?: object) => { body?: unknown[]; program?: { body?: unknown[] } }) | null | undefined = undefined;
function getBabelParser(): typeof babelParse {
  if (babelParse !== undefined) return babelParse;
  try {
    const parser = require('@babel/parser');
    babelParse = parser.parse.bind(parser);
  } catch {
    babelParse = null;
  }
  return babelParse;
}

/** File path + content for AST-based snapshot building */
export interface SourceFile {
  path: string;
  content: string;
}

/** Parse codebase block from test case text. Format: "codebase:\n  src/\n    api.py\n      | def get..." */
export function parseCodebaseBlock(text: string): CodebaseSnapshot | null {
  const lines = text.split(/\r?\n/);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() === 'codebase:') {
      start = i + 1;
      break;
    }
  }
  if (start < 0) return null;

  const stack: { entry: FileEntry; indent: number }[] = [];
  let root: FileEntry | null = null;

  for (let i = start; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith('---') || line.trim().startsWith('===')) break;
    const content = line.replace(/\t/g, '  ');
    const indent = content.length - content.trimStart().length;
    const name = content.trim();
    if (!name) continue;

    const isCodeLine = name.startsWith('| ');
    const displayName = isCodeLine ? name.slice(2).trim() : name;

    if (isCodeLine) {
      const parent = stack.length ? stack[stack.length - 1].entry : null;
      if (parent && parent.kind === 'file') {
        if (!parent.lines) parent.lines = [];
        parent.lines.push(displayName);
      }
      continue;
    }

    const nameClean = displayName.replace(/\/$/, '');
    const isDir = name.endsWith('/') || (!name.includes('.') && nameClean !== '');
    const entry: FileEntry = {
      path: nameClean,
      kind: isDir ? 'directory' : 'file',
      children: isDir ? [] : undefined,
      lines: undefined,
    };

    while (stack.length > 0 && stack[stack.length - 1].indent >= indent) stack.pop();

    const parentPath = stack.length ? stack[stack.length - 1].entry.path : '';
    entry.path = parentPath ? `${parentPath}/${nameClean}` : nameClean;

    if (stack.length === 0) {
      if (!root) {
        root = { path: '', kind: 'directory', children: [entry] };
      } else {
        root.children!.push(entry);
      }
      stack.push({ entry, indent });
    } else {
      const parent = stack[stack.length - 1].entry;
      if (!parent.children) parent.children = [];
      parent.children.push(entry);
      stack.push({ entry, indent });
    }
  }

  if (!root) return { root: { path: '', kind: 'directory', children: [] } };
  return { root };
}

/** Build a minimal codebase snapshot from a directory (for grounding). */
export function discoverCodebase(rootDir: string, maxDepth = 6): CodebaseSnapshot {
  function walk(dir: string, depth: number): FileEntry[] {
    if (depth > maxDepth) return [];
    const entries: FileEntry[] = [];
    try {
      const names = readdirSync(dir);
      for (const name of names) {
        if (name.startsWith('.') && name !== '.') continue;
        const full = join(dir, name);
        let stat;
        try {
          stat = statSync(full);
        } catch {
          continue;
        }
        const rel = full.slice(rootDir.length).replace(/^\/+/, '');
        if (stat.isDirectory()) {
          entries.push({
            path: rel,
            kind: 'directory',
            children: walk(full, depth + 1),
          });
        } else {
          entries.push({ path: rel, kind: 'file' });
        }
      }
    } catch {
      // ignore
    }
    return entries;
  }
  const children = walk(rootDir, 0);
  return {
    root: { path: '', kind: 'directory', children },
  };
}

/**
 * Find the index of the closing ')' that matches the first '(' after start.
 */
function findMatchingParen(s: string, start: number): number {
  let depth = 0;
  for (let i = start; i < s.length; i++) {
    if (s[i] === '(') depth++;
    else if (s[i] === ')') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/**
 * Extract full signature from a def/class line (single or multi-line).
 * Returns normalized signature with full args and return type: "def name(args) -> ret:" or "class Name(bases):".
 */
function extractPythonSignature(lines: string[], startIndex: number): { sig: string; nextIndex: number } {
  let buf = lines[startIndex]!.trim();
  let i = startIndex;
  let parens = 0;
  for (const c of buf) {
    if (c === '(') parens++;
    else if (c === ')') parens--;
  }
  const completeNoParens = /^class\s+\w+\s*:?\s*$/.test(buf.trim()) || /^def\s+\w+\s*\(\s*\)\s*:?\s*$/.test(buf.trim());
  while (!completeNoParens && (parens !== 0 || (!buf.includes('):') && !/\)\s*->\s*[^:]+:/.test(buf)))) {
    i++;
    if (i >= lines.length) break;
    const next = lines[i]!;
    if (next.trim() === '') { i++; continue; }
    const nextTrim = next.trim();
    if (parens === 0 && (nextTrim.startsWith('"""') || nextTrim.startsWith("'''") || nextTrim.startsWith('#'))) break;
    buf += ' ' + nextTrim;
    for (const c of next) {
      if (c === '(') parens++;
      else if (c === ')') parens--;
    }
  }
  buf = buf.replace(/\s+/g, ' ').trim();

  if (buf.startsWith('def ')) {
    const open = buf.indexOf('(', 4);
    if (open === -1) return { sig: buf.split(':')[0] + ': ...', nextIndex: i };
    const close = findMatchingParen(buf, open);
    const name = buf.slice(4, open).trim();
    const args = close > open ? buf.slice(open + 1, close).trim() : '';
    const after = close >= 0 ? buf.slice(close + 1).trim() : '';
    const retMatch = after.match(/^\-\>\s*(.+?)\s*:?$/);
    const ret = retMatch ? retMatch[1].trim() : '';
    const sig = ret ? `def ${name}(${args}) -> ${ret}: ...` : `def ${name}(${args}): ...`;
    return { sig, nextIndex: i };
  }
  if (buf.startsWith('class ')) {
    const open = buf.indexOf('(', 6);
    if (open === -1) {
      const nameMatch = buf.match(/^class\s+(\w+)\s*:?/);
      const name = nameMatch ? nameMatch[1] : buf.slice(6).split(/[(:]/)[0].trim();
      return { sig: `class ${name}: ...`, nextIndex: i };
    }
    const close = findMatchingParen(buf, open);
    const name = buf.slice(6, open).trim();
    const bases = close > open ? buf.slice(open + 1, close).trim() : '';
    const sig = bases ? `class ${name}(${bases}): ...` : `class ${name}: ...`;
    return { sig, nextIndex: i };
  }
  return { sig: buf.split(':')[0] + ': ...', nextIndex: i };
}

/**
 * Extract all def/class declarations from Python source with full args, types, and nesting.
 * Returns lines with leading spaces for hierarchy (2 spaces per indent level).
 */
function extractPythonDeclarationsGranular(content: string): string[] {
  const lines = content.split(/\r?\n/);
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const indent = line.length - line.trimStart().length;
    const t = line.trim();
    if (t === '') { i++; continue; }
    const isDef = /^def\s+\w+\s*\(/.test(t) || (t.startsWith('def ') && t.includes('('));
    const isClass = /^class\s+\w+/.test(t);
    if (isDef || isClass) {
      const { sig, nextIndex } = extractPythonSignature(lines, i);
      const level = Math.floor(indent / 4);
      const prefix = '  '.repeat(level);
      out.push(prefix + sig);
      i = nextIndex + 1;
      continue;
    }
    i++;
  }
  return out;
}

/** Minimal AST node shape for signature extraction (Babel) */
type BabelNode = {
  type: string;
  start: number;
  end: number;
  body?: BabelNode & { start: number; end: number };
  id?: { name: string };
  declaration?: BabelNode;
  params?: BabelNode[];
  returnType?: { typeAnnotation: BabelNode & { start: number; end: number } };
  key?: BabelNode & { name?: string };
  kind?: string;
};

type ProgramNode = BabelNode & { body?: BabelNode[] };

/**
 * Extract full signature string from a function/method node (start up to body).
 * Includes params and return type from source. Normalized to one line for snapshot.
 */
function signatureSlice(source: string, node: BabelNode): string {
  const body = node.body as { start?: number } | undefined;
  const end = body?.start ?? node.end;
  const raw = source.slice(node.start, end).trim();
  return raw.replace(/\s+/g, ' ').trim();
}

/**
 * Deep extraction for TS/JS: recurse into classes (methods, nested classes) and
 * emit full signatures (params + types). Each line may have leading spaces for
 * nesting (2 spaces per level).
 */
function extractTSDeclarationsDeep(content: string, path: string): string[] {
  const parse = getBabelParser();
  if (!parse) return [];
  const ext = path.replace(/^.*\./, '').toLowerCase();
  const isTS = ['ts', 'tsx'].includes(ext);
  const plugins = isTS ? ['typescript', 'jsx'] as const : ['jsx'] as const;
  let ast: { body?: BabelNode[]; program?: ProgramNode };
  try {
    ast = parse(content, {
      sourceType: 'module',
      plugins: [...plugins],
      allowAwaitOutsideFunction: true,
    }) as { body?: BabelNode[]; program?: ProgramNode };
  } catch {
    return [];
  }
  const body = ast.body ?? ast.program?.body ?? [];
  const out: string[] = [];
  const indent = (level: number) => '  '.repeat(level);

  function addLine(level: number, sig: string): void {
    out.push(indent(level) + sig);
  }

  function walkClass(cls: BabelNode, level: number): void {
    const sig = signatureSlice(content, cls) + ': ...';
    addLine(level, sig);
    const classBodyNode = (cls as BabelNode & { body?: { body?: BabelNode[] } }).body;
    const classBody = classBodyNode?.body ?? [];
    for (const member of classBody) {
      const m = member as BabelNode;
      if (m.type === 'ClassMethod' || m.type === 'ClassPrivateMethod' || m.type === 'TSDeclareMethod') {
        const methodSig = signatureSlice(content, m);
        addLine(level + 1, methodSig);
      } else if (m.type === 'ClassDeclaration') {
        walkClass(m, level + 1);
      }
    }
  }

  function walkFunction(fn: BabelNode, level: number): void {
    const sig = signatureSlice(content, fn);
    addLine(level, sig);
  }

  function walkNode(node: BabelNode, level: number): void {
    if (node.type === 'FunctionDeclaration') {
      if (node.id) walkFunction(node, level);
      return;
    }
    if (node.type === 'ClassDeclaration') {
      walkClass(node, level);
      return;
    }
    if (node.type === 'ExportDefaultDeclaration' && node.declaration) {
      const d = node.declaration as BabelNode;
      if (d.type === 'FunctionDeclaration' && d.id) walkFunction(d, level);
      else if (d.type === 'ClassDeclaration') walkClass(d, level);
      return;
    }
    if (node.type === 'ExportNamedDeclaration' && node.declaration) {
      const d = node.declaration as BabelNode;
      if (d.type === 'FunctionDeclaration' && d.id) walkFunction(d, level);
      else if (d.type === 'ClassDeclaration') walkClass(d, level);
      else if (d.type === 'VariableDeclaration') walkNode(d, level);
      return;
    }
    if (node.type === 'VariableDeclaration') {
      const decl = node as BabelNode & { declarations?: { id: BabelNode; init?: BabelNode }[] };
      for (const declItem of decl.declarations ?? []) {
        const init = declItem.init as BabelNode | undefined;
        if (!init) continue;
        const name = (declItem.id as { name?: string })?.name;
        if (init.type === 'ArrowFunctionExpression' || init.type === 'FunctionExpression') {
          const arrow = init as BabelNode & { params: BabelNode[] };
          const arrowStart = init.start;
          const arrowIdx = content.indexOf('=>', arrowStart);
          const paramPart = arrowIdx >= 0 ? content.slice(arrowStart, arrowIdx).trim() : '()';
          const retPart = init.returnType?.typeAnnotation
            ? content.slice(init.returnType.typeAnnotation.start, init.returnType.typeAnnotation.end)
            : '...';
          addLine(level, `function ${name ?? 'anonymous'}${paramPart}: ${retPart}`);
        }
      }
      return;
    }
  }

  for (const node of body) {
    walkNode(node as BabelNode, 0);
  }
  return out;
}

/** Extract code sketch lines from source (e.g. "def foo(...):" or "class Bar:") for snapshot. */
function declarationLinesFromSource(path: string, content: string): string[] {
  const ext = path.replace(/^.*\./, '').toLowerCase();
  const lines = content.split(/\r?\n/);
  const out: string[] = [];

  if (ext === 'py') {
    return extractPythonDeclarationsGranular(content);
  }

  if (['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs'].includes(ext)) {
    const deep = extractTSDeclarationsDeep(content, path);
    if (deep.length > 0) return deep;
    // Fallback: no Babel or parse error
    for (const line of lines) {
      const t = line.trim();
      if (/^\s*\/\*\*?|\s*\/\//.test(t)) continue;
      const fnMatch = t.match(/^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(/);
      const classMatch = t.match(/^(?:export\s+)?class\s+(\w+)\s*[\{\(]/);
      const arrowStart = t.match(/^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(/);
      if (fnMatch) out.push(`function ${fnMatch[1]}(...): ...`);
      else if (classMatch) out.push(`class ${classMatch[1]}: ...`);
      else if (arrowStart) out.push(`function ${arrowStart[1]}(...): ...`);
    }
  }

  return out;
}

/**
 * Build codebase snapshot from real source files by analyzing structure and AST.
 * Uses path hierarchy for structure; for each file, extracts declaration lines
 * (Python: def/class via regex; JS/TS: function/class via optional Babel or regex).
 * Use this to produce the same "codebase snapshot syntax" as in test_cases §3.
 */
export function buildCodebaseSnapshotFromSource(files: SourceFile[]): CodebaseSnapshot {
  const root: FileEntry = { path: '', kind: 'directory', children: [] };
  const pathToEntry = new Map<string, FileEntry>();
  pathToEntry.set('', root);

  for (const { path: filePath, content } of files) {
    const parts = filePath.split('/').filter(Boolean);
    let currentPath = '';
    for (let i = 0; i < parts.length; i++) {
      const isLast = i === parts.length - 1;
      const name = parts[i]!;
      const nextPath = currentPath ? `${currentPath}/${name}` : name;
      if (pathToEntry.has(nextPath)) {
        currentPath = nextPath;
        continue;
      }
      const parent = pathToEntry.get(currentPath) ?? root;
      const isDir = !isLast || !name.includes('.');
      const entry: FileEntry = {
        path: nextPath,
        kind: isDir ? 'directory' : 'file',
        children: isDir ? [] : undefined,
        lines: undefined,
      };
      if (!parent.children) parent.children = [];
      parent.children.push(entry);
      pathToEntry.set(nextPath, entry);
      currentPath = nextPath;
      if (!isDir) {
        entry.lines = declarationLinesFromSource(filePath, content);
      }
    }
  }

  return { root };
}

/**
 * Build codebase snapshot from a directory on disk (e.g. test/requests).
 * Reads all files under rootDir, extracts declaration lines (Python def/class
 * at top level, etc.), and returns the same CodebaseSnapshot format.
 */
export function buildCodebaseSnapshotFromDirectory(
  rootDir: string,
  options?: { maxDepth?: number; extensions?: string[] }
): CodebaseSnapshot {
  const maxDepth = options?.maxDepth ?? 6;
  const extensions = options?.extensions ?? ['.py', '.js', '.ts', '.jsx', '.tsx'];
  const normalizedRoot = rootDir.replace(/\/+$/, '');
  const files: SourceFile[] = [];

  function walk(dir: string, depth: number): void {
    if (depth > maxDepth) return;
    try {
      const names = readdirSync(dir);
      for (const name of names) {
        if (name.startsWith('.') && name !== '.') continue;
        const full = join(dir, name);
        let stat;
        try {
          stat = statSync(full);
        } catch {
          continue;
        }
        const rel = full.slice(normalizedRoot.length).replace(/^\/+/, '');
        if (stat.isDirectory()) {
          walk(full, depth + 1);
        } else {
          const ext = name.includes('.') ? '.' + name.split('.').pop()!.toLowerCase() : '';
          if (extensions.includes(ext)) {
            try {
              const content = readFileSync(full, 'utf-8');
              files.push({ path: rel, content });
            } catch {
              // skip unreadable
            }
          }
        }
      }
    } catch {
      // ignore
    }
  }

  walk(normalizedRoot, 0);
  return buildCodebaseSnapshotFromSource(files);
}

/**
 * Serialize a CodebaseSnapshot to the "codebase:" block text format (test_cases §3).
 * Use this to inspect the output of buildCodebaseSnapshotFromDirectory or to round-trip.
 */
export function codebaseSnapshotToBlock(snap: CodebaseSnapshot): string {
  const lines: string[] = ['codebase:'];
  function visit(entry: FileEntry, depth: number): void {
    const indent = '  '.repeat(depth);
    const name = entry.path ? entry.path.split('/').pop()! : '';
    if (!name) return;
    const display = entry.kind === 'directory' ? name + '/' : name;
    lines.push(indent + display);
    if (entry.lines?.length) {
      for (const line of entry.lines) lines.push(indent + '  ' + '| ' + line);
    }
    for (const child of entry.children ?? []) visit(child, depth + 1);
  }
  for (const child of snap.root.children ?? []) visit(child, 1);
  return lines.join('\n');
}
