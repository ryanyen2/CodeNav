# Prescriptive Semantic Tree — Test Case Format Specification

## 1. Tree Notation

The semantic tree is expressed as a markdown nested list. Indentation depth = tree depth. Each line is a node with inline annotations using sigils.

### 1.1 Node Syntax

```
- {type_sigil} feature_description [path_grounding] {contract_annotation} #status
```

### 1.2 Sigils

| Sigil | Meaning | Artifact class |
|---|---|---|
| `/` | Concrete directory | concrete-dir |
| `%` | Concrete file | concrete-file |
| `$` | Concrete leaf (function/method) | concrete-leaf |
| `^` | Concrete leaf (class) | concrete-leaf |
| `~` | Abstract grouping | abstract |

### 1.3 Annotations

| Syntax | Meaning | Example |
|---|---|---|
| `[path]` | Grounded file/dir path | `[src/auth/tokens.py]` |
| `(entity)` | Entity name in file | `(validate_jwt)` |
| `{sig: ...}` | Contract: function signature | `{sig: (token: str) -> bool}` |
| `{inv: ...}` | Contract: invariant | `{inv: raises ExpiredTokenError if expired}` |
| `{cls: ...}` | Contract: class interface | `{cls: extends AuthBase, methods=[__call__]}` |
| `{exp: ...}` | Contract: module exports | `{exp: get, post, put, delete}` |
| `#resolved` | Node status | code exists and passes |
| `#draft` | Node status | code exists, drift warnings |
| `#unresolved` | Node status | stub or failed generation |
| `#planned` | Node status | no code yet |

### 1.4 Dependency Edges (after tree block)

```
deps:
  (entity_a) --imports--> (entity_b)
  (entity_a) --invokes--> (entity_c)
  (entity_a) --inherits--> (entity_d)
```

---

## 2. Operation Syntax

Operations are described in a fenced block:

```
op: <OPERATION_NAME>
target: <node_path_in_tree using "/" for depth>
params:
  key: value
```

Node paths use `/` to traverse the tree by feature prefix. Example: `API/request handling/HTTP methods/send GET request` identifies a node by walking the tree.

### 2.1 Operation Types

```
op: AddNode
target: API/request handling/HTTP methods          # parent
params:
  feature: "send PATCH request"
  contract: {sig: (url, data=None, **kwargs) -> Response}
  
op: DeleteNode  
target: Utilities/string helpers/slugify text      # node to delete
params:
  strategy: redirect                               # cascade | sever | redirect | abort
  redirect_to: "python-slugify.slugify"

op: MoveNode
target: Security/authentication/validate JWT       # node to move
params:
  new_parent: Security/token-based auth

op: EditFeature
target: DataProcessing/validation/check email format
params:
  new_feature: "validate email format and check domain MX record"

op: EditContract
target: API/request handling/core request dispatcher
params:
  new_contract: {sig: (method, url, timeout=30, **kwargs) -> Response}

op: ExtractAndGroup
target:
  - Security/authentication/validate JWT token
  - Security/authentication/refresh OAuth token
params:
  group_feature: "manage token-based authentication flows"
  group_name: "token-based auth"

op: SplitFunction
target: Utilities/data helpers/parse and validate config
params:
  specs:
    - feature: "parse config from YAML file"
      contract: {sig: (path: str) -> dict}
    - feature: "validate config against schema"
      contract: {sig: (config: dict, schema: dict) -> bool}

op: MergeNodes
target:
  - API/error handling/handle HTTP errors
  - API/error handling/handle connection errors
params:
  merged_feature: "handle all request errors"
```

---

## 3. Codebase Snapshot Syntax

Codebase state is shown as a file tree with inline code sketches.

```
codebase:
  src/
    api.py
      | def get(url, **kwargs): return request("GET", url, **kwargs)
      | def post(url, data=None, **kwargs): return request("POST", url, data=data, **kwargs)
      | def request(method, url, **kwargs): ...
    auth.py
      | class HTTPBasicAuth(AuthBase):
      |     def __call__(self, r): ...
      | class HTTPDigestAuth(AuthBase):
      |     def __call__(self, r): ...
      | def validate_jwt(token): ...
    utils/
      strings.py
        | def slugify(text): ...
        | def truncate(text, max_len): ...
```

