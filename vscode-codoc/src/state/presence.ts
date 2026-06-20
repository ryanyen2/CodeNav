/**
 * presence.ts — the pure logic of agent-as-collaborator presence (P3 / spec §B).
 *
 * "When an agent works, a small labelled avatar glides through the doc to the feature it is
 * touching, whispering 'Claude is implementing Persist feature drafts'." Driven entirely by
 * the ALREADY-plumbed `sync.phase` (fid → editing|reflecting|done) + `activeWrite`/`activeRead`
 * + the new `sync.realize {done,total,current}` (the Python side now stamps
 * `format_realize_detail` → status.detail, parsed by parseRealizeProgress). No new backend
 * data, no doc-parse — the round-trip is untouched.
 *
 * This module holds the deterministic, DOM-free pieces (the phase→glyph/verb mapping, the
 * whisper formatting incl. realize progress, the multi-agent stack/collapse, the off-screen
 * clamp math) so they are unit-testable; the DOM (the floating avatar, glide, trail, tree
 * twin) lives in the webview and is EDH-only.
 */

/** The agent phase on a feature — the doc's `sync.phase` plus a `read` lane (from
 *  `activeRead`, which `sync.phase` doesn't carry). */
export type PresencePhase = 'editing' | 'reflecting' | 'read' | 'done';

/** The realize-progress slice (sync.realize): which directive of how many the agent is on. */
export interface RealizeProgress { done: number; total: number; current: string }

/** The Phosphor glyph the avatar's inner mark draws per phase (§B.1). Names map onto the
 *  icon registry (icons.ts) — editing → pen-nib, reflecting/done → arrows-clockwise, read → eye. */
export function phaseGlyph(phase: PresencePhase): 'pen-nib' | 'arrows-clockwise' | 'eye' {
    switch (phase) {
        case 'editing': return 'pen-nib';
        case 'reflecting': return 'arrows-clockwise';
        case 'done': return 'arrows-clockwise'; // transient; the landed check pops separately
        case 'read': return 'eye';
    }
}

/** The present-continuous lowercase verb for the whisper (§B.4 copy table). */
export function phaseVerb(phase: PresencePhase): string {
    switch (phase) {
        case 'editing': return 'implementing';
        case 'reflecting': return 'syncing the tree';
        case 'read': return 'reading';
        case 'done': return 'done';
    }
}

/** The realize-progress fragment of the whisper (§B + the new sync.realize signal): while the
 *  agent is realizing, surface `implementing 3/5 · <current title>`. Degrades to the plain verb
 *  when `realize` is undefined or empty. Pure — unit-tested. */
export function realizeWhisper(verb: string, realize?: RealizeProgress): string {
    if (!realize || !realize.total) return verb;
    const head = `${verb} ${realize.done}/${realize.total}`;
    const title = (realize.current || '').trim();
    return title ? `${head} · ${title}` : head;
}

/** The full whisper label (§B.4): `{Agent} · {verb}[ {title}]`, with the realize progress
 *  folded into the verb while editing. Present-continuous, lowercase, never exclamatory.
 *   - editing  → `Claude · implementing 3/5 · <current>` (realize) or `Claude · implementing`
 *   - reflecting → `Claude · syncing the tree`
 *   - read     → `Claude · reading <title>`
 *   - done     → `Claude · done`
 */
export function presenceWhisper(
    agent: string, phase: PresencePhase, title: string, realize?: RealizeProgress,
): string {
    if (phase === 'read') return `${agent} · reading ${title}`.trim();
    if (phase === 'editing') return `${agent} · ${realizeWhisper(phaseVerb(phase), realize)}`;
    return `${agent} · ${phaseVerb(phase)}`;
}

/** An active agent's live presence: which feature it is on + its phase + role (drives the
 *  --ink-* tint). One per agent (rarely more than one). */
export interface AgentPresence {
    /** A stable key for the agent (role id) — also the DOM key + the --ink-* class suffix. */
    role: string;
    /** Display name for the whisper (`Claude`, `Codex`, …). */
    name: string;
    /** The feature the agent is on right now. */
    fid: string;
    phase: PresencePhase;
}

/** The known agent roles → display name + the ink-class suffix (matches the --ink-* palette +
 *  the .codoc-role-* classes). The human is never an "agent" here. */
const ROLE_NAMES: Record<string, string> = {
    'claude-code': 'Claude', claude: 'Claude',
    codex: 'Codex', gemini: 'Gemini', cursor: 'Cursor', loop: 'Claude',
};

/** Display name for a role id (falls back to a Title-cased id). */
export function roleName(role: string): string {
    return ROLE_NAMES[role] ?? (role ? role[0].toUpperCase() + role.slice(1) : 'Agent');
}

