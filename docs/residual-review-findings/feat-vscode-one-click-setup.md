# Residual review findings — feat/vscode-one-click-setup

Source: focused Tier-2 adversarial review (security + correctness personas) of
`git diff a284447...HEAD` for the one-click VS Code setup feature (plan:
`docs/plans/2026-06-14-001-feat-vscode-one-click-setup-plan.md`). The two
high-value findings were fixed on-branch (`fix(review): claude prompt via stdin …`):
the `claude -p` argument-injection/argv-exposure vector (prompt now on stdin +
flag-shaped `CODOC_MODEL` guard) and the secrets-bounce pidfile clobber
(ownership-aware `clear_pidfile`). The items below are low-probability /
UX-quality follow-ups, accepted as known residuals.

1. **Two-window daemon-spawn TOCTOU** (`vscode-codoc/src/daemon/daemon-manager.ts`
   `startDaemon`, correctness P3, confidence 40). `readLock → shouldSpawn → spawn
   → writeLock` is not atomic: two windows activating on the same repo within the
   spawn window can both see no lock and both spawn a `codoc watch`, defeating the
   single-owner invariant. Mitigated today by the Python `write_pidfile`
   last-writer-wins, the parent-death self-exit, `deactivate()` SIGTERM, and the
   loops' own idempotence (self-write guards + reconcile) — so the worst case is a
   brief duplicate daemon, not corruption. Proper fix: atomic lock acquisition
   (`fs.writeFileSync(lockPath, …, { flag: 'wx' })`; on `EEXIST` re-read and bail,
   treating `EEXIST` + dead pid as reap-and-retry) before spawning. No test covers
   concurrent `startDaemon` from two windows (`shouldSpawn` is tested only with a
   pre-serialized `existing`).

2. **`probeClaudeAuth` checks binary presence, not login** (`vscode-codoc/src/
   setup/credentials.ts`, UX-quality). The keyless `claude` path is gated on
   `claude --version` exit 0, not an authenticated session. If `claude` is
   installed but not logged in, setup writes `CODOC_PROVIDER=claude` and the
   subsequent `codoc init` LLM bootstrap fails via `_complete_claude`, surfacing
   the generic Retry / View Log dialog rather than a clean "log in to Claude"
   message. Runtime `_complete_claude` (codoc/config.py) does surface the
   auth/billing failure, so this is UX polish, not silent breakage. A fast,
   version-stable static probe of login state is the blocker (Open Question in the
   plan). Direction: parse `claude` config/`/status`, or do one cheap probe call.

3. **Deleting the OpenAI secret does not revert `.env`** (`vscode-codoc/src/setup/
   credentials.ts` `syncCredentialsToEnv`). When the stored key is removed (only
   reachable via external SecretStorage tooling — the extension exposes no "clear
   key" action), `syncCredentialsToEnv` early-returns and leaves the stale
   `CODOC_PROVIDER=openai` + `OPENAI_API_KEY` in `.env`; the daemon then bounces on
   a stale key. Low severity given the only trigger is out-of-band. Direction: on
   an empty key, rewrite `.env` back to the keyless `claude` provider (or remove
   the openai vars).

4. **June-15 2026 subscription-billing dependency** (plan risk, external). The
   keyless single-Claude-auth promise depends on Anthropic's headless/Agent-SDK
   subscription-billing support dated 2026-06-15. Before shipping `claude` as the
   *default* provider, re-verify the support article and `claude -p --output-format
   json` cost/billing fields on/after that date; the OpenAI fallback (U5) is the
   contingency. The billing-caveat support URL surfaced in `bootstrapCredentials`
   should be confirmed against the most specific live article.

Accepted residual testing gaps (low value now, worth adding if these areas grow):
end-to-end daemon-bounce test (stop→start with a real Python daemon alongside the
TS lock writes); concurrent two-window `startDaemon` test; an assertion that the
spawned `codoc init` child actually reads the just-written `.env` (the
`cwd: rootDir` + `load_dotenv` coupling, currently correct by inspection).
