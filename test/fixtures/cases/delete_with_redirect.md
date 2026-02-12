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