---

## 4. Test Case Structure

Each test case has five sections:

```
=== TEST: <name> ===

--- DESCRIPTION ---
<what this test validates>

--- CODEBASE (BEFORE) ---
<file tree with code sketches>

--- TREE (BEFORE) ---
<semantic tree in markdown list notation>
<dependency edges>

--- OPERATION ---
<operation block>

--- EXPECTED TREE (AFTER) ---
<semantic tree after operation>
<dependency edges after>

--- EXPECTED CODEBASE (AFTER) ---
<file tree after operation>

--- EXPECTED SIDE EFFECTS ---
<list of LLM calls, user prompts, warnings, post-check results>

--- EDGE CASE NOTES ---
<what makes this case tricky>
```

---

## 5. Full Example Test Case

```
=== TEST: add_patch_endpoint ===

--- DESCRIPTION ---
User adds a new HTTP PATCH endpoint function to an existing API module.
The new function should be placed in the same file as its siblings (GET, POST)
because of high semantic affinity. Dependency edges should be updated to
reflect the new function calling the shared request() dispatcher.

--- CODEBASE (BEFORE) ---
codebase:
  requests/
    __init__.py
      | from .api import get, post
    api.py
      | from .sessions import Session
      | def request(method, url, **kwargs):
      |     with Session() as s:
      |         return s.request(method, url, **kwargs)
      | def get(url, params=None, **kwargs):
      |     return request("GET", url, params=params, **kwargs)
      | def post(url, data=None, **kwargs):
      |     return request("POST", url, data=data, **kwargs)
    sessions.py
      | class Session:
      |     def request(self, method, url, **kwargs): ...
      |     def get(self, url, **kwargs): ...
      |     def post(self, url, **kwargs): ...
    auth.py
      | class HTTPBasicAuth:
      |     def __call__(self, r): ...

--- TREE (BEFORE) ---
- ~ API
  - ~ request handling
    - % core request module [requests/api.py] {exp: request, get, post} #resolved
      - $ dispatch HTTP request [requests/api.py] (request) {sig: (method, url, **kwargs) -> Response} #resolved
      - $ send GET request [requests/api.py] (get) {sig: (url, params=None, **kwargs) -> Response} #resolved
      - $ send POST request [requests/api.py] (post) {sig: (url, data=None, **kwargs) -> Response} #resolved
  - ~ session management
    - % session handler [requests/sessions.py] #resolved
      - ^ manage HTTP session lifecycle [requests/sessions.py] (Session) {cls: methods=[request, get, post]} #resolved
  - ~ authentication
    - % auth strategies [requests/auth.py] #resolved
      - ^ attach basic auth credentials [requests/auth.py] (HTTPBasicAuth) {cls: methods=[__call__]} #resolved

deps:
  (get) --invokes--> (request)
  (post) --invokes--> (request)
  (request) --invokes--> (Session)
  (api.py) --imports--> (Session)

--- OPERATION ---
op: AddNode
target: API/request handling/core request module
params:
  feature: "send PATCH request"
  contract:
    sig: (url, data=None, **kwargs) -> Response
    inv: delegates to request() with method="PATCH"

--- EXPECTED TREE (AFTER) ---
- ~ API
  - ~ request handling
    - % core request module [requests/api.py] {exp: request, get, post, patch} #resolved
      - $ dispatch HTTP request [requests/api.py] (request) {sig: (method, url, **kwargs) -> Response} #resolved
      - $ send GET request [requests/api.py] (get) {sig: (url, params=None, **kwargs) -> Response} #resolved
      - $ send POST request [requests/api.py] (post) {sig: (url, data=None, **kwargs) -> Response} #resolved
      - $ send PATCH request [requests/api.py] (patch) {sig: (url, data=None, **kwargs) -> Response} #draft
  - ~ session management
    - % session handler [requests/sessions.py] #resolved
      - ^ manage HTTP session lifecycle [requests/sessions.py] (Session) {cls: methods=[request, get, post]} #resolved
  - ~ authentication
    - % auth strategies [requests/auth.py] #resolved
      - ^ attach basic auth credentials [requests/auth.py] (HTTPBasicAuth) {cls: methods=[__call__]} #resolved

deps:
  (get) --invokes--> (request)
  (post) --invokes--> (request)
  (patch) --invokes--> (request)           # NEW
  (request) --invokes--> (Session)
  (api.py) --imports--> (Session)

--- EXPECTED CODEBASE (AFTER) ---
codebase:
  requests/
    __init__.py
      | from .api import get, post, patch      # CHANGED: added patch
    api.py
      | from .sessions import Session
      | def request(method, url, **kwargs):
      |     with Session() as s:
      |         return s.request(method, url, **kwargs)
      | def get(url, params=None, **kwargs):
      |     return request("GET", url, params=params, **kwargs)
      | def post(url, data=None, **kwargs):
      |     return request("POST", url, data=data, **kwargs)
      | def patch(url, data=None, **kwargs):   # NEW
      |     return request("PATCH", url, data=data, **kwargs)
    sessions.py                                 # UNCHANGED
    auth.py                                     # UNCHANGED

--- EXPECTED SIDE EFFECTS ---
side_effects:
  algorithm_trace:
    1. infer_artifact_level: contract has sig -> level=function
    2. resolve_file_placement:
       - candidates: [requests/api.py]
       - siblings: [dispatch HTTP request, send GET request, send POST request]
       - semantic_similarity("send PATCH request", siblings) = HIGH
       - net_score > PLACEMENT_THRESHOLD -> place in requests/api.py
    3. LLM.generate_function:
       - context: requests/api.py source
       - feature: "send PATCH request"
       - contract: (url, data=None, **kwargs) -> Response
       - output: def patch(url, data=None, **kwargs): return request("PATCH", url, data=data, **kwargs)
    4. INSERT into requests/api.py after post()
    5. AST analysis:
       - detected: patch() calls request() -> add E_dep (patch)--invokes-->(request)
    6. parent file node update:
       - api.py exports list: {exp: request, get, post} -> {exp: request, get, post, patch}
       - __init__.py: add "patch" to import line
  post_check:
    - INV-1: forward_encode(patch) -> feature="send PATCH request" ✓ match
    - INV-4: requests/api.py within parent scope requests/ ✓
    - INV-5: signature matches contract ✓
    - result: PASS (node marked #draft until user confirms)
  user_prompts: []                   # no conflicts, no prompts needed
  warnings: []

--- EDGE CASE NOTES ---
- This is the simplest AddNode case: clear artifact level, obvious file placement,
  high sibling affinity, no conflicts.
- The __init__.py update is a secondary effect that the system must detect by
  inspecting the file node's export contract and propagating the new symbol.
- If the file had MAX_FILE_DENSITY functions already, resolve_file_placement
  would return null and a new file would be created instead.
```

