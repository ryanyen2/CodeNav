/**
 * Extract companion metadata from a full SemanticTree for .codoc.meta.json.
 * Used by the extension to build metadata from backend response and to enrich parsed clean .codoc.
 */

import type { SemanticTree, SemanticNode, Contract, NodeStatus } from '../types.js';

const CONTRACT_KEYS = ['sig', 'inv', 'cls', 'exp'] as const;

export interface NodeMetaEntry {
  status: NodeStatus;
  contracts: Record<string, string>;
  line_range?: [number, number];
}

export interface CodocMetaJson {
  version: number;
  nodes: Record<string, NodeMetaEntry>;
  file_exports: Record<string, string[]>;
}

function nodeKey(node: SemanticNode): string | null {
  const fpath = node.metadata?.fpath;
  const entity = node.metadata?.entity_name;
  if (fpath && entity) return `${fpath}::${entity}`;
  if (fpath) return fpath;
  return null;
}

function contractToRecord(c: Contract): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of CONTRACT_KEYS) {
    const val = (c as Record<string, string | undefined>)[k];
    if (val != null && String(val).trim()) out[k] = String(val).trim();
  }
  return out;
}

/**
 * Walk a full SemanticTree and extract the companion metadata structure
 * (contracts, status, line_ranges, exports per file) for .codoc.meta.json.
 */
export function extractMetadata(tree: SemanticTree): CodocMetaJson {
  const nodes: Record<string, NodeMetaEntry> = {};
  const fileExports: Record<string, string[]> = {};

  function walk(node: SemanticNode): void {
    const key = nodeKey(node);
    if (key) {
      nodes[key] = {
        status: node.status ?? 'resolved',
        contracts: contractToRecord(node.contract ?? {}),
        ...(node.metadata?.line_range && { line_range: node.metadata.line_range }),
      };
    }
    if (node.sigil === '%' && node.metadata?.fpath) {
      const entities = node.children
        .filter(c => c.metadata?.entity_name)
        .map(c => c.metadata!.entity_name!);
      if (entities.length) fileExports[node.metadata.fpath] = entities;
    }
    for (const child of node.children) walk(child);
  }

  walk(tree.root);
  return { version: 1, nodes, file_exports: fileExports };
}
