/**
 * edit-origin.ts — where every document change declares WHO caused it.
 *
 * The tracking engine's recurring failure mode has been deciding authorship
 * after the fact, by inspecting the document's shape. That answer is not
 * recoverable from a snapshot: a projection load, an authorship stamp, an id
 * mint and a keystroke all arrive as ordinary transactions, and by the time
 * anything diffs the result they are indistinguishable.
 *
 * So origin is DECLARED at dispatch, on the transaction, and read from there.
 * These metas are that declaration, and `isUserInput` is the single predicate
 * every consumer uses — one definition, so a plugin cannot quietly disagree
 * with another about whether a span was typed by a person.
 *
 * They live in a leaf module of their own because the plugins that declare an
 * origin and the plugins that read one would otherwise import each other.
 */
import type { Transaction } from '@tiptap/pm/state';

/** Our own authorship-stamp pass. */
export const AUTHOR_META = 'codocAuthorStamp';
/** A projection/system load: the daemon's doc, an id mint, a migration. */
export const REFLECT_META = 'codocReflect';
/** The mark-hygiene cleanup pass. */
export const MARK_HYGIENE_META = 'codocMarkHygiene';
/** A whole-feature move (drag / keyboard nudge). Structural, not typing: the
 *  moved slice must keep its existing authorship and any agent proposal marks —
 *  re-stamping it as the dragger's prose (or stripping the agent's marks into
 *  plain text) would silently resolve a proposal no one accepted. */
export const FEATURE_MOVE_META = 'codocFeatureMove';

const SYSTEM_METAS = [AUTHOR_META, REFLECT_META, MARK_HYGIENE_META, FEATURE_MOVE_META] as const;

/**
 * True when this transaction represents a person editing — the only kind whose
 * spans get authorship, and the only kind that may not carry agent marks.
 *
 * Deliberately a denylist of declared system origins rather than an allowlist of
 * known input types: a transaction nobody tagged is far more likely to be user
 * input reaching us through a path we did not anticipate (paste, drop, IME,
 * a browser input event) than a system pass someone forgot to tag. Guessing
 * "user" there attributes it to the person and keeps their text safe; guessing
 * "system" would silently drop it from authorship and hygiene both.
 */
export function isUserInput(tr: Transaction): boolean {
    return !SYSTEM_METAS.some(meta => tr.getMeta(meta));
}
