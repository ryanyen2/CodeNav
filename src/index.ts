/**
 * CodeNav semantic tree: parser, tree diff, and action dispatch.
 * First version: codebase detail + parsing; tree diff → operations → action stubs (no generation).
 */

// Types
export type {
  Sigil,
  ArtifactClass,
  NodeStatus,
  Contract,
  NodeMetadata,
  SemanticNode,
  DepEdge,
  DepRelationType,
  SemanticTree,
  NodePath,
  OperationType,
  Operation,
  OperationParams,
  TreeDiffResult,
  DiffDetails,
  CodebaseSnapshot,
  FileEntry,
} from './types.js';

// Tree parser: markdown list → SemanticTree + deps
export { parseTreeBlock, extractTreeBlockFromTestCase, findNodeByPath } from './parser/tree-parser.js';

// Codebase parser: codebase block, filesystem, or AST-from-source
export {
  parseCodebaseBlock,
  discoverCodebase,
  buildCodebaseSnapshotFromSource,
  buildCodebaseSnapshotFromDirectory,
  codebaseSnapshotToBlock,
} from './parser/codebase-parser.js';
export type { SourceFile } from './parser/codebase-parser.js';

// Operation parser: OPERATION block → Operation
export { extractOperationBlock, parseOperationBlock } from './parser/operation-parser.js';

// Tree diff: before vs after → inferred operations
export { diffTrees, diffResultToOperation } from './diff/tree-diff.js';
export type { TreeDiffOptions } from './diff/tree-diff.js';

// Action dispatch: Operation → ActionResult (stub plans)
export { dispatch } from './actions/dispatcher.js';
export type { ActionResult } from './actions/dispatcher.js';

// Sync: Surface-Absorb Protocol
export { classifyNodes, findNaturalParent, absorbAndSettle } from './sync/absorb.js';
export type { ClassificationResult } from './sync/absorb.js';
