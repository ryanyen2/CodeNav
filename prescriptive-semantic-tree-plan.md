# Prescriptive Semantic Tree: Algorithm Design for Intent-First Codebase Manipulation

## 1. Problem Statement

RPG-Encoder demonstrates that a semantic tree can faithfully *describe* a codebase (code to tree). CoDoc requires the inverse: let users *prescribe* changes by editing the semantic tree, then propagate those edits into concrete code modifications (tree to code). The system must guarantee that forward encoding (code to tree) and inverse grounding (tree to code) converge without entering an infinite reconciliation loop.

---

## 2. Foundational Definitions

### 2.1 Node Schema

Every node in the semantic tree carries a triple (f, m, c):

- **f (feature)**: A concise behavioral description. What this code *does*, not how.
  - Example: "validate JWT expiration claims"
- **m (metadata)**: Structural anchoring to physical artifacts.
  - For grounded nodes: {type, fpath, entity_name, line_range}
  - For ungrounded nodes (newly added): m = empty until placement resolves
- **c (contract)**: Interface-level specification sufficient for code generation or verification.
  - For functions: {signature, preconditions, postconditions, invariants}
  - For classes: {public_methods, inheritance, key_state}
  - For directories/modules: {exports, module_docstring}
  - For abstract groupings: c = empty (no code artifact)

The contract c is the critical addition over RPG-Encoder. It provides the lossless interface layer that enables round-tripping: the LLM can regenerate code from (f, c) without needing the original source, and verification can check generated code against c without re-encoding.

### 2.2 Edge Types

- **E_feature (functional edges)**: Parent-child hierarchy expressing teleological containment. "JWT validation" is-part-of "Authentication" is-part-of "Security."
- **E_dep (dependency edges)**: Cross-cutting execution relationships derived from AST: imports, invokes, inherits, type-references.

### 2.3 Node Classification by Artifact Binding

Every node falls into exactly one artifact class:

| Class | Has code artifact? | Examples | Behavior on edit |
|---|---|---|---|
| Concrete-Leaf | Yes (function/method) | validate_jwt() | Modifies a specific function |
| Concrete-File | Yes (file) | auth/tokens.py | Modifies file-level structure |
| Concrete-Dir | Yes (directory) | auth/ | Modifies directory layout |
| Abstract | No | "Token Management" | Reorganizes grouping only |

This classification determines which algorithm path executes on any user operation.

---

## 3. Invariants (Must Hold After Every Operation)

**INV-1: Grounding Consistency.** For every concrete node v, re-running forward encoding on v's artifact must produce a tree node whose feature f' is semantically equivalent to v.f. If f' does not match f, the system has drifted.

**INV-2: Dependency Coherence.** Every E_dep edge corresponds to a real AST relationship. No phantom dependencies, no missing edges for existing import/call relationships.

**INV-3: Artifact Uniqueness.** No two concrete-leaf nodes in the tree may map to the same (fpath, entity_name) pair. The tree must be injective at the leaf level.

**INV-4: Hierarchical Containment.** If node v is a child of node P via E_feature, then v.m.fpath must be within the directory scope of P.m.fpath. Children cannot escape their parent's physical boundary.

**INV-5: Contract Satisfaction.** For every concrete-leaf node, the actual code must satisfy the interface contract c. If the code's actual signature, types, or documented behavior diverge from c, a reconciliation is required.

**INV-6: No Orphan Artifacts.** Every file and function in the codebase must be reachable from the tree root via E_feature edges.

---

## 4. User Operations and Their Semantics

### 4.1 Operation Taxonomy

