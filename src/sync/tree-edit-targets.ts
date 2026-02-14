/**
 * Tree edit → target modification areas. Parses before/after tree markdown, diffs,
 * converts to operations, and attaches code locations (fpath, entity_name, line_range)
 * for each affected node so the system can identify which codebase sections to change.
 */

import type { SemanticNode, SemanticTree, NodePath, Operation } from '../types.js';
import { parseTreeBlock } from '../parser/tree-parser.js';
import { diffTrees, diffResultToOperation } from '../diff/tree-diff.js';

export interface TargetModificationArea {
  node_path: string;
  fpath?: string;
  entity_name?: string;
  line_range?: [number, number];
}

export interface TreeEditOperationItem {
  op: string;
  target: string;
  params: Record<string, unknown>;
  targets: TargetModificationArea[];
}

export interface TreeEditTargetsResult {
  operations: TreeEditOperationItem[];
  error?: string;
}

/** Collect path → node map (path from root = feature path). */
function collectNodesWithPath(root: SemanticNode, pathPrefix: string): Map<string, SemanticNode> {
  const out = new Map<string, SemanticNode>();
  const path = pathPrefix ? `${pathPrefix}/${root.feature}` : root.feature;
  out.set(path, root);
  for (const c of root.children) {
    for (const [p, n] of collectNodesWithPath(c, path)) out.set(p, n);
  }
  return out;
}

function nodeToTarget(node: SemanticNode, nodePath: string): TargetModificationArea {
  return {
    node_path: nodePath,
    fpath: node.metadata?.fpath,
    entity_name: node.metadata?.entity_name,
    line_range: node.metadata?.line_range,
  };
}

function findPathForNode(root: SemanticNode, target: SemanticNode, pathPrefix = ''): string | null {
  const path = pathPrefix ? `${pathPrefix}/${root.feature}` : root.feature;
  if (root === target) return path;
  for (const c of root.children) {
    const found = findPathForNode(c, target, path);
    if (found) return found;
  }
  return null;
}

/** Build targets array for one diff result from before/after trees. */
function targetsForDiffResult(
  result: { operation: string; details: Record<string, unknown> },
  before: SemanticTree,
  after: SemanticTree
): TargetModificationArea[] {
  const details = result.details;
  const beforePaths = collectNodesWithPath(before.root, '');
  const afterPaths = collectNodesWithPath(after.root, '');

  if (result.operation === 'DeleteNode' && details.removed) {
    const node = details.removed as SemanticNode;
    const path = findPathForNode(before.root, node) ?? [...beforePaths.entries()].find(([, n]) => n === node)?.[0] ?? '';
    return [nodeToTarget(node, path)];
  }
  if (result.operation === 'AddNode' && details.added) {
    const node = details.added as SemanticNode;
    const path = findPathForNode(after.root, node) ?? [...afterPaths.entries()].find(([, n]) => n === node)?.[0] ?? '';
    return [nodeToTarget(node, path)];
  }
  if (result.operation === 'MoveNode' && details.moved) {
    const node = details.moved as SemanticNode;
    const toPath = (details.toPath as string) ?? findPathForNode(after.root, node) ?? '';
    return [nodeToTarget(node, toPath)];
  }
  if (result.operation === 'EditFeature' && details.featureEdited) {
    const node = details.featureEdited as SemanticNode;
    const path = findPathForNode(after.root, node) ?? [...afterPaths.entries()].find(([, n]) => n === node)?.[0] ?? '';
    return [nodeToTarget(node, path)];
  }
  if (result.operation === 'EditContract' && details.contractEdited) {
    const node = details.contractEdited as SemanticNode;
    const path = findPathForNode(after.root, node) ?? [...afterPaths.entries()].find(([, n]) => n === node)?.[0] ?? '';
    return [nodeToTarget(node, path)];
  }
  if (result.operation === 'ReorderChildren' && details.reordered) {
    const parent = details.reordered as SemanticNode;
    const parentPath = (details.parentPath as string) ?? findPathForNode(after.root, parent) ?? '';
    return [nodeToTarget(parent, parentPath)];
  }
  return [];
}

/**
 * Compute operations and code targets from before/after tree markdown.
 * Use when the user edits the semantic tree spec; returns what changed and where in the codebase.
 */
export function computeTreeEditTargets(beforeMd: string, afterMd: string): TreeEditTargetsResult {
  try {
    const before = parseTreeBlock(beforeMd);
    const after = parseTreeBlock(afterMd);
    const diffResults = diffTrees(before, after);
    const operations: TreeEditOperationItem[] = [];

    for (const result of diffResults) {
      const op = diffResultToOperation(result, before, after);
      const targets = targetsForDiffResult(result, before, after);
      if (op) {
        operations.push({
          op: op.op,
          target: Array.isArray(op.target) ? op.target[0] ?? '' : op.target,
          params: (op.params || {}) as Record<string, unknown>,
          targets,
        });
      }
    }

    return { operations };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return { operations: [], error: message };
  }
}
