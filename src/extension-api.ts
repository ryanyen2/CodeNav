/**
 * Slim entry for VS Code extension: tree parser, clean serialization, metadata.
 * Avoids pulling in codebase-parser (Babel/import.meta) so the extension can bundle as CJS.
 */

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
} from './types.js';

export { parseTreeBlock, parseTreeLine, extractTreeBlockFromTestCase, findNodeByPath } from './parser/tree-parser.js';
export { treeToMarkdown, treeToCleanMarkdown, treeToLineMap } from './sync/tree-serializer.js';
export { extractMetadata } from './sync/metadata-extractor.js';
export type { CodocMetaJson, NodeMetaEntry } from './sync/metadata-extractor.js';