Users manipulate the tree through six atomic operations. Every complex edit decomposes into a sequence of these atomics.

    Op ::= AddNode(parent, f, c?)        -- create a new child
         | DeleteNode(v)                  -- remove a node (and subtree?)
         | MoveNode(v, new_parent)        -- reparent a node
         | EditFeature(v, f')             -- change what v does
         | EditContract(v, c')            -- change v's interface
         | ReorderChildren(parent, perm)  -- change sibling order

Each operation has preconditions, a tree transformation, a code transformation, and a post-check.

### 4.2 AddNode

**User intent**: "I want a new capability here."

**Preconditions**:
- parent exists in the tree
- f is non-empty
- No sibling of parent has semantically duplicate f

**Decision tree for artifact creation**:

    AddNode(parent, f, c?):
      level = infer_artifact_level(parent, f, c)
      
      CASE level = function:
        target_file = resolve_file_placement(parent, f)
        IF target_file exists:
          code = LLM.generate_function(f, c, context=target_file.source)
          INSERT code into target_file at appropriate location
        ELSE:
          file_name = LLM.infer_filename(f, parent.m.fpath)
          code = LLM.generate_file_with_function(f, c, parent context)
          CREATE file at parent.m.fpath/file_name
        m = extract_metadata_from_generated_code()
        
      CASE level = file:
        file_name = LLM.infer_filename(f, parent.m.fpath)
        stub = LLM.generate_file_skeleton(f, c, sibling_context)
        CREATE file at parent.m.fpath/file_name
        m = {type: file, fpath: parent.m.fpath/file_name}
        
      CASE level = directory:
        dir_name = LLM.infer_dirname(f, parent.m.fpath)
        CREATE directory at parent.m.fpath/dir_name
        CREATE __init__.py if Python project
        m = {type: directory, fpath: parent.m.fpath/dir_name}
        
      CASE level = abstract:
        m = empty   // no artifact; pure grouping
        // no code change; tree-only operation
        
      new_node = (f, m, c)
      ATTACH new_node to parent via E_feature
      IF level in {function, file}:
        run_ast_analysis(affected_files) -> update E_dep
        run_post_check(new_node)

**infer_artifact_level heuristic**:

    infer_artifact_level(parent, f, c):
      IF c contains function signature -> function
      IF c contains class definition or exports list -> file
      IF parent is tree root or depth <= 1 -> directory or abstract
      IF f uses grouping language ("management", "utilities", "core") -> abstract
      IF confidence < threshold:
        PROMPT user: "Should this be a new function, file, module, or grouping?"

**resolve_file_placement algorithm**:

    resolve_file_placement(parent, f):
      candidates = all concrete-file nodes under parent
      IF candidates = empty: RETURN null
      
      FOR each file_node in candidates:
        existing_features = [child.f for child in file_node.children]
        score = semantic_similarity(f, existing_features)
        cohesion_penalty = size(existing_features) / MAX_FILE_DENSITY
        net_score = score - cohesion_penalty
        
      IF best_net_score > PLACEMENT_THRESHOLD:
        RETURN best_candidate
      ELSE:
        RETURN null

**Edge cases**:
- User adds a leaf under an abstract node that has no grounded path. The system must walk up the tree until it finds a grounded ancestor, then use that ancestor's path as the base scope.
- User adds a function whose contract conflicts with an existing function's contract. Detect overlapping signatures and surface conflict.
- User adds a node with feature f that the forward encoder would place under a different parent. This is a specification choice, not a bug. Record as a **pinned placement** that future re-encoding must respect.

### 4.3 DeleteNode

**User intent**: "I want to remove this capability."

    DeleteNode(v):
      dependents = all nodes w where (w -> v) in E_dep
      
      IF dependents is not empty:
        impact_report = format_dependency_list(dependents)
        choice = PROMPT user:
          "These entities depend on v: {impact_report}."
          Options:
            (a) CASCADE: delete v and all dependents
            (b) SEVER: delete v, update dependents to remove references
            (c) REDIRECT: delete v, point dependents to a replacement
            (d) ABORT
        
        CASE (a): FOR each w in topological_reverse(dependents): DeleteNode(w)
        CASE (b): FOR each w in dependents:
                    LLM.remove_reference(w.source, reference_to=v)
                    update_contract(w)
        CASE (c): replacement = user specifies or system suggests
                  FOR each w in dependents:
                    LLM.replace_reference(w.source, old=v, new=replacement)
        CASE (d): RETURN
      
      IF v is concrete-leaf:
        REMOVE function/method from v.m.fpath
        IF file is now empty: DELETE file
      IF v is concrete-file:
        DELETE file at v.m.fpath
      IF v is concrete-dir:
        ASSERT directory is empty (all children already deleted)
        DELETE directory
      IF v is abstract:
        FOR each child of v: REATTACH child to v.parent via E_feature
      
      REMOVE v from tree
      PruneOrphans(v.parent)  // recursive structural hygiene
      run_ast_analysis(affected_files) -> update E_dep
      run_post_check()

**Edge cases**:
- Deleting a node re-exported by __init__.py or a public API surface. Must check module-level exports.
- Deleting an abstract node with concrete children. Children must survive; reparent to grandparent.
- Circular dependency involving the deleted node. Flag mutual dependencies before executing.

### 4.4 MoveNode

**User intent**: "This capability belongs somewhere else."

Most complex operation: simultaneously affects semantic hierarchy, physical layout, and dependency graph.

    MoveNode(v, new_parent):
      old_parent = v.parent
      old_path = v.m.fpath
      
      // Precondition: no cycles
      ASSERT new_parent not in descendants(v)
      
      new_scope = new_parent.m.fpath
      IF new_scope = empty:
        new_scope = nearest_grounded_ancestor(new_parent).m.fpath
      
      CASE v is concrete-leaf (function):
        target_file = resolve_file_placement(new_parent, v.f)
        IF target_file exists:
          MOVE function source from old file to target_file
        ELSE:
          CREATE new file in new_scope
          MOVE function source to new file
          
      CASE v is concrete-file:
        MOVE file from old_path to new_scope/filename
        
      CASE v is concrete-dir:
        MOVE directory from old_path to new_scope/dirname
        UPDATE all descendant nodes' m.fpath (prefix replacement)
        
      CASE v is abstract:
        FOR each concrete descendant d of v:
          IF d.m.fpath NOT within new_parent's scope:
            CONFLICT: "Moving this group would orphan {d}."
            Options: (a) move descendants too, (b) abort
      
      // Update references
      affected_importers = all nodes w where (w imports v) in E_dep
      FOR each w in affected_importers:
        LLM.update_import(w.source, old_path, new_path)
      
      DETACH v from old_parent
      ATTACH v to new_parent via E_feature
      UPDATE v.m.fpath
      PruneOrphans(old_parent)
      run_ast_analysis(all affected files) -> rebuild E_dep
      run_post_check()

**Edge cases**:
- Name collision in target file. Check uniqueness before executing; prompt rename or merge.
- Deeply nested directory with many internal cross-references. Only external E_dep edges need import updates.
- Moving a node into its own descendant. Precondition check blocks this.

### 4.5 EditFeature

**User intent**: "This code should do something different now."

    EditFeature(v, f'):
      drift = semantic_distance(v.f, f')
      
      CASE drift < t_minor:
        v.f = f'
        // No code change. Tree-only update.
        IF NOT semantically_compatible(f', v.parent.f):
          SUGGEST: "This feature no longer fits under {v.parent.f}. Move it?"
        
      CASE t_minor <= drift < t_major:
        v.f = f'
        IF v is concrete-leaf:
          diff_spec = compute_behavioral_delta(v.f_old, f')
          new_code = LLM.modify_function(
            current_source = v.source,
            contract = v.c,
            behavioral_delta = diff_spec,
            context = surrounding_file
          )
          REPLACE function body in v.m.fpath
          IF new_code changes signature:
            PROMPT user: "Implementation changed the interface. Update contract?"
        
      CASE drift >= t_major:
        PROMPT user: "This is a major intent change. Treat as replacement?"
        IF confirmed:
          old_dependents = dependents of v via E_dep
          DeleteNode(v)
          new_node = AddNode(v.parent, f', c_new)
          IF old_dependents not empty:
            PROMPT: "Redirect dependents to the new node?"
      
      run_ast_analysis(affected_files) -> update E_dep
      run_post_check(v)

**Drift thresholds**:
- t_minor: Wording difference only. "check token expiry" -> "validate token expiration"
- t_major: Fundamentally different behavior. "validate JWT" -> "generate OAuth2 refresh token"
- Use LLM-as-judge for drift classification, not embedding distance alone.

**Edge cases**:
- User edits feature to match an existing sibling. Detect near-duplicates and suggest merge.
- User edits an abstract node's feature. Re-evaluate all children for fit.

### 4.6 EditContract

**User intent**: "The interface should change."

    EditContract(v, c'):
      delta = diff_contract(v.c, c')
      
      IF delta affects signature:
        callers = nodes w where (w invokes v) in E_dep
        IF callers not empty:
          migration_plan = LLM.plan_caller_migration(v.c, c', callers)
          PRESENT migration_plan to user for approval
          IF approved:
            FOR each (caller, patch) in migration_plan:
              APPLY patch to caller's source
              run_post_check(caller)
        
        new_code = LLM.regenerate_function(v.f, c', context=file)
        REPLACE function in v.m.fpath
        v.c = c'
        
      IF delta affects only invariants/postconditions:
        new_code = LLM.modify_function(
          current_source = v.source,
          new_invariants = c'.invariants,
          preserve_signature = True
        )
        REPLACE function body
        v.c = c'
      
      run_ast_analysis(affected_files) -> update E_dep
      run_post_check(v)

**Edge cases**:
- Removing a parameter with a default value. Analyze call sites via AST to partition callers into affected vs. unaffected.
- Changing return type. All downstream consumers must be checked via E_dep.

### 4.7 ReorderChildren

    ReorderChildren(parent, permutation):
      IF parent is concrete-file:
        new_order = apply_permutation(parent.children, permutation)
        LLM.reorder_functions_in_file(parent.m.fpath, new_order)
        refresh_metadata(parent)
      ELSE:
        parent.children = apply_permutation(parent.children, permutation)

---

## 5. Post-Check: The Convergence Guard

### 5.1 Algorithm

    post_check(affected_nodes):
      FOR each node v in affected_nodes:
        IF v is not concrete: SKIP
        
        v_encoded = forward_encode(v.m.fpath, v.m.entity_name)
        
        // INV-1: Feature consistency
        IF semantic_distance(v.f, v_encoded.f) > t_minor:
          WARN: "Generated code drifted from specification."
          RECORD drift_report(v, v.f, v_encoded.f)
        
        // INV-5: Contract satisfaction
        IF v.c is not empty:
          violations = check_contract(v.source, v.c)
          IF violations: WARN and RECORD
        
        // INV-4: Hierarchy consistency
        IF v.m.fpath NOT within v.parent.m.fpath:
          ERROR: "Grounding violation."
      
      // INV-2: Dependency coherence
      fresh_deps = run_ast_analysis(affected_files)
      stale_deps = E_dep edges involving affected_nodes
      SYNC E_dep with fresh_deps
      
      RETURN (warnings, errors)

### 5.2 The Anti-Loop Rule

The post-check is observational only. It detects drift and reports it. It never triggers corrective operations autonomously.

    User edits tree -> code changes -> post-check detects drift -> REPORT to user
                                                                    (never auto-fix)

The user sees drift reports as annotations on the tree and then chooses to:
- Accept the drift (update f or c to match the code)
- Reject the drift (regenerate the code to match f and c)
- Investigate (open code and specification side-by-side)

---

## 6. Compound Operations

### 6.1 Extract and Group

User selects several sibling leaves and groups them under a new abstract parent.

    ExtractAndGroup(nodes[], group_name, group_feature):
      shared_parent = assert_common_parent(nodes)
      new_abstract = AddNode(shared_parent, group_feature, c=empty)
      FOR each v in nodes: MoveNode(v, new_abstract)

### 6.2 Split Function

    SplitFunction(v, specs[]):
      parent = v.parent
      partition = LLM.partition_function(v.source, specs)
      FOR each (fi, ci, code_fragment) in partition:
        new_node = AddNode(parent, fi, ci)
      FOR each dependent w of v:
        best_replacement = LLM.match_usage(w.source, v, new_nodes)
        LLM.update_reference(w.source, old=v, new=best_replacement)
      DeleteNode(v)

### 6.3 Merge Functions

    MergeFunctions(v1, v2):
      merged_f = LLM.merge_features(v1.f, v2.f)
      merged_c = LLM.merge_contracts(v1.c, v2.c)
      survivor = v1 if v1 has more dependents else v2
      victim = the other
      EditFeature(survivor, merged_f)
      EditContract(survivor, merged_c)
      FOR each dependent w of victim:
        LLM.replace_reference(w.source, old=victim, new=survivor)
      DeleteNode(victim)

### 6.4 Promote to Module

    PromoteToModule(v):
      ASSERT v.artifact_class = concrete-file
      dir_path = v.m.fpath.replace('.py', '/')
      CREATE directory dir_path
      CREATE dir_path/__init__.py
      groups = LLM.cluster_functions(v.children, by=semantic_similarity)
      FOR each group in groups:
        file_name = LLM.infer_filename(group)
        CREATE dir_path/file_name
        FOR each child in group: MOVE child's code, UPDATE m.fpath
      v.m = {type: directory, fpath: dir_path}
      UPDATE all external importers
      DELETE old file

---

## 7. LLM Request Design Principles

### 7.1 Context Window Budget

    LLM request context =
      ALWAYS: target node's (f, c)
      ALWAYS: parent node's (f, c) for scope
      ALWAYS: sibling nodes' features
      IF function-level: surrounding file source
      IF move/refactor: old and new file contexts
      IF dependency-affecting: source of direct dependents (capped)
      NEVER: entire repository
      NEVER: nodes more than 2 hops away in E_feature (unless via E_dep)

### 7.2 Request Atomicity

Each LLM call produces a self-contained, verifiable output. The system maintains state; the LLM is stateless per call.

### 7.3 Fallback and Retry

Maximum 2 retries. If the LLM cannot produce conforming code in 3 attempts, insert a stub with TODO annotation and mark the node as status: unresolved.

---

## 8. Conflict Resolution

### 8.1 Conflict Types

| Conflict | Trigger | Resolution |
|---|---|---|
| Name collision | AddNode/MoveNode duplicate entity_name | Prompt rename |
| Semantic overlap | AddNode similar to existing sibling | Suggest merge or differentiation |
| Broken dependency | DeleteNode/MoveNode severs E_dep | Impact report; user chooses |
| Contract violation | EditFeature violates own contract | Surface to user |
| Grounding violation | MoveNode escapes parent scope | Hard error; block |
| Circular dependency | Creates cycle in E_dep | Reject; suggest alternative |
| Concurrent edit | Overlapping artifacts | Serialize operations |

### 8.2 Resolution Priority

1. Hard errors (grounding violation, circular dependency) -> block operation
2. Structural conflicts (name collision, broken dependency) -> require user decision
3. Semantic warnings (overlap, contract violation, drift) -> annotate and proceed

---

## 9. Pitfalls and Design Constraints

### 9.1 The Specification Ambiguity Problem

A feature like "handle user authentication" could mean a 5-line wrapper or a 500-line OAuth implementation. **Rule**: For AddNode at the leaf level, contract c must include at minimum a function signature. Refuse to generate code from feature alone.

### 9.2 The Drift Accumulation Problem

Small drifts can accumulate. **Rule**: Periodic full re-encoding (never automatic, never auto-corrects) produces a diff report the user reviews.

### 9.3 The File Granularity Mismatch

A single user operation may require changes to multiple files. The system must communicate: "Adding this node will modify N files: [list]."

### 9.4 The Generation Quality Cliff

LLM quality degrades with insufficient context. **Rule**: Fail fast and visibly. Node statuses:
- RESOLVED: code exists and passes post-check
- DRAFT: code exists but has drift warnings
- UNRESOLVED: stub only; generation failed or deferred
- PLANNED: no code yet; user has indicated intent

### 9.5 The Abstraction Leak

Users may express implementation details in features. Flag overly specific features and suggest decomposition. Feature describes what, contract describes interface, implementation belongs only in code.

### 9.6 Cross-Cutting Concerns

Some capabilities (logging, error handling) don't fit into a tree. E_dep edges capture these. Tree placement is the primary home; secondary relationships are dependency edges.

### 9.7 The Empty Tree Bootstrap

Path resolution is deferred until the first concrete descendant is added. When the first leaf triggers code generation, the system walks up the tree and grounds the entire ungrounded lineage in one pass.

---

## 10. Example Operation Traces

### 10.1 Adding a New Endpoint to a Web API

**Before**:
    API / request handling / HTTP methods /
      -> send_get_request [requests/api.py:get]
      -> send_post_request [requests/api.py:post]

**User**: AddNode(parent="HTTP methods", f="send PATCH request", c={signature: "(url, data=None, **kwargs) -> Response"})

**System**: infer level=function; resolve placement to requests/api.py (high affinity with siblings); LLM generates patch(); insert after post; AST adds E_dep to request(); post-check passes.

### 10.2 Reorganizing Authentication

**User**: ExtractAndGroup([validate_jwt_token, refresh_oauth_token], "token-based auth")

**System**: Create abstract node (no code change); MoveNode each selected node (same file scope, pure tree reparenting); post-check trivially passes. User later triggers PromoteToModule to split into auth/basic.py and auth/tokens.py.

### 10.3 Editing a Feature to Change Behavior

**Before**: check_email_format with invariant "returns True if RFC 5322 compliant"

**User**: EditFeature -> "validate email format and check domain MX record"

**System**: Drift is moderate (adds network operation). LLM modifies function, adds dns.resolver import. Post-check warns contract invariant is stale. User updates contract.

### 10.4 Deleting a Core Utility

**User**: DeleteNode(slugify_text) which has 3 dependents

**System**: Impact report shows 3 call sites. User chooses REDIRECT to python-slugify library. System updates all imports, removes function, preserves file (has other functions).

---

## 11. Summary of Design Decisions

| Decision | Rationale |
|---|---|
| Post-check never auto-corrects | Prevents infinite reconciliation loops |
| Contract c required for leaf code generation | Bounds LLM ambiguity; ensures verifiability |
| Drift is user-resolved | Human is authority on specification intent |
| Abstract nodes have no code artifact | Clean separation of organization from implementation |
| Operations decompose into atomics | Enables transactional rollback |
| LLM calls are stateless per call | Reproducibility; no hidden state |
| File placement uses semantic affinity | Respects existing code organization |
| Dependency impact surfaced before execution | No silent breaking changes |
| Pinned placements override auto-encoding | User organizational intent takes precedence |
| Full re-encoding is periodic and manual | Catches drift without feedback loops |
