/**
 * Surface-Absorb Protocol: absorb surfaced nodes from re-encoded tree into user-edited tree.
 *
 * Given T₁ (user-edited tree) and T₁' (re-encoded tree from codebase), produces T₂ by:
 * 1. Matching nodes by stableId
 * 2. Classifying surfaced nodes (in T₁' but not T₁)
 * 3. Absorbing surfaced nodes under their natural parent
 * 4. Annotating drift on matched nodes whose features diverged
 *
 * Key invariant: this is a tree-only operation — no code generation triggered.
 */

import type { SemanticNode } from '../types.js';

/** Result of classifying nodes between T₁ and T₁'. */
export interface ClassificationResult {
  /** Pairs of (T₁ node, T₁' node) matched by stableId. */
  matched: Map<string, { original: SemanticNode; reencoded: SemanticNode }>;
  /** Nodes in T₁' with no stableId match in T₁. */
  surfaced: Set<SemanticNode>;
  /** Subset of matched where feature strings differ (drift detected). */
  drifted: Set<string>;
}

/** Collect all nodes with their feature-path from root. Mirrors tree-diff.ts pattern. */
function collectNodesWithPath(root: SemanticNode, pathPrefix: string): Map<string, SemanticNode> {
  const out = new Map<string, SemanticNode>();
  const path = pathPrefix ? `${pathPrefix}/${root.feature}` : root.feature;
  out.set(path, root);
  for (const c of root.children) {
    for (const [p, n] of collectNodesWithPath(c, path)) out.set(p, n);
  }
  return out;
}

/** Stable id for matching: grounded = fpath::entity_name, else path. Mirrors tree-diff.ts:35-38. */
function stableId(n: SemanticNode, path: string): string {
  if (n.metadata.fpath && n.metadata.entity_name) return `${n.metadata.fpath}::${n.metadata.entity_name}`;
  return path;
}

/** Deep clone a SemanticNode tree (excluding parent refs, which are re-linked). */
function deepCloneNode(node: SemanticNode): SemanticNode {
  const clone: SemanticNode = {
    id: node.id,
    sigil: node.sigil,
    artifactClass: node.artifactClass,
    feature: node.feature,
    metadata: { ...node.metadata },
    contract: { ...node.contract },
    status: node.status,
    children: [],
  };
  if (node.provenance) clone.provenance = node.provenance;
  if (node.drift) clone.drift = { ...node.drift };
  for (const child of node.children) {
    const childClone = deepCloneNode(child);
    childClone.parent = clone;
    clone.children.push(childClone);
  }
  return clone;
}

/**
 * Classify nodes between T₁ (user-edited) and T₁' (re-encoded) into matched, surfaced, and drifted sets.
 */
export function classifyNodes(t1Root: SemanticNode, t1PrimeRoot: SemanticNode): ClassificationResult {
  const t1ByPath = collectNodesWithPath(t1Root, '');
  const t1PrimeByPath = collectNodesWithPath(t1PrimeRoot, '');

  // Build stableId → (node, path) maps
  const t1ById = new Map<string, { node: SemanticNode; path: string }>();
  for (const [path, node] of t1ByPath) {
    t1ById.set(stableId(node, path), { node, path });
  }

  const matched = new Map<string, { original: SemanticNode; reencoded: SemanticNode }>();
  const surfaced = new Set<SemanticNode>();
  const drifted = new Set<string>();

  for (const [path, node] of t1PrimeByPath) {
    const id = stableId(node, path);
    const t1Entry = t1ById.get(id);

    if (t1Entry) {
      matched.set(id, { original: t1Entry.node, reencoded: node });
      if (t1Entry.node.feature !== node.feature) {
        drifted.add(id);
      }
    } else {
      surfaced.add(node);
    }
  }

  return { matched, surfaced, drifted };
}

/**
 * Find the natural parent in T₂ for a surfaced node, using fpath containment.
 *
 * Strategy: find the node in `tree` whose fpath is the longest prefix of the surfaced node's fpath,
 * or whose fpath equals the surfaced node's fpath (same file, different entity).
 * Falls back to root if no match found.
 */
export function findNaturalParent(surfacedNode: SemanticNode, tree: SemanticNode): SemanticNode {
  const sFpath = surfacedNode.metadata.fpath;
  if (!sFpath) return tree;

  const allNodes = collectNodesWithPath(tree, '');
  let bestMatch: SemanticNode = tree;
  let bestFpathLen = -1;

  for (const [, node] of allNodes) {
    const nFpath = node.metadata.fpath;
    if (!nFpath) continue;

    // Same file: entity in same file → parent is the file node
    if (nFpath === sFpath && node !== surfacedNode) {
      // Prefer file-level nodes (type 'file') over entity-level nodes
      if (node.metadata.type === 'file' || node.metadata.type === 'directory') {
        if (nFpath.length > bestFpathLen) {
          bestMatch = node;
          bestFpathLen = nFpath.length;
        }
      } else if (bestFpathLen < 0) {
        // If no file-level match yet, use this as fallback
        bestMatch = node;
        bestFpathLen = 0;
      }
      continue;
    }

    // Directory containment: surfaced node's fpath starts with this node's fpath
    if (sFpath.startsWith(nFpath) && nFpath.length > bestFpathLen) {
      // Only consider directory/file nodes as parents for containment
      if (node.metadata.type === 'directory' || node.metadata.type === 'file') {
        bestMatch = node;
        bestFpathLen = nFpath.length;
      }
    }
  }

  return bestMatch;
}

/**
 * Absorb surfaced nodes and annotate drift, producing T₂.
 *
 * Pure function: T₁ and T₁' are not modified. Returns a new tree (deep clone of T₁)
 * with surfaced nodes attached and drift annotations applied.
 */
export function absorbAndSettle(t1Root: SemanticNode, t1PrimeRoot: SemanticNode): SemanticNode {
  const { matched, surfaced, drifted } = classifyNodes(t1Root, t1PrimeRoot);

  // Deep clone T₁ to create T₂
  const t2Root = deepCloneNode(t1Root);

  // Annotate drift on matched nodes in T₂
  if (drifted.size > 0) {
    const t2ByPath = collectNodesWithPath(t2Root, '');
    const t2ById = new Map<string, SemanticNode>();
    for (const [path, node] of t2ByPath) {
      t2ById.set(stableId(node, path), node);
    }

    for (const id of drifted) {
      const t2Node = t2ById.get(id);
      const matchEntry = matched.get(id);
      if (t2Node && matchEntry) {
        t2Node.drift = { expected: matchEntry.original.feature, actual: matchEntry.reencoded.feature };
        t2Node.status = 'draft';
      }
    }
  }

  // Absorb surfaced nodes into T₂
  for (const surfacedNode of surfaced) {
    const clone = deepCloneNode(surfacedNode);
    clone.status = 'surfaced';
    clone.provenance = 'generation_artifact';

    const parent = findNaturalParent(surfacedNode, t2Root);
    clone.parent = parent;
    parent.children.push(clone);
  }

  return t2Root;
}
