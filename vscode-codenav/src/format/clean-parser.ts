/**
 * Clean .codoc content → SemanticTree (with path inheritance from meta or from % nodes).
 * Use metaStore to enrich with contracts, status, line_range from .codoc.meta.json.
 */

import {
  parseTreeBlock,
  type SemanticTree,
  type SemanticNode,
} from 'codenav-semantic-tree/extension-api';
import type { CodocMetaJson } from 'codenav-semantic-tree/extension-api';

export function parseCleanCodoc(text: string): SemanticTree {
  return parseTreeBlock(text);
}

/**
 * Enrich a tree (from parsing clean .codoc) with metadata from .codoc.meta.json:
 * contracts, status, line_range per node key (fpath::entity_name or fpath).
 */
export function enrichTreeFromMeta(tree: SemanticTree, meta: CodocMetaJson): void {
  function walk(node: SemanticNode): void {
    const key = nodeKey(node);
    if (key && meta.nodes[key]) {
      const entry = meta.nodes[key];
      node.status = entry.status ?? node.status;
      if (entry.contracts && Object.keys(entry.contracts).length) {
        node.contract = { ...node.contract, ...entry.contracts };
      }
      if (entry.line_range) {
        node.metadata = { ...node.metadata, line_range: entry.line_range };
      }
    }
    for (const child of node.children) walk(child);
  }
  walk(tree.root);
}

function nodeKey(node: SemanticNode): string | null {
  const fpath = node.metadata?.fpath;
  const entity = node.metadata?.entity_name;
  if (fpath && entity) return `${fpath}::${entity}`;
  if (fpath) return fpath;
  return null;
}
