// The planned cohort, and where a participant sits in it.
//
// Written down as a plan rather than counted from whoever happens to exist. Two
// pilots and twelve participants, and the twelve are four repeats of the four
// combinations of order and project pairing, so the design is balanced by
// construction instead of by watching a tally drift and correcting it at the end.
//
// The slots exist before anybody is created. A dashboard that only lists what
// exists cannot show you the gap you are trying to fill, and "how many more do I
// need, in which order" is the question a researcher actually has mid-study.

import { isPilotCode } from './schema.js';

/**
 * A pilot, by the code or by the flag.
 *
 * Either alone is enough. The flag is what the dashboard writes; the prefix is
 * what survives an export, a CSV and a zip. Trusting both means a record that
 * lost one is still handled correctly rather than quietly analysed.
 */
export const isPilot = (p) => !!(p && (p.pilot || isPilotCode(p.code)));

export const PILOTS = 2;
export const PARTICIPANTS = 12;

export const ORDERS = Object.freeze(['codoc-first', 'baseline-first']);

/**
 * The plan, in the order slots should be filled.
 *
 * Orders alternate rather than running in blocks. If recruitment stops early —
 * and it usually does — an alternating plan leaves a balanced half, and a
 * blocked one leaves every remaining participant in the same condition first.
 */
export function plan({ pilots = PILOTS, participants = PARTICIPANTS } = {}) {
    pilots = Math.max(0, pilots);
    participants = Math.max(0, participants);
    const slots = [];
    for (let i = 0; i < pilots; i += 1) {
        slots.push({
            n: i + 1,
            kind: 'pilot',
            label: `Pilot ${i + 1}`,
            order: ORDERS[i % ORDERS.length],
        });
    }
    for (let i = 0; i < participants; i += 1) {
        slots.push({
            n: i + 1,
            kind: 'participant',
            label: `P${String(i + 1).padStart(2, '0')}`,
            order: ORDERS[i % ORDERS.length],
        });
    }
    return slots;
}

/**
 * The plan with whoever exists placed into it.
 *
 * Existing participants are matched to slots of their own kind in the order they
 * were created, so a slot's label stays with the same person for the life of the
 * study even when somebody is excluded later.
 */
export function fill(existing, opts = {}) {
    // The plan grows to hold whoever exists. Two pilots is the intention, not a
    // limit: a study that turns out to need a third should not have to edit a
    // constant, and the third would otherwise land in the "beyond the plan" pile
    // where nothing counts it.
    const counts = { pilot: 0, participant: 0 };
    for (const p of existing) counts[p.pilot || isPilotCode(p.code) ? 'pilot' : 'participant'] += 1;
    const grown = {
        pilots: Math.max(opts.pilots ?? PILOTS, counts.pilot),
        participants: Math.max(opts.participants ?? PARTICIPANTS, counts.participant),
    };
    const slots = plan(grown).map((s) => ({ ...s, participant: null }));
    const byKind = { pilot: [], participant: [] };
    for (const p of [...existing].sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))) {
        byKind[isPilot(p) ? 'pilot' : 'participant'].push(p);
    }
    const extra = [];
    for (const kind of ['pilot', 'participant']) {
        const open = slots.filter((s) => s.kind === kind);
        byKind[kind].forEach((p, i) => {
            // More people than slots is not an error worth blocking on mid-study,
            // but it must be visible rather than silently dropped off the end.
            if (i < open.length) open[i].participant = p;
            else extra.push(p);
        });
    }
    return { slots, extra };
}

/** Which order the next participant of this kind should get. */
export function nextOrder(existing, kind = 'participant') {
    const { slots } = fill(existing);
    const open = slots.find((s) => s.kind === kind && !s.participant);
    if (open) return open.order;
    // Past the plan: keep the halves even rather than repeating the last one.
    const counts = { 'codoc-first': 0, 'baseline-first': 0 };
    for (const p of existing) if (p.order in counts) counts[p.order] += 1;
    return counts['codoc-first'] <= counts['baseline-first'] ? 'codoc-first' : 'baseline-first';
}

/**
 * What is done, what is open, and what is unbalanced.
 *
 * `analysable` excludes pilots and anyone marked excluded. Pilots are for
 * finding out that the instrument is broken, and a study that quietly analyses
 * them has no way left to say so.
 */
export function progress(existing) {
    const { slots, extra } = fill(existing);
    const count = (kind, pred) =>
        slots.filter((s) => s.kind === kind && s.participant && pred(s.participant)).length;

    const analysable = existing.filter((p) => !isPilot(p) && !p.excluded);
    const byOrder = { 'codoc-first': 0, 'baseline-first': 0 };
    for (const p of analysable) if (p.order in byOrder) byOrder[p.order] += 1;

    return {
        pilots: { filled: count('pilot', () => true), of: slots.filter((s) => s.kind === 'pilot').length },
        participants: {
            filled: count('participant', () => true),
            of: slots.filter((s) => s.kind === 'participant').length,
        },
        excluded: existing.filter((p) => p.excluded).length,
        analysable: analysable.length,
        byOrder,
        // The thing that quietly goes wrong: excluding two people who happened to
        // share an order leaves the design unbalanced and nothing says so.
        imbalance: Math.abs(byOrder['codoc-first'] - byOrder['baseline-first']),
        extra: extra.length,
    };
}