---

## 6. Second Example: Delete With Dependency Impact

```
=== TEST: delete_with_redirect ===

--- DESCRIPTION ---
User deletes a utility function that has three dependents.
Chooses REDIRECT strategy pointing to an external library.
System must update all import statements and remove the function
without deleting the file (other functions remain).

--- CODEBASE (BEFORE) ---
codebase:
  src/
    utils/
      strings.py
        | import re
        | import unicodedata
        | def slugify(text):
        |     text = unicodedata.normalize('NFKD', text)
        |     text = re.sub(r'[^\w\s-]', '', text).strip().lower()
        |     return re.sub(r'[-\s]+', '-', text)
        | def truncate(text, max_len=100):
        |     return text[:max_len] + '...' if len(text) > max_len else text
    models.py
      | from src.utils.strings import slugify
      | class Article:
      |     def save(self):
      |         self.slug = slugify(self.title)
    views.py
      | from src.utils.strings import slugify
      | def create_post(request):
      |     slug = slugify(request.POST['title'])
      |     ...
    api.py
      | from src.utils.strings import slugify
      | def sanitize_input(text):
      |     return slugify(text)

--- TREE (BEFORE) ---
- ~ Utilities
  - % string helpers [src/utils/strings.py] #resolved
    - $ generate URL-safe slug from text [src/utils/strings.py] (slugify) {sig: (text: str) -> str} {inv: returns lowercase hyphenated ASCII} #resolved
    - $ truncate text to max length [src/utils/strings.py] (truncate) {sig: (text: str, max_len=100) -> str} #resolved
- ~ Content Management
  - % data models [src/models.py] #resolved
    - ^ manage article persistence [src/models.py] (Article) {cls: methods=[save]} #resolved
  - % view handlers [src/views.py] #resolved
    - $ handle post creation form [src/views.py] (create_post) {sig: (request) -> Response} #resolved
- ~ API
  - % input processing [src/api.py] #resolved
    - $ sanitize user input text [src/api.py] (sanitize_input) {sig: (text: str) -> str} #resolved

deps:
  (Article.save) --invokes--> (slugify)
  (create_post) --invokes--> (slugify)
  (sanitize_input) --invokes--> (slugify)
  (models.py) --imports--> (slugify)
  (views.py) --imports--> (slugify)
  (api.py) --imports--> (slugify)

--- OPERATION ---
op: DeleteNode
target: Utilities/string helpers/generate URL-safe slug from text
params:
  strategy: redirect
  redirect_to: "python-slugify::slugify"
  redirect_import: "from slugify import slugify"

--- EXPECTED TREE (AFTER) ---
- ~ Utilities
  - % string helpers [src/utils/strings.py] #resolved
    - $ truncate text to max length [src/utils/strings.py] (truncate) {sig: (text: str, max_len=100) -> str} #resolved
- ~ Content Management
  - % data models [src/models.py] #resolved
    - ^ manage article persistence [src/models.py] (Article) {cls: methods=[save]} #resolved
  - % view handlers [src/views.py] #resolved
    - $ handle post creation form [src/views.py] (create_post) {sig: (request) -> Response} #resolved
- ~ API
  - % input processing [src/api.py] #resolved
    - $ sanitize user input text [src/api.py] (sanitize_input) {sig: (text: str) -> str} #resolved

deps:
  (Article.save) --invokes--> (ext:slugify)     # CHANGED: now external
  (create_post) --invokes--> (ext:slugify)      # CHANGED: now external
  (sanitize_input) --invokes--> (ext:slugify)   # CHANGED: now external
  (models.py) --imports--> (ext:slugify)
  (views.py) --imports--> (ext:slugify)
  (api.py) --imports--> (ext:slugify)

--- EXPECTED CODEBASE (AFTER) ---
codebase:
  src/
    utils/
      strings.py                                  # slugify REMOVED, file survives
        | import re                                # CHANGED: removed unicodedata
        | def truncate(text, max_len=100):
        |     return text[:max_len] + '...' if len(text) > max_len else text
    models.py
      | from slugify import slugify                # CHANGED: import source
      | class Article:
      |     def save(self):
      |         self.slug = slugify(self.title)    # call site unchanged
    views.py
      | from slugify import slugify                # CHANGED: import source
      | def create_post(request):
      |     slug = slugify(request.POST['title'])  # call site unchanged
    api.py
      | from slugify import slugify                # CHANGED: import source
      | def sanitize_input(text):
      |     return slugify(text)                   # call site unchanged

--- EXPECTED SIDE EFFECTS ---
side_effects:
  algorithm_trace:
    1. Compute dependents of (slugify) via E_dep:
       - (Article.save) --invokes--> (slugify)
       - (create_post) --invokes--> (slugify)
       - (sanitize_input) --invokes--> (slugify)
    2. dependents not empty -> present impact_report to user
    3. User chose: strategy=redirect, redirect_to=python-slugify::slugify
    4. For each dependent file:
       - models.py: LLM.replace_import("from src.utils.strings import slugify", "from slugify import slugify")
       - views.py: same replacement
       - api.py: same replacement
       - Call sites unchanged (same function name "slugify")
    5. Remove slugify() from src/utils/strings.py
    6. Check: file still has truncate() -> do NOT delete file
    7. Clean unused imports in strings.py: unicodedata no longer needed -> remove
    8. PruneOrphans: parent "string helpers" still has child (truncate) -> no pruning
    9. AST analysis: rebuild E_dep for affected files
  post_check:
    - INV-2: old E_dep edges (slugify) removed, new external refs recorded ✓
    - INV-6: no orphan artifacts (strings.py still tracked via truncate) ✓
    - result: PASS
  user_prompts:
    - prompt_1:
        message: "3 entities depend on slugify: Article.save, create_post, sanitize_input"
        options: [CASCADE, SEVER, REDIRECT, ABORT]
        user_chose: REDIRECT
  warnings: []

--- EDGE CASE NOTES ---
- The redirect_to uses the same function name "slugify", so call sites don't change.
  If the external function had a different name (e.g., "generate_slug"), all call
  sites would need renaming too.
- strings.py survives deletion because truncate() remains. If slugify were the only
  function, the file would be deleted and the file-level node pruned.
- The unused import cleanup (unicodedata) is a secondary effect the system should
  detect via AST: after removing slugify's body, unicodedata has no references.
- E_dep edges now point to (ext:slugify) — the "ext:" prefix denotes an external
  dependency not tracked in the semantic tree. This is important: external deps
  are in E_dep but have no corresponding tree node.
```

