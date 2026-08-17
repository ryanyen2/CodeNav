// The closed vocabulary a session is written in, and the mapping into it.
//
// Why a fixed list at all. The raw logs hold a few thousand events of a dozen
// shapes. That is the right form for "did they look at this change", which the
// analysis plan already answers from it. It is the wrong form for "what patterns
// do people follow", because counting recurring sequences needs a small alphabet,
// and no amount of post-processing turns arbitrary events into one. So the
// alphabet is designed here, at write time, and anything that does not map is
// dropped rather than given a name like OTHER that would swallow the tail.
//
// The list is split in two. The shared level exists in both conditions and is the
// only level a cross-condition comparison may use. The codoc level exists in one
// condition only: counting accepts against a condition that cannot accept would
// show a difference that is an artifact of the tool rather than of behaviour.

/** Present in both conditions. Cross-condition comparison uses these only. */
export const SHARED = Object.freeze([
    'READ_DOC',    // looked at the written description
    'READ_CODE',   // looked at source
    'READ_TEST',   // looked at a test
    'EDIT_DOC',    // changed the description by hand
    'EDIT_CODE',   // changed source by hand
    'EDIT_TEST',   // changed a test by hand
    'PROMPT',      // sent an instruction to the agent
    'AGENT_EDIT',  // the agent changed source or a test
    'AGENT_DOC',   // the agent changed the description
    'RUN_TEST',    // ran the tests
    'RUN_BUILD',   // built or ran the project
    'IDLE',        // a gap long enough that the thread was dropped
]);

/**
 * Present in the codoc condition only. Reviewing is deliberately not here.
 * Seeing which proposal was opened would mean instrumenting codoc's own editor,
 * and nothing in this study changes the tool being studied. Looking at the
 * description is READ_DOC in both conditions; the verdict is what differs.
 *
 * ASK is `/codoc:ask` — the participant asked the agent a question and it drew the
 * answer as a numbered reading path over the tree (`.codoc/ask.json`). It is here,
 * not in SHARED, because only the codoc arm can produce it: the baseline has no
 * such surface, so counting it across conditions would compare a thing one side
 * cannot do. The PROMPT that triggered it is SHARED and is counted on both sides;
 * ASK records only that the answer landed as a walkthrough, reported within the
 * codoc arm the way ACCEPT/REJECT are.
 */
export const CODOC_ONLY = Object.freeze(['ACCEPT', 'REJECT', 'ASK']);

export const ACTIONS = Object.freeze([...SHARED, ...CODOC_ONLY]);

const SHARED_SET = new Set(SHARED);
export const isShared = (action) => SHARED_SET.has(action);

/** Defaults, all overridable so the pilots can set them from real data. */
export const DEFAULTS = Object.freeze({
    idleGapMs: 60_000,    // a gap this long becomes one IDLE, not many
    coalesceMs: 5_000,    // consecutive edits to one file inside this are one action
});

// ── the mapping ──────────────────────────────────────────────────────────────

const READ_BY_SURFACE = { document: 'READ_DOC', code: 'READ_CODE', test: 'READ_TEST' };
const EDIT_BY_SURFACE = { document: 'EDIT_DOC', code: 'EDIT_CODE', test: 'EDIT_TEST' };

const TEST_COMMANDS = /^(pytest|vitest|jest)$/;
const BUILD_COMMANDS = /^(scribe|tally|make|npm|node|python|python3)$/;

/**
 * One raw event to one action, or nothing.
 *
 * Returns `{ t, a, ...detail }` where `t` is when the action *started*. A view
 * event arrives when the file left the screen, so its time is wound back by how
 * long it was up; otherwise every look would be filed at the moment it ended and
 * the order of a sequence would be wrong.
 */
