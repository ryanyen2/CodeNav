/**
 * SemanticTree → clean .codoc format. Re-exports from CodeNav library.
 */

import { treeToCleanMarkdown as libTreeToCleanMarkdown } from 'codenav-semantic-tree/extension-api';

export function treeToCleanMarkdown(tree: import('codenav-semantic-tree/extension-api').SemanticTree): string {
  return libTreeToCleanMarkdown(tree);
}
