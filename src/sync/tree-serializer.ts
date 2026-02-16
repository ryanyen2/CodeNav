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

function dumpNode(node: SemanticNode, depth: number, lines: string[]): void {
  lines.push('  '.repeat(depth) + '- ' + lineForNode(node));
  for (const child of node.children) dumpNode(child, depth + 1, lines);
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