/** The --ink-* class suffix for a role (claude/codex/gemini/cursor) — the avatar tint. Unknown
 *  roles fall back to claude (lilac), so an avatar is always tinted. */
export function roleInk(role: string): string {
    if (role === 'codex' || role === 'gemini' || role === 'cursor') return role;
    return 'claude';
}

/**
 * Derive the active agent presences from the live sync signal (§B.1). Since the activity
 * schema carries no per-agent identity, all active features are attributed to ONE agent
 * (`role`, default the keyless-Claude default) — the common case (there is rarely more than
 * one). A feature in `editing`/`reflecting` (from `phase`) wins over a bare `read`. The
 * avatar parks on the agent's CURRENT feature: the most-recently-active one, which the caller
 * passes as `primaryFid` (the realize `current` title's feature, else the first write phase).
 *
 * Returns at most one presence today, but the shape is a list so a future per-agent signal
 * drops in without a caller change.
 */
export function deriveAgentPresences(
    phase: Record<string, PresencePhase | 'editing' | 'reflecting' | 'done'>,
    activeRead: readonly string[],
    role = 'claude',
): AgentPresence[] {
    // Pick the agent's single current feature: prefer an editing one, then reflecting, then read.
    const editing = Object.keys(phase).find(fid => phase[fid] === 'editing');
    const reflecting = Object.keys(phase).find(fid => phase[fid] === 'reflecting');
    const reading = activeRead[0];
    const fid = editing ?? reflecting ?? reading;
    if (!fid) return [];
    const p: PresencePhase = editing ? 'editing' : reflecting ? 'reflecting' : 'read';
    return [{ role, name: roleName(role), fid, phase: p }];
}

/** Cap the avatar stack at `cap` (§B.1): show the first `cap`, report the overflow as `+N`.
 *  Pure — unit-tested. */
export function agentStack<T>(agents: readonly T[], cap = 3): { visible: T[]; overflow: number } {
    return { visible: agents.slice(0, cap), overflow: Math.max(0, agents.length - cap) };
}

/** The hover tooltip for a multi-agent stack (§B.4): `Claude, Codex working`. */
export function stackTooltip(names: readonly string[]): string {
    return names.length ? `${names.join(', ')} working` : '';
}

/** Off-screen clamp (§B.5): when the target heading is above/below the doc viewport, pin the
 *  avatar to the nearer edge and report which chevron (↑ above / ↓ below) to show. `pad` keeps
 *  it off the very edge. Pure — unit-tested.
 *   - target above viewport → clamp to top, chevron '↑'
 *   - target below viewport → clamp to bottom, chevron '↓'
 *   - in view → the target y, chevron null
 */
export function clampToViewport(
    targetY: number, viewTop: number, viewBottom: number, pad = 8,
): { y: number; chevron: '↑' | '↓' | null } {
    if (targetY < viewTop) return { y: viewTop + pad, chevron: '↑' };
    if (targetY > viewBottom) return { y: viewBottom - pad, chevron: '↓' };
    return { y: targetY, chevron: null };
}

/** A bounding rectangle (the bits we need from getBoundingClientRect). */
export interface Rect { top: number; left: number; right: number; height: number }

/**
 * The avatar's top/left RELATIVE to a non-scrolling overlay parent (the fix for the scroll
 * drift, §B.2/§B.5). The avatar is absolutely positioned inside `.doc-host` — which does NOT
 * scroll — so its offset is the heading's CURRENT viewport position minus the overlay's, i.e.
 * `headingRect.top - overlayRect.top`. Because it reads the LIVE `headingRect.top` (which
 * reflects scroll) against the FIXED `overlayRect.top`, the result tracks the heading smoothly
 * as the doc scrolls — and stays viewport-relative so the off-screen clamp still applies.
 *
 *   `avatarSize` centres the avatar on the heading's mid-line; `gap` offsets the avatar past
 *   the heading's right edge; `maxRight` clamps it inside the overlay so it never overflows.
 *   Pure — unit-tested across scroll positions.
 */
export function overlayAnchor(
    headingRect: Rect, overlayRect: Rect,
    opts: { avatarSize?: number; gap?: number; maxRight?: number } = {},
): { top: number; left: number } {
    const { avatarSize = 16, gap = 8, maxRight = overlayRect.right - overlayRect.left - 28 } = opts;
    const top = headingRect.top - overlayRect.top + headingRect.height / 2 - avatarSize / 2;
    const left = Math.min(headingRect.right - overlayRect.left + gap, maxRight);
    return { top, left };
}