---

## 7. Third Example: Move With Scope Conflict

```
=== TEST: move_function_across_modules ===

--- DESCRIPTION ---
User moves a validation function from a utilities module into the
authentication module. This requires: physical file move, import
updates for all callers, and re-grounding of the node metadata.
Tests INV-4 (hierarchical containment) enforcement.

--- CODEBASE (BEFORE) ---
codebase:
  src/
    utils/
      validation.py
        | import re
        | def validate_email(email):
        |     return bool(re.match(r'^[\w.-]+@[\w.-]+\.\w+$', email))
        | def validate_phone(phone):
        |     return bool(re.match(r'^\+?[\d\s-]{10,}$', phone))
    auth/
      login.py
        | from src.utils.validation import validate_email
        | def authenticate(email, password):
        |     if not validate_email(email):
        |         raise ValueError("Invalid email")
        |     ...

--- TREE (BEFORE) ---
- ~ Utilities
  - % input validation [src/utils/validation.py] #resolved
    - $ check email format [src/utils/validation.py] (validate_email) {sig: (email: str) -> bool} #resolved
    - $ check phone format [src/utils/validation.py] (validate_phone) {sig: (phone: str) -> bool} #resolved
- ~ Authentication
  - / auth module [src/auth/] #resolved
    - % login handler [src/auth/login.py] #resolved
      - $ authenticate user credentials [src/auth/login.py] (authenticate) {sig: (email, password) -> User} #resolved

deps:
  (authenticate) --invokes--> (validate_email)
  (login.py) --imports--> (validate_email)

--- OPERATION ---
op: MoveNode
target: Utilities/input validation/check email format
params:
  new_parent: Authentication/auth module

--- EXPECTED TREE (AFTER) ---
- ~ Utilities
  - % input validation [src/utils/validation.py] #resolved
    - $ check phone format [src/utils/validation.py] (validate_phone) {sig: (phone: str) -> bool} #resolved
- ~ Authentication
  - / auth module [src/auth/] #resolved
    - % login handler [src/auth/login.py] #resolved
      - $ authenticate user credentials [src/auth/login.py] (authenticate) {sig: (email, password) -> User} #resolved
    - % auth validation [src/auth/validation.py] #resolved
      - $ check email format [src/auth/validation.py] (validate_email) {sig: (email: str) -> bool} #resolved

deps:
  (authenticate) --invokes--> (validate_email)       # same edge, new location
  (login.py) --imports--> (validate_email)            # import path changed

--- EXPECTED CODEBASE (AFTER) ---
codebase:
  src/
    utils/
      validation.py                                     # validate_email REMOVED
        | import re
        | def validate_phone(phone):
        |     return bool(re.match(r'^\+?[\d\s-]{10,}$', phone))
    auth/
      validation.py                                     # NEW FILE
        | import re
        | def validate_email(email):
        |     return bool(re.match(r'^[\w.-]+@[\w.-]+\.\w+$', email))
      login.py
        | from src.auth.validation import validate_email  # CHANGED import path
        | def authenticate(email, password):
        |     if not validate_email(email):
        |         raise ValueError("Invalid email")
        |     ...

--- EXPECTED SIDE EFFECTS ---
side_effects:
  algorithm_trace:
    1. old_path = src/utils/validation.py, entity = validate_email
    2. new_parent scope = src/auth/ (from auth module node)
    3. resolve_file_placement(auth module, "check email format"):
       - candidates: [src/auth/login.py]
       - semantic_similarity("check email format", [authenticate user credentials]) = LOW
       - net_score < PLACEMENT_THRESHOLD -> return null
    4. No suitable existing file -> create new file
       - LLM.infer_filename("check email format", "src/auth/") -> "validation.py"
       - Create src/auth/validation.py
    5. Move validate_email source from utils/validation.py to auth/validation.py
       - Carry over `import re` dependency
    6. Update importers:
       - login.py: "from src.utils.validation import validate_email"
                -> "from src.auth.validation import validate_email"
    7. Check: src/utils/validation.py still has validate_phone -> file survives
    8. PruneOrphans: parent "input validation" still has child -> no pruning
    9. New file node "auth validation" created and attached to "auth module"
  post_check:
    - INV-4: src/auth/validation.py within src/auth/ scope ✓
    - INV-3: no duplicate (validate_email) in tree ✓
    - INV-2: E_dep edges updated to new path ✓
    - result: PASS
  user_prompts: []
  warnings: []

--- EDGE CASE NOTES ---
- The new file is named "validation.py" which collides with the filename in utils/.
  This is fine because they're in different directories. But if the user had moved
  to the same directory, a name collision would trigger a conflict.
- If validate_phone also depended on validate_email (E_dep edge), the move would
  need to update utils/validation.py too, adding a cross-module import.
- The inferred filename could also be "email_validation.py" — this depends on
  LLM judgment. The test asserts the behavior, not the exact filename.
```

