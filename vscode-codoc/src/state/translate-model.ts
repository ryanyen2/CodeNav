/**
 * translate-model.ts — reading `.codoc/translate.json` (a `codoc translate` run's
 * live progress channel; written per batch by `codoc/loop/translate.py`).
 *
 * Pure and lease-guarded. The CLI finalizes the file (`running: false`) in a
 * `finally`, but a `kill -9` mid-batch leaves `running: true` behind with nobody to
 * clean it up — and a skeleton the UI draws from a dead run's file would lock nodes
 * forever. So `running` is only believed while the file is FRESH: a batch is one
 * LLM call over ≤12 nodes, and a file older than the lease is read as a dead run
 * (its `pending` list is reported empty so no skeleton survives it).
 */
import type { TranslationProgress } from '../webview/protocol';

/** Mirrors `codoc.loop.translate.TRANSLATE_LEASE_S` (parity by value; the Python
 *  side owns the number). */
export const TRANSLATE_LEASE_MS = 300_000;

/** How long a FINISHED run's summary stays interesting to the UI. Beyond this the
 *  payload omits the block entirely — a translation that ended yesterday is not a
 *  status anyone is waiting on. */
export const TRANSLATE_LINGER_MS = 120_000;

export function parseTranslateProgress(
    text: string,
    mtimeMs: number | undefined,
    nowMs: number,
): TranslationProgress | null {
    let raw: unknown;
    try {
        raw = JSON.parse(text);
    } catch {
        return null;
    }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const o = raw as Record<string, unknown>;
    if (typeof o.target !== 'string' || !o.target) return null;

    const age = mtimeMs === undefined ? Number.POSITIVE_INFINITY : nowMs - mtimeMs;
    const claimed = !!o.running;
    const running = claimed && age < TRANSLATE_LEASE_MS;
    // A finished (or lease-expired) run stops being news after a short linger —
    // return null so the payload drops the block and the toolbar goes quiet.
    if (!running && age > TRANSLATE_LINGER_MS) return null;

    const skipped = Array.isArray(o.skipped)
        ? (o.skipped as Record<string, unknown>[]).flatMap(s =>
            s && typeof s === 'object' && typeof s.reason === 'string'
                ? [{
                    feature_id: String(s.feature_id ?? ''),
                    title: String(s.title ?? ''),
                    reason: s.reason,
                }]
                : [])
        : [];
    return {
        running,
        target: o.target,
        targetName: typeof o.target_name === 'string' && o.target_name ? o.target_name : o.target,
        total: typeof o.total === 'number' ? o.total : 0,
        translated: typeof o.translated === 'number' ? o.translated : 0,
        skipped,
        // A dead run's pending list must not skeleton-lock anything.
        pending: running && Array.isArray(o.pending)
            ? (o.pending as unknown[]).filter((x): x is string => typeof x === 'string')
            : [],
    };
}

/**
 * Whether this run should shimmer each node, or just guard them quietly.
 *
 * The per-node skeleton means "this section is being produced". That reads
 * correctly when a few nodes are in flight. It reads wrongly when every node is:
 * translating a tree into a language it has never held puts the whole document in
 * the pending set at once, and the result was every section dimmed and sweeping
 * for the length of the run, with the old prose — still perfectly true, just in
 * the previous language — underneath it. Nothing was gained, because the toolbar
 * already carries the document-level fact ("translating 6/25").
 *
 * Above half the document, the animation drops and only the edit guard stays.
 * The guard is not negotiable at any size: typing into a paragraph that is about
 * to be replaced wholesale merges a keystroke against text that is already gone.
 */
export function shouldQuietSkeleton(tr: TranslationProgress): boolean {
    return tr.total > 0 && tr.pending.length > tr.total / 2;
}
