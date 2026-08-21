/**
 * provenance.ts — "why does this say what it says?", as rows (W8).
 *
 * The change ledger has always held the whole chain: a change cites the directive that
 * asked for it, a directive quotes the prompt a person typed, that prompt names the
 * session it was typed in, and the directive records the commit its code work started
 * from. Every link existed; nothing ever showed them together, so the answer to "who
 * asked for this sentence?" was a `codoc history` call, a `realize.md` read, and a
 * guess.
 *
 * One link was still missing, and it was the one a reader actually needs: the chain says
 * what happened BEFORE a claim, never what the claim is WARRANTED by. A "Rests on" row
 * closes it, quoting the commit message, request or earlier note that licensed the stated
 * reason (`codoc/loop/warrant.py`). Its absence is meaningful and common — a description
 * that reports what code achieves is making no claim that needs a warrant.
 *
 * This module assembles that chain once, for two callers that ask it from opposite
 * ends — the timeline (about a moment in the tree's history) and the History stance's
 * per-feature label (about this paragraph, right here). Pure: no DOM, no vscode, so
 * both the wording and the omissions are unit-tested.
 *
 * The one editorial rule: a row is omitted rather than shown empty. A card that says
 * "Session: —" teaches the reader the field is noise, and then they stop reading the
 * rows that do carry something.
 */
import type { HistoryEntry, WarrantRow } from './bindings-model';
import { actorLabel, kindPhrase, relativeTime } from './blame-model';
import { filesTouched, type Moment, type RevisionDirective, type Timeline } from './revision-model';

export interface TraceRow { label: string; value: string }

/** The chain behind a directive: what it implements, who asked, where, from what. */
function directiveRows(d: RevisionDirective): TraceRow[] {
    const rows: TraceRow[] = [
        { label: 'Implements', value: d.done ? 'a completed request' : 'a queued request' },
    ];
    if (d.asked) rows.push({ label: 'You asked', value: `“${d.asked}”` });
    if (d.session_id) rows.push({ label: 'Session', value: d.session_id });
    if (d.base_sha) rows.push({ label: 'From commit', value: d.base_sha.slice(0, 8) });
    return rows;
}

/** How each evidence kind is named to a reader. Phrased as a ground rather than as a
 *  source table ("Rests on commit …", not "commit"), because the row's job is to say
 *  what the sentence above it stands on. */
const GROUND: Record<string, string> = {
    intent: 'Rests on your ask',
    directive: 'Rests on a request',
    commit: 'Rests on commit',
    prior: 'Rests on an earlier note',
};

/** One row per piece of evidence the stated why rests on — the answer to "should I
 *  believe that reason?", which the chain rows never gave.
 *
 *  Rendered right after `Why`, because the two are one thought: the reason, then its
 *  ground. Nothing is drawn when a change has no warrant, and that is the common case
 *  by design — most descriptions report what the code achieves and make no claim about
 *  a decision, so a `Rests on: —` row would turn the ordinary into a defect. */
function warrantRows(warrant: WarrantRow[] | undefined): TraceRow[] {
    return (warrant ?? [])
        .filter(w => !!w?.quote)
        .map(w => {
            const base = GROUND[w.kind] ?? 'Rests on';
            // The ref belongs in the LABEL, not the value: a sha is an address the
            // reader may go type, and burying it in front of the quotation makes them
            // read past it to reach the words that matter.
            const label = w.kind === 'commit' && w.ref ? `${base} ${w.ref}` : base;
            return { label, value: `“${w.quote}”` };
        });
}

/** The cause is recorded but its directive has aged out of the bounded logs.
 *
 *  Said explicitly rather than dropped: "we know this had a reason and no longer have
 *  it" is a different fact from "this had no reason", and the logs are capped, so the
 *  first one is routine rather than exotic. */
function forgottenCause(causedBy: string): TraceRow {
    return { label: 'Implements', value: `${causedBy} (details no longer kept)` };
}

/** The trace for one moment on the timeline. */
export function momentTrace(moment: Moment, timeline: Timeline): TraceRow[] {
    const rows: TraceRow[] = [];
    // One moment can hold several ops. The ground has to come from the SAME op that
    // supplied the reason above it — pairing a Why row with a neighbouring op's warrant
    // would offer evidence for a claim it was never offered for, which is worse than
    // showing no ground at all. Only when nothing recorded a reason does a warrant from
    // any op in the moment stand on its own.
    const said = moment.entries.find(e => !!e.rationale);
    if (said?.rationale) rows.push({ label: 'Why', value: said.rationale });
    const grounded = said ?? moment.entries.find(e => e.warrant?.length);
    rows.push(...warrantRows(grounded?.warrant));

    const d = moment.causedBy ? timeline.directives[moment.causedBy] : undefined;
    if (d) rows.push(...directiveRows(d));
    else if (moment.causedBy) rows.push(forgottenCause(moment.causedBy));

    const files = filesTouched(moment);
    if (files.length) rows.push({ label: 'Code', value: files.join(', ') });
    return rows;
}

/**
 * The trace for ONE feature, from its blame history — the answer to "who last changed
 * this paragraph, and why", asked at the paragraph.
 *
 * Only the newest entry's cause is chased. A feature's history is a list of separate
 * changes, each with its own reason; stacking four directives into one card would
 * present them as though they explained one thing.
 */
export function featureTrace(
    history: HistoryEntry[],
    directives: Record<string, RevisionDirective>,
    nowMs: number = Date.now(),
): TraceRow[] {
    if (!history.length) return [];
    const [latest] = history;
    const when = relativeTime(latest.at, nowMs);
    const rows: TraceRow[] = [{
        label: 'Last change',
        value: `${actorLabel(latest.actor)} ${kindPhrase(latest.kind)}${when ? ` · ${when}` : ''}`,
    }];
    if (latest.rationale) rows.push({ label: 'Why', value: latest.rationale });
    rows.push(...warrantRows(latest.warrant));
    const d = latest.caused_by ? directives[latest.caused_by] : undefined;
    if (d) rows.push(...directiveRows(d));
    else if (latest.caused_by) rows.push(forgottenCause(latest.caused_by));
    // How much history is behind this one, so the card admits it is showing the tip of
    // something rather than the whole record.
    if (history.length > 1) {
        rows.push({ label: 'Earlier', value: `${history.length - 1} more recorded change${history.length > 2 ? 's' : ''}` });
    }
    return rows;
}

/** The base commit to diff a feature's code against, or `''` when nothing recorded one. */
export function traceBaseSha(
    history: HistoryEntry[], directives: Record<string, RevisionDirective>,
): string {
    for (const e of history) {
        const d = e.caused_by ? directives[e.caused_by] : undefined;
        if (d?.base_sha) return d.base_sha;
    }
    return '';
}