export function mapEvent(raw) {
    if (!raw || typeof raw !== 'object') return null;

    switch (raw.ev) {
        // Looking. `view` is used rather than `focus` because it carries how long,
        // and because using both would count every look twice.
        case 'view': {
            const a = READ_BY_SURFACE[raw.surface];
            if (!a) return null;
            const ms = raw.ms || 0;
            return { t: raw.t - ms, a, file: raw.file, ms, from: raw.from, to: raw.to };
        }

        // Changing. Whether the editor was the active one is what separates a
        // person typing from a file being rewritten underneath them.
        case 'edit': {
            const human = !!raw.active && !!raw.focused;
            if (human) {
                const a = EDIT_BY_SURFACE[raw.surface];
                if (!a) return null;
                return { t: raw.t, a, file: raw.file, added: raw.added || 0, removed: raw.removed || 0 };
            }
            const a = raw.surface === 'document' ? 'AGENT_DOC' : 'AGENT_EDIT';
            if (raw.surface !== 'document' && !EDIT_BY_SURFACE[raw.surface]) return null;
            return { t: raw.t, a, file: raw.file, added: raw.added || 0, removed: raw.removed || 0 };
        }

        // Running something. Only the two kinds the study cares about map.
        case 'agent': {
            const cmd = (raw.cmd || '').split('/').pop();
            if (TEST_COMMANDS.test(cmd)) return { t: raw.t, a: 'RUN_TEST' };
            if (BUILD_COMMANDS.test(cmd)) return { t: raw.t, a: 'RUN_BUILD' };
            return null;
        }

        // Instructing the agent. From the study's own prompt hook, which is
        // installed in both conditions.
        case 'prompt':
            return {
                t: raw.t, a: 'PROMPT',
                chars: raw.chars || 0, words: raw.words || 0, lines: raw.lines || 0,
            };

        // A `/codoc:ask` walkthrough was drawn (`.codoc/ask.json` appeared). Only
        // the codoc arm produces this file, so the action is codoc-only and never
        // enters a cross-condition comparison. `steps` is how many stops the path
        // drew — a count, never the question text.
        case 'ask':
            return { t: raw.t, a: 'ASK', steps: raw.steps || 0 };

        // From codoc's change ledger. In the codoc condition the description is
        // edited through a custom editor, so no text edit ever reaches the logger
        // and this is the only place those edits appear. In the other condition
        // the same act is an ordinary text edit and arrives as `edit` above.
        case 'codoc': {
            if (raw.kind === 'verdict') {
                return raw.accept ? { t: raw.t, a: 'ACCEPT', eventId: raw.eventId }
                    : { t: raw.t, a: 'REJECT', eventId: raw.eventId };
            }
            if (raw.kind !== 'amend' && raw.kind !== 'add_node'
                && raw.kind !== 'move_node' && raw.kind !== 'retire_node') return null;
            return raw.actor === 'human'
                ? { t: raw.t, a: 'EDIT_DOC', feature: raw.feature }
                : { t: raw.t, a: 'AGENT_DOC', feature: raw.feature };
        }

        default:
            return null;   // closed on purpose
    }
}

// ── events to a sequence ─────────────────────────────────────────────────────

/**
 * Map, order, join repeats, and mark the gaps.
 *
 * Two reductions matter. Typing produces an edit event every few characters, so
 * consecutive edits of one kind to one file inside `coalesceMs` become a single
 * action carrying the totals; without that, one paragraph of typing would out-
 * number everything else in the sequence. And a gap over `idleGapMs` becomes
 * exactly one IDLE, not one per second, so a break reads as a break.
 */
export function toSequence(rawEvents, opts = {}) {
    const { idleGapMs, coalesceMs } = { ...DEFAULTS, ...opts };

    const mapped = [];
    for (const raw of rawEvents || []) {
        const a = mapEvent(raw);
        if (a) mapped.push(a);
    }
    mapped.sort((x, y) => x.t - y.t);

    const joined = [];
    for (const a of mapped) {
        const last = joined[joined.length - 1];
        const sameRun = last && last.a === a.a && last.file === a.file
            && a.t - (last.t + (last.spanMs || 0)) <= coalesceMs;
        if (sameRun) {
            last.spanMs = (a.t - last.t) + (a.ms || 0);
            last.added = (last.added || 0) + (a.added || 0);
            last.removed = (last.removed || 0) + (a.removed || 0);
            last.count = (last.count || 1) + 1;
            if (a.from != null) last.from = Math.min(last.from ?? a.from, a.from);
            if (a.to != null) last.to = Math.max(last.to ?? a.to, a.to);
        } else {
            joined.push({ ...a, spanMs: a.ms || 0, count: 1 });
        }
    }

    const out = [];
    for (const a of joined) {
        const last = out[out.length - 1];
        if (last) {
            const gap = a.t - (last.t + (last.spanMs || 0));
            if (gap >= idleGapMs) out.push({ t: last.t + (last.spanMs || 0), a: 'IDLE', ms: gap });
        }
        out.push(a);
    }
    return out;
}

/** Just the action names, which is what pattern counting reads. */
export function toLetters(sequence) {
    return (sequence || []).map((s) => s.a);
}

/**
 * Drop the codoc-only actions. Any comparison between the two conditions must
 * run through this first, or it counts an act one side cannot perform.
 */
export function sharedOnly(sequence) {
    return (sequence || []).filter((s) => isShared(s.a));
}
