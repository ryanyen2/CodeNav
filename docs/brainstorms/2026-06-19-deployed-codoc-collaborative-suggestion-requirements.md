---
date: 2026-06-19
topic: deployed-codoc-collaborative-suggestion
type: Deep — product
---

# Deployed codoc — remote, GitHub-authorized suggestion on the intent tree

## Summary

Serve codoc as a GitHub-authorized web surface **from the maintainer's own always-on machine** (reached through a tunnel, not a cloud service), so remote people — a contributor with no clone, a non-coder teammate, or the maintainer from a phone — can **suggest edits and comments on the intent tree the way they review a PR**. Accepted suggestions are handed to the maintainer's local agent, which realizes them and opens a code PR, while the suggester watches the tree catch up live. v1 ships the async **suggest → hand-off → PR** loop; real-time co-editing sessions for trusted collaborators are a planned fast-follow on the same infrastructure.

---

## Problem Frame

Today codoc is a **local, single-author-of-record tool**: the only way to steer the tree is the VS Code extension on a checkout, with `codoc watch` + a coding agent alive on that same machine. Code "catches up" only because a local session is running there. That excludes everyone who isn't sitting at a dev box: a contributor on another continent who would have to clone to participate, a non-coder teammate who owns product intent but not an IDE, and the maintainer themselves when away from their machine.

These people *do* have options today — file a GitHub issue, or clone and open a PR — but those operate at the code/text altitude and demand local setup. The thing none of them can do is **operate at the intent altitude**: edit the human-intent view of the codebase and let the code follow. That intent-level steering, on a tree that stays synced to code, is codoc's wedge — and it's the thing a generic browser code-agent can't trivially copy, because it has no synced intent tree to steer.

The 2026-06-16 collaborative-editing-model brainstorm consciously parked this as a deferred extension path and built the AI-collaboration plumbing (change ledger, holds, per-author attribution, `tree.doc.json` as the authoritative artifact) **N-author-capable** specifically so it could be added later without a rewrite. This brainstorm picks up that path.

---

## Key Decisions

- **Home hub, not cloud.** The maintainer's always-on machine serves the web app, persists the doc, relays edits, enforces auth, and hosts the realizing agent — all in one place reached via a tunnel. Code, repo, and API keys never leave that box. *Why:* preserves codoc's local, file-backed identity and sidesteps third-party key custody; the cost accepted is that this box is the single always-on point everyone depends on.

- **Two tiers; ship Tier 1 first.** Tier 1 (v1) is an **async suggestion/PR-review surface** for anyone GitHub-authorized. Tier 2 (fast-follow) is **real-time co-editing sessions** for trusted collaborators. *Why:* Tier 1 is the durable wedge and reuses existing machinery; Tier 2's real-time CRDT is the only part that would re-seat the authoritative store, so it's isolated to opt-in sessions rather than made the foundation.

- **Suggest, don't execute.** Remote people can only *suggest* and *comment*; their code-implying edits land as held drafts (safe-by-default), and nothing realizes until someone with hand-off authority accepts them. *Why:* an outsider must never silently drive the maintainer's agent or spend their budget. Reuses the existing draft/hand-off hold set.

- **Realized code lands as a reviewable PR.** Remote-originated realization is implemented on a branch and opened as a code PR for normal review + CI — never pushed straight to `main`. *Why:* the agent's output is a second, independent safety gate; the maintainer's own local edits keep their existing direct flow.

- **GitHub repo role is the access model.** Read access → suggest; write access → hand-off/edit. Authorization is a GitHub OAuth + repo-collaborator check at the hub's edge. *Why:* the repo's existing permission list is already the right ACL; no parallel invite system to build or keep in sync.

- **Live catch-up ≠ live co-editing in v1.** Suggesters watch the tree and bindings update as realization lands (a host→browser push), but do not see each other's cursors or co-type. *Why:* live catch-up delivers the "I moved the codebase" payoff without CRDT; simultaneous co-editing is the Tier-2 feature.

---

## Actors

