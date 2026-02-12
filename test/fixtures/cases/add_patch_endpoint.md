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