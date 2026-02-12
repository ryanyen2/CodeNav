/**
 * Parser for codebase snapshot (test_cases §3) and codebase discovery from filesystem.
 */

import type { FileEntry, CodebaseSnapshot } from '../types.js';
import { readdirSync, statSync } from 'fs';
import { join } from 'path';

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
      if (entry.kind === 'directory') stack.push({ entry, indent });
    } else {
      const parent = stack[stack.length - 1].entry;
      if (!parent.children) parent.children = [];
      parent.children.push(entry);
      if (entry.kind === 'directory') stack.push({ entry, indent });
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
