/**
 * Serialize SemanticTree to markdown (parseTreeBlock-compatible).
 * Matches server/api/semantic_tree/output/tree_serializer.py format.
 */

import type { SemanticTree, SemanticNode, DepEdge, Contract } from '../types.js';

const CONTRACT_KEYS = ['sig', 'inv', 'cls', 'exp'] as const;

function contractStr(c: Contract): string {
  const parts: string[] = [];
  for (const k of CONTRACT_KEYS) {
    const val = (c as Record<string, string | undefined>)[k];
    if (val != null && String(val).trim()) parts.push(`{${k}: ${val}}`);
  }
  return parts.join(' ');
}

function lineForNode(node: SemanticNode): string {
  const feature = (node.feature ?? '').trim() || ' ';
  let out = `${node.sigil} ${feature}`;
  if (node.metadata?.fpath) out += ` [${node.metadata.fpath}]`;
  if (node.metadata?.entity_name) out += ` (${node.metadata.entity_name})`;
  const cs = contractStr(node.contract ?? {});
  if (cs) out += ` ${cs}`;
  out += ` #${node.status}`;
  return out;
}

/** Line for clean .codoc format: no [path] on leaf, no contracts, no #status.
 * For % file nodes, use metadata.fpath (e.g. main.py) so parsed tree has correct fpath. */
function lineForCleanNode(node: SemanticNode, _parentFpath: string | undefined): string {
  const isFile = node.sigil === '%';
  const isLeaf = node.sigil === '$' || node.sigil === '^';
  const display = isFile && node.metadata?.fpath
    ? node.metadata.fpath
    : (node.feature ?? '').trim() || ' ';
  let out = `${node.sigil} ${display}`;
  if (isLeaf && node.metadata?.entity_name) out += ` (${node.metadata.entity_name})`;
  return out;
}

function dumpNode(node: SemanticNode, depth: number, lines: string[]): void {
  lines.push('  '.repeat(depth) + '- ' + lineForNode(node));
  for (const child of node.children) dumpNode(child, depth + 1, lines);
}

function dumpCleanNode(node: SemanticNode, depth: number, lines: string[], parentFpath: string | undefined): void {
  lines.push('  '.repeat(depth) + '- ' + lineForCleanNode(node, parentFpath));
  const nextFpath = node.sigil === '%' ? (node.metadata?.fpath ?? node.feature?.trim()) : (node.metadata?.fpath ?? parentFpath);
  for (const child of node.children) dumpCleanNode(child, depth + 1, lines, nextFpath);
}

function depFromId(d: DepEdge): string {
  return d.from ?? '?';
}

/**
 * Serialize a semantic tree to markdown (parseable by parseTreeBlock).
 */
export function treeToMarkdown(tree: SemanticTree): string {
  const lines: string[] = [];
  dumpNode(tree.root, 0, lines);
  if (tree.deps?.length) {
    lines.push('');
    lines.push('deps:');
    for (const d of tree.deps) {
      lines.push(`  (${depFromId(d)}) --${d.relation}--> (${d.to})`);
    }
  }
  return lines.join('\n');
}

/**
 * Serialize to clean .codoc format: no [path] on leaves, no {contracts}, no #status.
 * Path is inherited from parent % node; % nodes use feature as filename.
 */
export function treeToCleanMarkdown(tree: SemanticTree): string {
  const lines: string[] = [];
  dumpCleanNode(tree.root, 0, lines, undefined);
  if (tree.deps?.length) {
    lines.push('');
    lines.push('deps:');
    for (const d of tree.deps) {
      lines.push(`  (${depFromId(d)}) --${d.relation}--> (${d.to})`);
    }
  }
  return lines.join('\n');
}

/**
 * Build a map from node id to 1-based line number in the serialized clean markdown.
 * Used by extension to map sidebar tree items to editor lines.
 */
export function treeToLineMap(tree: SemanticTree): Map<string, number> {
  const map = new Map<string, number>();
  let line = 1;
  function walk(node: SemanticNode, depth: number, parentFpath: string | undefined): void {
    if (node.id && node.id !== '__empty' && node.id !== '__virtual') {
      map.set(node.id, line);
    }
    line += 1;
    const nextFpath = node.sigil === '%' ? (node.metadata?.fpath ?? node.feature?.trim()) : (node.metadata?.fpath ?? parentFpath);
    for (const child of node.children) walk(child, depth + 1, nextFpath);
  }
  walk(tree.root, 0, undefined);
  return map;
}
