/**
 * In-memory model of a .codoc document: tree lines with path inheritance,
 * deps block, and entity id ↔ line index mapping for focus mode and code locations.
 */

import {
  parseTreeLine,
  parseTreeBlock,
  type Sigil,
  type DepEdge,
} from 'codenav-semantic-tree/extension-api';
import type { NodeMetadata } from 'codenav-semantic-tree/extension-api';

export interface LineInfo {
  lineIndex: number;
  depth: number;
  sigil: Sigil;
  feature: string;
  metadata: NodeMetadata;
  /** Resolved entity id: fpath::entity_name or fpath (for file-only nodes). */
  entityId: string | null;
  /** For dep matching: file path (e.g. main.py). */
  fpath: string | null;
}

export interface DepLineInfo {
  lineIndex: number;
  from: string;
  to: string;
  fromId: string | null;
  toId: string | null;
}

export interface CodocDocumentSnapshot {
  lineInfos: LineInfo[];
  deps: DepEdge[];
  depsLines: DepLineInfo[];
  entityToLines: Map<string, number[]>;
  depsStartLine: number;
  /** Tree path: line index → ancestor line indices (root to parent). */
  lineToAncestors: Map<number, number[]>;
  /** Tree path: line index → descendant line indices. */
  lineToDescendants: Map<number, number[]>;
}

/**
 * Build a snapshot of the document: each tree line gets resolved entity id
 * (with path inheritance), and we parse deps to support "related lines" later.
 */
export function parseCodocDocument(
  text: string
): CodocDocumentSnapshot {
  const lines = text.split(/\r?\n/);
  const lineInfos: LineInfo[] = [];
  const entityToLines = new Map<string, number[]>();
  let currentFpath: string | null = null;
  let depsStartLine = -1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === 'deps:') {
      depsStartLine = i;
      break;
    }
    const parsed = parseTreeLine(line);
    if (!parsed) continue;

    const { depth, sigil, feature, metadata } = parsed;
    const fpath =
      metadata?.fpath ?? (sigil === '%' && feature?.trim() ? feature.trim() : currentFpath);
    if (sigil === '%' && feature?.trim()) currentFpath = feature.trim();

    const entityName = metadata?.entity_name;
    const entityId =
      fpath && entityName
        ? `${fpath}::${entityName}`
        : fpath
          ? fpath
          : null;

    const info: LineInfo = {
      lineIndex: i,
      depth,
      sigil,
      feature,
      metadata: { ...metadata, fpath: fpath ?? metadata?.fpath },
      entityId,
      fpath,
    };
    lineInfos.push(info);
    if (entityId) {
      const arr = entityToLines.get(entityId) ?? [];
      arr.push(i);
      entityToLines.set(entityId, arr);
    }
  }

  const { deps } = parseTreeBlock(text);
  const knownFpaths = new Set(lineInfos.map((l) => l.fpath).filter(Boolean) as string[]);
  const depsLines: DepLineInfo[] = [];
  if (depsStartLine >= 0) {
    for (let i = depsStartLine + 1; i < lines.length; i++) {
      const edge = parseDepLine(lines[i]);
      if (!edge) continue;
      const fromId = normalizeDepEntity(edge.from, knownFpaths);
      const toId = normalizeDepEntity(edge.to, knownFpaths);
      depsLines.push({
        lineIndex: i,
        from: edge.from,
        to: edge.to,
        fromId,
        toId,
      });
    }
  }

  const lineToAncestors = new Map<number, number[]>();
  const lineToDescendants = new Map<number, number[]>();
  for (let idx = 0; idx < lineInfos.length; idx++) {
    const info = lineInfos[idx]!;
    const depth = info.depth;
    const ancestors: number[] = [];
    for (let j = idx - 1; j >= 0; j--) {
      const prev = lineInfos[j]!;
      if (prev.depth === depth - 1) {
        ancestors.push(prev.lineIndex);
        const prevAncestors = lineToAncestors.get(prev.lineIndex) ?? [];
        ancestors.push(...prevAncestors);
        break;
      }
    }
    lineToAncestors.set(info.lineIndex, ancestors);

    const descendants: number[] = [];
    for (let j = idx + 1; j < lineInfos.length; j++) {
      const next = lineInfos[j]!;
      if (next.depth <= depth) break;
      descendants.push(next.lineIndex);
    }
    lineToDescendants.set(info.lineIndex, descendants);
  }

  return {
    lineInfos,
    deps,
    depsLines,
    entityToLines,
    depsStartLine,
    lineToAncestors,
    lineToDescendants,
  };
}

