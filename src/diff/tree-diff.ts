/**
 * Tree diff: compare semantic tree before vs after to infer atomic operations.
 * Enables "tree diff understanding → trigger correct actions" (plan §4).
 */

import type {
  SemanticNode,
  SemanticTree,
  NodePath,
  OperationType,
  Operation,
  TreeDiffResult,
  DiffDetails,
  AddNodeParams,
  DeleteNodeParams,
  MoveNodeParams,
  EditFeatureParams,
  EditContractParams,
  ReorderChildrenParams,
} from '../types.js';
import { findNodeByPath } from '../parser/tree-parser.js';

/** Collect all nodes in tree with path from root (feature path). */
function collectNodesWithPath(root: SemanticNode, pathPrefix: string): Map<string, SemanticNode> {
  const out = new Map<string, SemanticNode>();
  const path = pathPrefix ? `${pathPrefix}/${root.feature}` : root.feature;
  out.set(path, root);
  for (const c of root.children) {
    for (const [p, n] of collectNodesWithPath(c, path)) out.set(p, n);
  }
  return out;
}

/** Stable id for matching: grounded = fpath::entity, else path. */
function stableId(n: SemanticNode, path: string): string {
  if (n.metadata.fpath && n.metadata.entity_name) return `${n.metadata.fpath}::${n.metadata.entity_name}`;
  return path;
}

/** Deep equality for contract (simple key-value). */
function contractEqual(a: Record<string, string | undefined>, b: Record<string, string | undefined>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if ((a as Record<string, string>)[k] !== (b as Record<string, string>)[k]) return false;
  }
  return true;
}

export interface TreeDiffOptions {
  /** Prefer to report MoveNode when the same entity appears in a different path. */
  inferMove?: boolean;
}

/**
 * Compare before and after trees; returns a list of inferred operations.
 * Order: removes first, then moves, then edits, then adds, then reorders.
 */
export function diffTrees(
  before: SemanticTree,
  after: SemanticTree,
  options: TreeDiffOptions = {}
): TreeDiffResult[] {
  const { inferMove = true } = options;
  const results: TreeDiffResult[] = [];
  const oldByPath = collectNodesWithPath(before.root, '');
  const newByPath = collectNodesWithPath(after.root, '');
  const oldById = new Map<string, { node: SemanticNode; path: string }>();
  const newById = new Map<string, { node: SemanticNode; path: string }>();
  for (const [path, node] of oldByPath) {
    const id = stableId(node, path);
    oldById.set(id, { node, path });
  }
  for (const [path, node] of newByPath) {
    const id = stableId(node, path);
    newById.set(id, { node, path });
  }

  const parentPath = (path: string): string => {
    const i = path.lastIndexOf('/');
    return i <= 0 ? '' : path.slice(0, i);
  };

  // 1) Deleted: in old, not in new (by id). Same entity not elsewhere in new.
  const processedOldIds = new Set<string>();
  for (const [path, node] of oldByPath) {
    const id = stableId(node, path);
    const inNew = newById.get(id);
    if (!inNew) {
      const sameEntityNew = node.metadata.entity_name
        ? [...newByPath.entries()].find(([, n]) => n.metadata.entity_name === node.metadata.entity_name)
        : null;
      if (!sameEntityNew) {
        const parent = parentPath(path);
        results.push({
          operation: 'DeleteNode',
          details: { removed: node, parentPath: parent },
        });
        processedOldIds.add(id);
      }
    }
  }

  // 2) Moved: same entity (fpath::entity or entity_name) but path or fpath changed
  for (const [path, node] of newByPath) {
    const id = stableId(node, path);
    const oldEntry = oldById.get(id);
    if (oldEntry) {
      const { path: oldPath, node: oldNode } = oldEntry;
      const newParent = parentPath(path);
      const oldParent = parentPath(oldPath);
      if (newParent !== oldParent) {
        results.push({
          operation: 'MoveNode',
          details: { moved: node, fromPath: oldPath, toPath: path },
        });
        processedOldIds.add(id);
      } else if (oldNode.metadata.fpath !== node.metadata.fpath && node.metadata.entity_name) {
        results.push({
          operation: 'MoveNode',
          details: { moved: node, fromPath: oldPath, toPath: path },
        });
        processedOldIds.add(id);
      }
    } else if (inferMove && node.metadata.entity_name) {
      const oldWithSameEntity = [...oldByPath.entries()].find(
        ([p, n]) => n.metadata.entity_name === node.metadata.entity_name && stableId(n, p) !== id
      );
      if (oldWithSameEntity) {
        const [oldPath] = oldWithSameEntity;
        results.push({
          operation: 'MoveNode',
          details: { moved: node, fromPath: oldPath, toPath: path },
        });
      }
    }
  }

  // 3) EditFeature / EditContract: same path (or same id), content changed
  for (const [path, newNode] of newByPath) {
    const id = stableId(newNode, path);
    const oldEntry = oldById.get(id);
    if (!oldEntry) continue;
    const { node: oldNode } = oldEntry;
    if (oldNode.feature !== newNode.feature) {
      if (!results.some(r => r.operation === 'MoveNode' && (r.details as { moved: SemanticNode }).moved === newNode)) {
        results.push({
          operation: 'EditFeature',
          details: { featureEdited: newNode, oldFeature: oldNode.feature, newFeature: newNode.feature },
        });
      }
    }
    if (!contractEqual(oldNode.contract as Record<string, string>, newNode.contract as Record<string, string>)) {
      results.push({
        operation: 'EditContract',
        details: { contractEdited: newNode, oldContract: oldNode.contract, newContract: newNode.contract },
      });
    }
  }

  // 4) Added: in new, no corresponding old (and not already marked as move)
  for (const [path, node] of newByPath) {
    const id = stableId(node, path);
    const oldEntry = oldById.get(id);
    const alreadyMoved = results.some(
      r => r.operation === 'MoveNode' && (r.details as { moved: SemanticNode; toPath: string }).toPath === path
    );
    if (!oldEntry && !alreadyMoved) {
      const parent = parentPath(path);
      results.push({
        operation: 'AddNode',
        details: { added: node, parentPath: parent },
      });
    }
  }

  // 5) Reorder: same parent, same children, order changed
  for (const [path, newNode] of newByPath) {
    const parentPathStr = parentPath(path);
    if (!parentPathStr) continue;
    const oldParentNode = findNodeByPath(before.root, parentPathStr);
    const newParentNode = findNodeByPath(after.root, parentPathStr);
    if (!oldParentNode || !newParentNode) continue;
    const oldChildFeatures = oldParentNode.children.map(c => c.feature);
    const newChildFeatures = newParentNode.children.map(c => c.feature);
    if (
      oldChildFeatures.length === newChildFeatures.length &&
      oldChildFeatures.join('\0') !== newChildFeatures.join('\0')
    ) {
      const reorderDetail: DiffDetails = {
        reordered: newParentNode,
        parentPath: parentPathStr,
        newOrder: newChildFeatures,
      };
      if (!results.some(r => r.operation === 'ReorderChildren' && (r.details as { parentPath: string }).parentPath === parentPathStr)) {
        results.push({ operation: 'ReorderChildren', details: reorderDetail });
      }
    }
  }

  return results;
}

