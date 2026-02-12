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
import { readdirSync, statSync } from 'fs';
import { join } from 'path';

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

/** Extract code sketch lines from source (e.g. "def foo(...):" or "class Bar:") for snapshot. */
function declarationLinesFromSource(path: string, content: string): string[] {
  const ext = path.replace(/^.*\./, '').toLowerCase();
  const lines = content.split(/\r?\n/);
  const out: string[] = [];

  if (ext === 'py') {
    for (const line of lines) {
      const t = line.trim();
      const defMatch = t.match(/^def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s]+)?\s*:/);
      const classMatch = t.match(/^class\s+(\w+)\s*(?:\([^)]*\))?\s*:/);
      if (defMatch) out.push(`def ${defMatch[1]}(...): ...`);
      else if (classMatch) out.push(`class ${classMatch[1]}: ...`);
    }
    return out;
  }

  if (['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs'].includes(ext)) {
    try {
      const parse = (globalThis as unknown as { __babelParse?: (code: string) => unknown }).__babelParse;
      if (typeof parse === 'function') {
        type ASTNode = { type: string; id?: { name: string }; declaration?: { type: string; id?: { name: string } } };
        const ast = parse(content) as { body?: ASTNode[] };
        if (ast?.body) {
          for (const node of ast.body) {
            if (node.type === 'FunctionDeclaration' && node.id)
              out.push(`function ${node.id.name}(...): ...`);
            else if (node.type === 'ClassDeclaration' && node.id)
              out.push(`class ${node.id.name}: ...`);
            else if (node.type === 'ExportNamedDeclaration' && node.declaration) {
              const d = node.declaration as { type: string; id?: { name: string } };
              if (d.type === 'FunctionDeclaration' && d.id) out.push(`function ${d.id.name}(...): ...`);
              else if (d.type === 'ClassDeclaration' && d.id) out.push(`class ${d.id.name}: ...`);
            }
          }
        }
        return out;
      }
    } catch {
      // fallback to regex
    }
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