---

## 8. Notation Grammar (Formal)

For parser implementors:

```
tree_line     := INDENT "- " sigil " " feature grounding? entity? contract* status?
sigil         := "/" | "%" | "$" | "^" | "~"
feature       := <free text until "[" or "{" or "#" or EOL>
grounding     := "[" path "]"
entity        := "(" identifier ")"
contract      := "{" contract_key ":" contract_value "}"
contract_key  := "sig" | "inv" | "cls" | "exp"
status        := "#resolved" | "#draft" | "#unresolved" | "#planned"
INDENT        := ("  ")*                        // 2 spaces per level

dep_line      := "  " entity " --" rel_type "--> " entity
rel_type      := "imports" | "invokes" | "inherits" | "type-refs"
entity        := "(" identifier ")" | "(ext:" identifier ")"
```

### 8.1 Incremental editing and partial input

In real use, users edit text incrementally (char-by-char, Enter, Tab, delete, move lines). Parsers should tolerate partial or invalid input:

- **Tree parser**: Lines with odd indent, missing/invalid sigil, or half-written annotations are skipped; valid lines still produce nodes. Invalid lines do not advance the depth stack, so the next valid line at the same indent may attach to the wrong parent — callers that need strict hierarchy under incremental edit may re-parse on idle or on section blur.
- **Codebase parser**: `parseCodebaseBlock` requires a line starting with `codebase:`; partial `| ` lines are still attached to the current file. Use `buildCodebaseSnapshotFromSource` to build the same snapshot format from real source files (AST/regex) instead of markdown.
- **Operation parser**: Missing `op:` yields `null`; partial `target` or `params` still return a best-effort operation with empty or default fields.

Automated tests for these behaviors live in `src/parser/*.test.ts` (tree, codebase, operation).