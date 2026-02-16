/**
 * Parser for prescriptive semantic tree (markdown list notation).
 * Grammar: test_cases.md §1.1, §8.
 */

import type {
  Sigil,
  ArtifactClass,
  NodeStatus,
  Contract,
  ContractKey,
  NodeMetadata,
  SemanticNode,
  DepEdge,
  DepRelationType,
  SemanticTree,
  NodePath,
} from '../types.js';

const SIGILS: Sigil[] = ['/', '%', '$', '^', '~'];
const ARTIFACT_CLASS: Record<Sigil, ArtifactClass> = {
  '/': 'concrete-dir',
  '%': 'concrete-file',
  '$': 'concrete-leaf',
  '^': 'concrete-leaf',
  '~': 'abstract',
};
const CONTRACT_KEYS: ContractKey[] = ['sig', 'inv', 'cls', 'exp'];
const STATUSES: NodeStatus[] = ['resolved', 'draft', 'unresolved', 'planned', 'surfaced'];

function parseSigil(char: string): Sigil | null {
  return SIGILS.includes(char as Sigil) ? (char as Sigil) : null;
}

function parseStatus(s: string): NodeStatus {
  const t = s.replace(/^#/, '').trim().toLowerCase();
  return STATUSES.includes(t as NodeStatus) ? (t as NodeStatus) : 'resolved';
}

/** Parse one tree line: "- / feature [path] (entity) {sig: ...} #resolved" */
export function parseTreeLine(line: string): { depth: number; sigil: Sigil; feature: string; metadata: NodeMetadata; contract: Contract; status: NodeStatus } | null {
  const trimmed = line.trimStart();
  const indent = line.length - trimmed.length;
  if (indent % 2 !== 0) return null;
  const depth = indent / 2;
  if (!trimmed.startsWith('- ')) return null;
  const rest = trimmed.slice(2).trim();
  if (rest.length < 2) return null;
  const sigil = parseSigil(rest[0]);
  if (!sigil) return null;
  let tail = rest.slice(1).trim();
  if (!tail) return null;

  let feature = '';
  const metadata: NodeMetadata = {};
  const contract: Contract = {};
  let status: NodeStatus = 'resolved';

  // Feature: until " [", " {", " #" or EOL
  let i = 0;
  while (i < tail.length) {
    const c = tail[i];
    if (c === ' ' && tail.slice(i, i + 2) === ' [') break;
    if (c === ' ' && tail.slice(i, i + 2) === ' {') break;
    if (c === ' ' && tail.slice(i, i + 2) === ' #') break;
    if (c === '[' || c === '{' || c === '#') break;
    feature += c;
    i++;
  }
  feature = feature.trim();
  tail = tail.slice(i).trimStart();

  // For leaves ($/^), if feature ends with " (name)" and we don't have entity from tail, extract so stableId = fpath::name
  if ((sigil === '$' || sigil === '^') && !metadata.entity_name) {
    const endEntity = feature.match(/^(.+?)\s+\(([^)]+)\)$/);
    if (endEntity) {
      feature = endEntity[1].trim();
      metadata.entity_name = endEntity[2].trim();
      if (metadata.type !== 'directory') metadata.type = 'function';
    }
  }

  // Grounding [path]
  const pathMatch = tail.match(/^\[([^\]]+)\]\s*/);
  if (pathMatch) {
    metadata.fpath = pathMatch[1].trim();
    if (metadata.fpath?.endsWith('/')) metadata.type = 'directory';
    else if (metadata.fpath) metadata.type = 'file';
    tail = tail.slice(pathMatch[0].length);
  }

  // Entity (entity_name)
  const entityMatch = tail.match(/^\(([^)]+)\)\s*/);
  if (entityMatch) {
    metadata.entity_name = entityMatch[1].trim();
    if (metadata.type !== 'directory') metadata.type = 'function'; // class vs function from sigil ^ vs $
    tail = tail.slice(entityMatch[0].length);
  }

  // Contracts {key: value} (multiple)
  let brace = tail.match(/^\{\s*(\w+)\s*:\s*([^}]*)\}\s*/);
  while (brace) {
    const key = brace[1] as ContractKey;
    const value = brace[2].trim();
    if (CONTRACT_KEYS.includes(key)) (contract as Record<string, string>)[key] = value;
    tail = tail.slice(brace[0].length);
    brace = tail.match(/^\{\s*(\w+)\s*:\s*([^}]*)\}\s*/);
  }

  // Status #resolved etc
  const hashMatch = tail.match(/^#(\S+)/);
  if (hashMatch) {
    status = parseStatus(hashMatch[1]);
    tail = tail.slice(hashMatch[0].length).trim();
  }

  return { depth, sigil, feature, metadata, contract, status };
}

/** Build stable id for a node (for diffing). Prefer (fpath, entity_name), else feature path. */
function nodeId(node: SemanticNode, pathFromRoot: string): string {
  if (node.metadata.fpath && node.metadata.entity_name)
    return `${node.metadata.fpath}::${node.metadata.entity_name}`;
  if (node.metadata.fpath) return node.metadata.fpath;
  return pathFromRoot || node.feature;
}