/**
 * Convert a TreeDiffResult into an Operation (for action dispatch).
 * Fills target and params from diff details.
 */
export function diffResultToOperation(result: TreeDiffResult, before: SemanticTree, after: SemanticTree): Operation | null {
  const { operation, details } = result;
  switch (operation) {
    case 'AddNode':
      if ('added' in details && 'parentPath' in details) {
        return {
          op: 'AddNode',
          target: details.parentPath,
          params: { feature: details.added.feature, contract: details.added.contract },
        };
      }
      break;
    case 'DeleteNode':
      if ('removed' in details) {
        const path = [...collectNodesWithPath(before.root, '')].find(([, n]) => n === details.removed)?.[0] ?? '';
        return { op: 'DeleteNode', target: path, params: {} };
      }
      break;
    case 'MoveNode':
      if ('moved' in details && 'fromPath' in details && 'toPath' in details) {
        const newParent = details.toPath.replace(/\/[^/]+$/, '');
        return {
          op: 'MoveNode',
          target: details.fromPath,
          params: { new_parent: newParent },
        };
      }
      break;
    case 'EditFeature':
      if ('featureEdited' in details && 'newFeature' in details) {
        const path = [...collectNodesWithPath(after.root, '')].find(([, n]) => n === details.featureEdited)?.[0] ?? '';
        return {
          op: 'EditFeature',
          target: path,
          params: { new_feature: details.newFeature },
        };
      }
      break;
    case 'EditContract':
      if ('contractEdited' in details && 'newContract' in details) {
        const path = [...collectNodesWithPath(after.root, '')].find(([, n]) => n === details.contractEdited)?.[0] ?? '';
        return {
          op: 'EditContract',
          target: path,
          params: { new_contract: details.newContract },
        };
      }
      break;
    case 'ReorderChildren':
      if ('reordered' in details && 'parentPath' in details && 'newOrder' in details) {
        const perm = details.newOrder.map((_, i) => i);
        return {
          op: 'ReorderChildren',
          target: details.parentPath,
          params: { permutation: perm },
        };
      }
      break;
    default:
      break;
  }
  return null;
}