- A1. **Maintainer** — repo owner. Runs the hub; holds the keys and the agent; has hand-off authority; reviews the resulting code PR.
- A2. **Remote suggester** — GitHub-authorized, no clone. Suggests edits and comments on the intent tree. Includes the **non-coder teammate** (owns intent, not code) and the **maintainer-from-a-phone** as the same surface with the same permissions as their GitHub role.
- A3. **Local realizing agent** — the coding agent on the maintainer's box that implements handed-off intent and opens the code PR.
- Mediator — **the hub** (daemon + web server + tunnel + auth edge + Loop A/B + holds + change ledger). Not a UI actor: it authorizes, persists, attributes authorship, queues realization, and pushes live catch-up. Transport is asynchronous (suggestions are durable proposals, not live keystrokes).

---

## Key Flows

- F1. **Deploy & authorize**
  - **Trigger:** Maintainer starts the hub on their always-on box.
  - **Steps:** Hub serves the web app, opens a tunnel, prints an authed link; a visitor signs in with GitHub; the hub checks repo-collaborator status and grants suggest (read) or edit/hand-off (write).
  - **Outcome:** A shareable link only repo collaborators can open. **Covers R1, R2, R3.**

- F2. **Remote suggestion (the core loop)**
  - **Trigger:** A2 edits a feature's intent or drops a comment in the browser.
  - **Steps:** The edit lands as a tracked, A2-attributed suggestion in an *awaiting-review* state; code-implying edits are held as drafts. Nothing runs; no budget is spent.
  - **Outcome:** Intent is captured and attributed without touching the repo. **Covers R5, R6, R7, R9.**

- F3. **Hand-off & realization**
  - **Trigger:** A1 accepts a suggestion and hands it to the agent.
  - **Steps:** The draft clears, a directive queues, the local agent realizes it on a branch and opens a code PR.
  - **Outcome:** Accepted intent becomes reviewable code. **Covers R10, R11.**

- F4. **Live catch-up**
  - **Trigger:** Realization lands while A2 is on the live doc.
  - **Steps:** The hub pushes the updated tree/bindings to A2's browser.
  - **Outcome:** A2 watches the code follow, having never cloned. **Covers R13.**

- F5. **Reject / withdraw**
  - **Trigger:** A1 rejects a suggestion, or A2 withdraws their own pending one.
  - **Steps:** The suggestion reverts; any queued directive is cancelled.
  - **Outcome:** Nothing commits against either party's will. **Covers R8, R12.**

- F6. **Hub-offline degradation**
  - **Trigger:** A2 edits while the hub or tunnel is down.
  - **Steps:** Suggestions are retained locally and sync when the hub returns; cross-user visibility and realization resume on reconnect.
  - **Outcome:** No edit is lost; the system degrades to "queued," not "failed." **Covers R14, R15.**

---

## Acceptance Examples

- AE1. **Covers R1–R3, F1.** A contributor with *read* access on `owner/repo` opens the link and sees the tree in suggest mode; a logged-in non-collaborator is denied; a *write*-access collaborator additionally sees hand-off controls.
- AE2. **Covers R5, R9, F2.** A remote contributor rewords the `Authentication` description into "separate token issuance from session lifecycle." It appears as their attributed suggestion in *awaiting review*; no realize directive is queued and no agent runs.
- AE3. **Covers R10, R11, F3.** The maintainer accepts AE2 and hands it off; the local agent splits the module and opens a PR; the change reaches `main` only after PR review/CI, never by direct push.
- AE4. **Covers R13, F4.** While AE3's agent works, the remote contributor — still viewing the live doc — sees the one feature become two and the bindings repopulate, without reloading or cloning.
- AE5. **Covers R12, F5.** A remote contributor makes a code-implying suggestion (held as a draft), then withdraws it before any hand-off. The intent reverts and no directive remains queued.
- AE6. **Covers R14, R15, F6.** A remote contributor suggests an edit while the hub is briefly offline; when the hub returns, the suggestion appears for the maintainer and nothing is lost.

---

## Requirements

**Deploy & access**
- R1. The hub serves the codoc web surface from the maintainer's machine, reachable remotely via a tunnel, with no cloud service holding the repo, keys, or agent.
- R2. Access requires GitHub sign-in and is granted only to collaborators on the target repo.
- R3. GitHub repo role maps to codoc capability: read → suggest/comment; write → hand-off/edit.
- R4. The deployed surface is the existing intent-tree editor running in a browser; it does not require the visitor to install anything or clone the repo.