function parseDepLine(line: string): DepEdge | null {
  const trimmed = line.replace(/#.*$/, '').trim();
  const relMatch = trimmed.match(/--(imports|invokes|inherits|type-refs)-->/);
  if (!relMatch) return null;
  const relation = relMatch[1] as DepEdge['relation'];
  const parts = trimmed.split(relMatch[0]);
  if (parts.length !== 2) return null;
  const fromMatch = parts[0].trim().match(/\(([^)]+)\)\s*$/);
  const toMatch = parts[1].trim().match(/^\s*\(([^)]+)\)/);
  if (!fromMatch || !toMatch) return null;
  return {
    from: fromMatch[1].trim(),
    to: toMatch[1].trim(),
    relation,
    fromExternal: fromMatch[1].startsWith('ext:'),
    toExternal: toMatch[1].startsWith('ext:'),
  };
}

/** Normalize dep entity string (e.g. "main.greet" or "__init__.py") to our entity id using known fpaths. */
function normalizeDepEntity(
  depEntity: string,
  knownFpaths: Set<string>
): string | null {
  const t = depEntity.trim();
  if (!t) return null;
  if (t.startsWith('ext:')) return null;
  if (t.includes('::')) return t;
  if (t.includes('.')) {
    const [filePart, ...rest] = t.split('.');
    const entityPart = rest.join('.');
    for (const fpath of knownFpaths) {
      const base = fpath.replace(/\.(py|ts|js)$/, '');
      if (base === filePart || fpath === filePart) {
        return entityPart ? `${fpath}::${entityPart}` : fpath;
      }
    }
    return `${filePart}.py::${entityPart}`;
  }
  if (knownFpaths.has(t)) return t;
  for (const fpath of knownFpaths) {
    if (fpath.endsWith(t) || fpath === t) return fpath;
  }
  return t;
}

/**
 * Get line indices that should stay at full opacity (focus):
 * 1) Tree path: the node, its ancestors, and its descendants (semantic hierarchy).
 * 2) Dep-related: nodes connected by the dependency graph (imports/invokes/etc.).
 * Combined = tree path ∪ dep-related. All other tree/deps lines are dimmed.
 */
export function getRelatedLineIndices(
  snapshot: CodocDocumentSnapshot,
  lineIndex: number
): Set<number> {
  const related = new Set<number>();
  const info = snapshot.lineInfos.find((l) => l.lineIndex === lineIndex);
  const depLine = snapshot.depsLines.find((d) => d.lineIndex === lineIndex);
  const knownFpaths = new Set(snapshot.lineInfos.map((l) => l.fpath).filter(Boolean) as string[]);

  if (info) {
    related.add(info.lineIndex);
    const ancestors = snapshot.lineToAncestors.get(info.lineIndex) ?? [];
    ancestors.forEach((i) => related.add(i));
    const descendants = snapshot.lineToDescendants.get(info.lineIndex) ?? [];
    descendants.forEach((i) => related.add(i));
  }

  let entityIdsInvolved = new Set<string>();
  if (depLine) {
    related.add(depLine.lineIndex);
    if (depLine.fromId) entityIdsInvolved.add(depLine.fromId);
    if (depLine.toId) entityIdsInvolved.add(depLine.toId);
  } else if (info?.entityId) {
    entityIdsInvolved.add(info.entityId);
  }

  if (entityIdsInvolved.size > 0) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const d of snapshot.depsLines) {
        if (d.fromId && entityIdsInvolved.has(d.fromId) && d.toId && !entityIdsInvolved.has(d.toId)) {
          entityIdsInvolved.add(d.toId);
          changed = true;
        }
        if (d.toId && entityIdsInvolved.has(d.toId) && d.fromId && !entityIdsInvolved.has(d.fromId)) {
          entityIdsInvolved.add(d.fromId);
          changed = true;
        }
      }
    }
    for (const eid of entityIdsInvolved) {
      const indices = snapshot.entityToLines.get(eid);
      if (indices) indices.forEach((i) => related.add(i));
    }
    for (const d of snapshot.depsLines) {
      if (
        (d.fromId && entityIdsInvolved.has(d.fromId)) ||
        (d.toId && entityIdsInvolved.has(d.toId))
      ) {
        related.add(d.lineIndex);
      }
    }
  }

  return related;
}

/**
 * Code location for a tree line: relative fpath and 1-based line range.
 * Caller resolves fpath against workspace folder and builds vscode.Uri + Range.
 */
export interface CodeLocationRef {
  fpath: string;
  startLine: number;
  endLine: number;
}

/**
 * Get code location ref for a tree line if the node has fpath.
 * Caller should pass meta line_range when available and resolve fpath to Uri.
 */
export function getCodeLocationRef(
  info: LineInfo,
  metaLineRange: [number, number] | undefined
): CodeLocationRef | null {
  if (!info.fpath) return null;
  const [start, end] = metaLineRange ?? [1, 1];
  return { fpath: info.fpath, startLine: start, endLine: end };
}
