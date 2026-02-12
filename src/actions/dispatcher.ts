/**
 * Action dispatcher: map inferred or parsed operations to handlers.
 * No code generation in v1 — handlers are stubs that return a plan / intent.
 */

import type {
  Operation,
  OperationType,
  SemanticTree,
  CodebaseSnapshot,
  AddNodeParams,
  DeleteNodeParams,
  MoveNodeParams,
  EditFeatureParams,
  EditContractParams,
  ReorderChildrenParams,
  ExtractAndGroupParams,
  SplitFunctionParams,
  MergeNodesParams,
} from '../types.js';
import { findNodeByPath } from '../parser/tree-parser.js';

export type ActionResult =
  | { kind: 'add_node'; parentPath: string; feature: string; contract?: AddNodeParams['contract']; plan: string[] }
  | { kind: 'delete_node'; targetPath: string; strategy?: DeleteNodeParams['strategy']; redirect_to?: string; plan: string[] }
  | { kind: 'move_node'; targetPath: string; newParentPath: string; plan: string[] }
  | { kind: 'edit_feature'; targetPath: string; newFeature: string; plan: string[] }
  | { kind: 'edit_contract'; targetPath: string; newContract: EditContractParams['new_contract']; plan: string[] }
  | { kind: 'reorder_children'; parentPath: string; permutation: number[]; plan: string[] }
  | { kind: 'extract_and_group'; targets: string[]; groupFeature: string; groupName: string; plan: string[] }
  | { kind: 'split_function'; targetPath: string; specs: SplitFunctionParams['specs']; plan: string[] }
  | { kind: 'merge_nodes'; targets: string[]; mergedFeature: string; plan: string[] }
  | { kind: 'unhandled'; op: OperationType; message: string };

/**
 * Dispatch an operation to the correct handler. Returns an action result (stub plan).
 * Does not modify codebase or tree; only returns what would be done.
 */
export function dispatch(
  operation: Operation,
  _tree: SemanticTree,
  _codebase?: CodebaseSnapshot
): ActionResult {
  const { op, target, params } = operation;
  const targetPath = Array.isArray(target) ? target[0] : target;

  switch (op) {
    case 'AddNode': {
      const p = params as AddNodeParams;
      return {
        kind: 'add_node',
        parentPath: targetPath,
        feature: p.feature,
        contract: p.contract,
        plan: [
          'infer_artifact_level(parent, feature, contract)',
          'resolve_file_placement(parent, feature) or create new file',
          'LLM.generate_function / generate_file (stub)',
          'INSERT into target file; update E_dep',
          'run_post_check',
        ],
      };
    }
    case 'DeleteNode': {
      const p = params as DeleteNodeParams;
      return {
        kind: 'delete_node',
        targetPath,
        strategy: p.strategy,
        redirect_to: p.redirect_to,
        plan: [
          'compute dependents via E_dep',
          'if dependents: present impact_report; user chooses strategy',
          'apply strategy (cascade/sever/redirect/abort)',
          'remove node from tree; remove/modify code artifact',
          'PruneOrphans; run_ast_analysis; run_post_check',
        ],
      };
    }
    case 'MoveNode': {
      const p = params as MoveNodeParams;
      return {
        kind: 'move_node',
        targetPath,
        newParentPath: p.new_parent,
        plan: [
          'resolve new scope from new_parent',
          'resolve_file_placement or create new file in new scope',
          'move source; update importers via E_dep',
          'detach from old parent; attach to new parent; update m.fpath',
          'PruneOrphans; run_ast_analysis; run_post_check',
        ],
      };
    }
    case 'EditFeature': {
      const p = params as EditFeatureParams;
      return {
        kind: 'edit_feature',
        targetPath,
        newFeature: p.new_feature,
        plan: [
          'compute drift = semantic_distance(old_f, new_f)',
          'if minor: tree-only update',
          'if moderate: LLM.modify_function (stub)',
          'if major: prompt replacement or Delete+AddNode',
          'run_ast_analysis; run_post_check',
        ],
      };
    }
    case 'EditContract': {
      const p = params as EditContractParams;
      return {
        kind: 'edit_contract',
        targetPath,
        newContract: p.new_contract,
        plan: [
          'diff_contract(old_c, new_c)',
          'if signature change: plan caller migration; LLM.regenerate_function (stub)',
          'if invariants only: LLM.modify_function preserve_signature (stub)',
          'run_ast_analysis; run_post_check',
        ],
      };
    }
    case 'ReorderChildren': {
      const p = params as ReorderChildrenParams;
      return {
        kind: 'reorder_children',
        parentPath: targetPath,
        permutation: p.permutation,
        plan: [
          'if parent is concrete-file: LLM.reorder_functions_in_file (stub)',
          'else: apply permutation to parent.children',
        ],
      };
    }
    case 'ExtractAndGroup': {
      const p = params as ExtractAndGroupParams;
      const targets = Array.isArray(target) ? target : [target];
      return {
        kind: 'extract_and_group',
        targets,
        groupFeature: p.group_feature,
        groupName: p.group_name,
        plan: ['AddNode(abstract); for each target MoveNode into new group'],
      };
    }
    case 'SplitFunction': {
      const p = params as SplitFunctionParams;
      return {
        kind: 'split_function',
        targetPath,
        specs: p.specs,
        plan: [
          'LLM.partition_function (stub)',
          'AddNode for each partition; update dependents; DeleteNode(original)',
        ],
      };
    }
    case 'MergeNodes': {
      const p = params as MergeNodesParams;
      const targets = Array.isArray(target) ? target : [target];
      return {
        kind: 'merge_nodes',
        targets,
        mergedFeature: p.merged_feature,
        plan: [
          'merge features and contracts; pick survivor',
          'EditFeature+EditContract on survivor; redirect dependents of victim; DeleteNode(victim)',
        ],
      };
    }
    default:
      return { kind: 'unhandled', op: op as OperationType, message: `Unknown operation: ${op}` };
  }
}