**Suggestion surface**
- R5. Authorized remote users can suggest edits to feature titles and descriptions on the intent tree.
- R6. Authorized remote users can leave inline comments on features (PR-review style) that the maintainer can resolve.
- R7. Every suggestion and comment is attributed to its author and rendered as a tracked, reviewable change, reusing codoc's existing suggesting and inline-comment machinery.
- R8. The maintainer can accept or reject any suggestion; a suggester can withdraw their own pending suggestion.

**Realization & safety**
- R9. A remote suggester's code-implying edit is held as a draft (safe-by-default) and never triggers the agent or spends budget on its own.
- R10. Realization runs only after a user with hand-off authority (write access; default the maintainer) accepts and hands off the suggestion.
- R11. Remote-originated realization is implemented on a branch and surfaced as a code PR for review/CI; it never pushes directly to `main`.
- R12. Rejecting or withdrawing a suggestion reverts the intent and cancels any queued realize directive.

**Live feedback & robustness**
- R13. While a suggester is viewing the live doc, accepted realizations push updated tree/bindings to their browser so they see the code catch up.
- R14. If the hub or tunnel is unavailable, suggestions are retained and sync when it returns; no edit is lost.
- R15. Concurrent suggestions from multiple remote users are reconciled without clobbering one another (mechanism to be chosen in planning).

---

## Scope Boundaries

### Deferred for later
- **Tier 2 — real-time co-editing sessions** (live cursors, presence, simultaneous merge for trusted collaborators). The planned fast-follow; scoped to opt-in sessions, not the v1 foundation.
- **Auto-realization without hand-off** (a "trust = write access, auto-run" mode) — deliberately excluded from v1's safe-by-default posture.
- **Multiple maintainer hubs / hub fail-over** — v1 assumes one always-on host per repo.

### Outside this product's identity
- **Cloud-hosted realization, managed CRDT/sync services, or third-party custody of repo access and API keys.** The realizing agent and keys stay on the maintainer's machine by design.
- **codoc as a real-time multiplayer prose editor.** The PR-review UX borrows the *look* of collaborative editing, not a multiplayer-document product.

---

## Dependencies / Assumptions

- Reuses the existing daemon (`codoc watch`), Loop A/B, the in-situ suggesting mode, inline comments, the draft/hand-off hold set, the change ledger (`actor`/`mode`/`caused_by`), and `tree.doc.json` as the authoritative artifact.
- Assumes the maintainer's machine is online and reachable via a tunnel for remote users to see each other's work and get realization; offline degrades to queued (R14).
- Assumes a GitHub OAuth/app integration and the repo-collaborator API as the authorization source (R2, R3).
- Assumes live catch-up (R13) is a lightweight host→browser push of derived tree/binding state — **not** CRDT; CRDT enters only with the deferred Tier 2.

---

## Outstanding Questions

All resolvable during planning; none block the product shape.

### Deferred to planning
- **Live-catch-up transport.** Does R13 reuse the existing file-watch → webview update path lifted over the tunnel, or need a new push channel? Decides whether the browser is a thin client of the hub's existing state-push or a new surface.
- **Tunnel + auth posture.** Which tunnel (e.g. a named tunnel vs ad-hoc) and which GitHub integration shape (OAuth App vs GitHub App) — both bear on the security surface of exposing a home machine.
- **Tier 2 store model.** When real-time co-editing lands, does the CRDT replace `tree.doc.json` as the live authority, or act as a transient sync layer flushed back to it? (The parked fork from dialogue.)
- **Divergence surfacing on remote realizations.** Reuse the prior brainstorm's divergence rule (agent touched beyond the edited feature, or reflected change exceeds the suggestion) so a remote suggester sees when the agent did something other than intended.
- **Concurrent-suggestion reconciliation (R15).** Soft-lock vs last-write-wins vs proposal queue for two remote users editing the same feature.
- **Code-PR integration.** How branch/PR creation for remote-originated realization slots into the existing realize queue and status lifecycle.