/** Parse dependency line: "  (get) --invokes--> (request)" or "  (ext:slugify)" */
function parseDepLine(line: string): DepEdge | null {
  const trimmed = line.replace(/#.*$/, '').trim();
  const relMatch = trimmed.match(/--(imports|invokes|inherits|type-refs)-->/);
  if (!relMatch) return null;
  const relation = relMatch[1] as DepRelationType;
  const parts = trimmed.split(relMatch[0]);
  if (parts.length !== 2) return null;
  const fromMatch = parts[0].trim().match(/\(([^)]+)\)\s*$/);
  const toMatch = parts[1].trim().match(/^\s*\(([^)]+)\)/);
  if (!fromMatch || !toMatch) return null;
  const fromPart = fromMatch[1].trim();
  const toPart = toMatch[1].trim();
  return {
    from: fromPart,
    to: toPart,
    relation,
    fromExternal: fromPart.startsWith('ext:'),
    toExternal: toPart.startsWith('ext:'),
  };
}

/** Parse a full tree block (list lines) and optional deps block. Returns SemanticTree. */
export function parseTreeBlock(text: string): SemanticTree {
  const lines = text.split(/\r?\n/);
  const treeLines: string[] = [];
  let depsStart = -1;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === 'deps:') {
      depsStart = i;
      break;
    }
    if (line.match(/^\s*-\s+[\/\%\$\^\~]/)) treeLines.push(line);
  }

  const virtualRoot: SemanticNode = {
    id: '__virtual',
    sigil: '~',
    artifactClass: 'abstract',
    feature: '',
    metadata: {},
    contract: {},
    status: 'resolved',
    children: [],
  };
  const stack: { node: SemanticNode; depth: number; path: string }[] = [{ node: virtualRoot, depth: -1, path: '' }];

  for (const line of treeLines) {
    const parsed = parseTreeLine(line);
    if (!parsed) continue;
    const { depth, sigil, feature, metadata, contract, status } = parsed;
    const artifactClass = ARTIFACT_CLASS[sigil];
    const node: SemanticNode = {
      id: '',
      sigil,
      artifactClass,
      feature,
      metadata: { ...metadata },
      contract: { ...contract },
      status,
      children: [],
    };

    while (stack.length > 0 && stack[stack.length - 1].depth >= depth) stack.pop();
    const pathFromRoot = stack.length === 0 ? feature : `${stack[stack.length - 1].path}/${feature}`.replace(/^\//, '');
    node.id = nodeId(node, pathFromRoot);

    const parent = stack[stack.length - 1].node;
    parent.children.push(node);
    node.parent = parent;
    stack.push({ node, depth, path: pathFromRoot });
  }

  const root = virtualRoot.children.length === 1 ? virtualRoot.children[0]! : virtualRoot;
  if (root.id === '__virtual') {
    root.id = '__empty';
  }

  // Path inheritance: % nodes use feature as fpath if missing; $/^ inherit from nearest % ancestor
  function applyPathInheritance(node: SemanticNode, ancestorFpath: string | undefined): void {
    if (node.sigil === '%' && !node.metadata.fpath) {
      node.metadata.fpath = node.feature.trim() || ancestorFpath;
    }
    const fpath = node.metadata.fpath ?? ancestorFpath;
    if ((node.sigil === '$' || node.sigil === '^') && !node.metadata.fpath && ancestorFpath) {
      node.metadata.fpath = ancestorFpath;
    }
    if (node.metadata.fpath && !node.id.includes('::')) {
      node.id = node.metadata.entity_name
        ? `${node.metadata.fpath}::${node.metadata.entity_name}`
        : node.metadata.fpath;
    }
    for (const child of node.children) applyPathInheritance(child, node.metadata.fpath ?? fpath);
  }
  applyPathInheritance(root, undefined);

  const deps: DepEdge[] = [];
  if (depsStart >= 0) {
    for (let i = depsStart + 1; i < lines.length; i++) {
      const line = lines[i];
      if (!line.trim()) continue;
      if (line.trim().startsWith('---') || line.trim().startsWith('===')) break;
      const edge = parseDepLine(line);
      if (edge) deps.push(edge);
    }
  }

  return { root, deps };
}

/** Extract tree block from test case text (between "--- TREE (BEFORE) ---" and next "---" or "deps:" then after deps until "---"). */
export function extractTreeBlockFromTestCase(content: string, section: 'BEFORE' | 'AFTER'): string {
  const marker = section === 'BEFORE' ? '--- TREE (BEFORE) ---' : '--- EXPECTED TREE (AFTER) ---';
  const idx = content.indexOf(marker);
  if (idx === -1) return '';
  const start = idx + marker.length;
  const rest = content.slice(start);
  const nextH = rest.match(/\n---/);
  const end = nextH ? nextH.index! + 1 : rest.length;
  return rest.slice(0, end).trim();
}

/** Get node by path (slash-separated feature path). */
export function findNodeByPath(root: SemanticNode, path: NodePath): SemanticNode | null {
  const parts = path.split('/').map(p => p.trim()).filter(Boolean);
  if (parts.length === 0) return root;
  let current: SemanticNode = root;
  for (const part of parts) {
    const child = current.children.find(c => c.feature === part || c.feature.includes(part));
    if (!child) return null;
    current = child;
  }
  return current;
}

