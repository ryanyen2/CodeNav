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