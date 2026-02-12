/**
 * Prescriptive Semantic Tree types (prescriptive-semantic-tree-plan.md + test_cases.md).
 * Node schema: (f, m, c) = feature, metadata, contract.
 */

/** Sigil → artifact class (test_cases §1.2) */
export type Sigil = '/' | '%' | '$' | '^' | '~';

export type ArtifactClass =
  | 'concrete-dir'   // /
  | 'concrete-file'  // %
  | 'concrete-leaf'  // $ function, ^ class
  | 'abstract';      // ~

export type NodeStatus = 'resolved' | 'draft' | 'unresolved' | 'planned';

/** Contract keys (test_cases §1.3, §8) */
export type ContractKey = 'sig' | 'inv' | 'cls' | 'exp';

export interface Contract {
  sig?: string;   // function signature
  inv?: string;   // invariant
  cls?: string;   // class interface
  exp?: string;   // module exports
}

/** Metadata: structural anchoring (plan §2.1) */
export interface NodeMetadata {
  type?: 'directory' | 'file' | 'function' | 'class' | 'method';
  fpath?: string;      // file or dir path
  entity_name?: string; // function/class name in file
  line_range?: [number, number];
}

/** Semantic tree node: (f, m, c) */
export interface SemanticNode {
  id: string;           // stable id for diff (e.g. path-based or feature+grounding)
  sigil: Sigil;
  artifactClass: ArtifactClass;
  feature: string;
  metadata: NodeMetadata;
  contract: Contract;
  status: NodeStatus;
  children: SemanticNode[];
  parent?: SemanticNode;
}

/** E_dep: dependency edge (plan §2.2, test_cases §1.4) */
export type DepRelationType = 'imports' | 'invokes' | 'inherits' | 'type-refs';

export interface DepEdge {
  from: string;   // entity id e.g. "get" or "ext:slugify"
  to: string;
  relation: DepRelationType;
  fromExternal?: boolean;
  toExternal?: boolean;
}

/** Full semantic tree: root node + dependency edges */
export interface SemanticTree {
  root: SemanticNode;
  deps: DepEdge[];
}

/** Node path: slash-separated feature path for targeting (test_cases §2) */
export type NodePath = string;

/** Atomic operations (plan §4.1) */
export type OperationType =
  | 'AddNode'
  | 'DeleteNode'
  | 'MoveNode'
  | 'EditFeature'
  | 'EditContract'
  | 'ReorderChildren'
  | 'ExtractAndGroup'
  | 'SplitFunction'
  | 'MergeNodes';

export interface AddNodeParams {
  feature: string;
  contract?: Contract;
  group_name?: string;
}

export interface DeleteNodeParams {
  strategy?: 'cascade' | 'sever' | 'redirect' | 'abort';
  redirect_to?: string;
  redirect_import?: string;
}

export interface MoveNodeParams {
  new_parent: NodePath;
}

export interface EditFeatureParams {
  new_feature: string;
}

export interface EditContractParams {
  new_contract: Contract;
}

export interface ReorderChildrenParams {
  permutation: number[]; // indices
}

export interface ExtractAndGroupParams {
  group_feature: string;
  group_name: string;
}

export interface SplitFunctionParams {
  specs: Array<{ feature: string; contract: Contract }>;
}

export interface MergeNodesParams {
  merged_feature: string;
}

export type OperationParams =
  | AddNodeParams
  | DeleteNodeParams
  | MoveNodeParams
  | EditFeatureParams
  | EditContractParams
  | ReorderChildrenParams
  | ExtractAndGroupParams
  | SplitFunctionParams
  | MergeNodesParams;

/** Inferred or parsed operation (for action dispatch) */
export interface Operation {
  op: OperationType;
  target: NodePath | NodePath[]; // single node or list for ExtractAndGroup/MergeNodes
  params: OperationParams;
}

/** Result of tree diff: what changed and which operation it implies */
export interface TreeDiffResult {
  operation: OperationType;
  details: DiffDetails;
}

export type DiffDetails =
  | { added: SemanticNode; parentPath: NodePath }
  | { removed: SemanticNode; parentPath: NodePath }
  | { moved: SemanticNode; fromPath: NodePath; toPath: NodePath }
  | { featureEdited: SemanticNode; oldFeature: string; newFeature: string }
  | { contractEdited: SemanticNode; oldContract: Contract; newContract: Contract }
  | { reordered: SemanticNode; parentPath: NodePath; newOrder: string[] };

/** Codebase snapshot (test_cases §3): file tree with code sketches */
export interface FileEntry {
  path: string;
  kind: 'file' | 'directory';
  children?: FileEntry[];
  lines?: string[]; // code sketch lines
}

export interface CodebaseSnapshot {
  root: FileEntry;
}
