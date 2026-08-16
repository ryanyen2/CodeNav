// Figure: is the description in the loop, or beside it?
//
// The previous figure counts writes. Writing to a thing is not the same as
// working through it: someone can keep a description perfectly current and never
// once consult it before instructing the agent, and that description is a
// logbook rather than a shared representation.
//
// So this counts named transitions instead of actions, and scores each one by
// how much more often it happens than the two actions' own rates would predict.
// That is what makes it a claim about sequence: READ_DOC is common and PROMPT is
// common, so the pair is common by arithmetic alone, and only the excess says
// anything about whether one leads to the other.
//
// The transitions are chosen in advance and written down here, not mined. Mining
// a dozen sessions for the strongest bigram and reporting it is how a study finds
// something that will not replicate.
//
// Each one carries how far ahead it looks. The pilot is why: a session that read
// the description before every single instruction scored as AVOIDING that pattern,
// because what it actually did was read, then write, then instruct — so the
// strictly adjacent pair never occurred. "Did consulting inform the instruction"
// is a question about influence, not about immediate succession, and measuring it
// as adjacency answers a different question convincingly and wrongly. The windows
// are set here, before the data, and shown on the figure.
import {
    el, text, svg, INK, RULE, MUTED, TYPE, CONDITION_LABEL, CONDITION_COLOR,
} from './theme.js';

/**
 * The transitions the study is about, each with the reading that makes it
 * interesting. Every one is in the shared vocabulary, so both conditions can
 * produce it and the comparison is about behaviour rather than about which tool
 * has which button.
 */
export const TRANSITIONS = Object.freeze([
    { from: 'READ_DOC', to: 'PROMPT', within: 3,
      label: 'Consulted the description, then instructed the agent',
      reading: 'The description informing what is asked for. Windowed, because a glance at the code or a note written down in between does not undo the consultation.' },
    { from: 'EDIT_DOC', to: 'PROMPT', within: 2,
      label: 'Wrote intent down, then handed it off',
      reading: 'The description used as the instruction itself.' },
    { from: 'AGENT_EDIT', to: 'READ_DOC', within: 3,
      label: 'Agent changed code, then they read the description',
      reading: 'Checking the description still holds after a change.' },
    { from: 'AGENT_EDIT', to: 'READ_CODE', within: 3,
      label: 'Agent changed code, then they read the code',
      reading: 'Checking the change itself. The alternative to the row above, and windowed the same way so the two are comparable.' },
    { from: 'AGENT_EDIT', to: 'RUN_TEST', within: 3,
      label: 'Agent changed code, then they ran the tests',
      reading: 'Checking by execution rather than by reading.' },
    { from: 'PROMPT', to: 'AGENT_EDIT', within: 1,
      label: 'Instructed, then the agent acted',
      reading: 'The plain handoff, and the only row that is genuinely about immediate succession.' },
    { from: 'READ_DOC', to: 'READ_CODE', within: 2,
      label: 'Read the description, then the code',
      reading: 'The description used to navigate.' },
]);

/**
 * Observed count, expected count under independence, and log2 lift.
 *
 * Computed per session and averaged, so one long session cannot carry a
 * transition on its own.
 */
export function transitionLift(sessions, transitions = TRANSITIONS) {
    const per = transitions.map(() => []);

    for (const s of sessions) {
        // IDLE is dropped rather than treated as an action. A gap between two
        // moves does not break the relationship between them, and leaving it in
        // would split every pair that happened either side of a coffee.
        const seq = (s.actions || []).map((a) => a.a).filter((a) => a !== 'IDLE');
        if (seq.length < 3) continue;
        const total = seq.length;
        const freq = {};
        for (const a of seq) freq[a] = (freq[a] || 0) + 1;
        const pairs = total - 1;

        transitions.forEach((t, i) => {
            // A transition whose parts never occurred is not evidence of
            // anything, so it contributes nothing rather than a zero that would
            // drag the average toward "no effect".
            if (!freq[t.from] || !freq[t.to]) return;
            const w = t.within || 1;

            // Counted once per occurrence of `from`, whether the window holds one
            // `to` or three. Otherwise a burst of agent edits would score as
            // several separate checks of the same change.
            let opportunities = 0;
            let obs = 0;
            for (let k = 0; k < seq.length - 1; k += 1) {
                if (seq[k] !== t.from) continue;
                opportunities += 1;
                for (let j = k + 1; j <= Math.min(k + w, seq.length - 1); j += 1) {
                    if (seq[j] === t.to) { obs += 1; break; }
                }
            }
            if (!opportunities) return;

            // Under independence, the chance that a window of w holds at least
            // one `to`. Using w times p would over-count, and over-counting the
            // expectation makes a real effect look like none.
            const pTo = freq[t.to] / total;
            const expected = opportunities * (1 - (1 - pTo) ** w);
            per[i].push({
                obs,
                expected,
                // +0.5 on both sides so a single absent transition is a strong
                // negative rather than negative infinity.
                lift: Math.log2((obs + 0.5) / (expected + 0.5)),
                rate: obs / opportunities,
            });
        });
    }

    const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    return transitions.map((t, i) => ({
        ...t,
        n: per[i].length,
        lift: mean(per[i].map((x) => x.lift)),
        obs: mean(per[i].map((x) => x.obs)),
        rate: mean(per[i].map((x) => x.rate)),
    }));
}

/**
 * @param byCondition { [condition]: output of transitionLift }
 */
export function mediation(byCondition, opts = {}) {
    const conditions = opts.conditions || ['codoc', 'baseline'];
    const rows = byCondition[conditions[0]] || [];

    const labelW = opts.labelW || 260;
    const plotW = opts.plotW || 200;
    const rowH = 30;
    const padL = 8;
    const padT = 34;
    const padB = 40;

    const width = padL + labelW + plotW + 20;
    const height = padT + rows.length * rowH + padB;
    const root = svg(width, height, opts.title || 'Which moves follow which');

    const all = conditions.flatMap((c) => (byCondition[c] || []).map((r) => r.lift))
        .filter((v) => v != null);
    const bound = Math.max(1, ...all.map((v) => Math.abs(v)));
    const zero = padL + labelW + plotW / 2;
    const x = (v) => zero + (v / bound) * (plotW / 2);

    root.append(text('less often than chance', {
        x: padL + labelW, y: padT - 18, 'font-size': TYPE.caption, fill: MUTED,
    }));
    root.append(text('more often', {
        x: padL + labelW + plotW, y: padT - 18, 'text-anchor': 'end',
        'font-size': TYPE.caption, fill: MUTED,
    }));

    rows.forEach((row, ri) => {
        const y = padT + ri * rowH;
        root.append(text(row.label, {
            x: padL + labelW - 28, y: y + rowH / 2 + 3.5,
            'text-anchor': 'end', 'font-size': TYPE.label, fill: INK,
        }));
        // How far ahead this row looked. Without it the reader cannot tell an
        // adjacent pair from a windowed one, and they mean different things.
        root.append(text(`≤${row.within || 1}`, {
            x: padL + labelW - 8, y: y + rowH / 2 + 3.5,
            'text-anchor': 'end', 'font-size': TYPE.caption, fill: MUTED,
        }));
        conditions.forEach((c, ci) => {
            const r = (byCondition[c] || [])[ri];
            if (!r || r.lift == null) return;
            const barY = y + 6 + ci * 9;
            const w = Math.abs(x(r.lift) - zero);
            root.append(el('rect', {
                x: r.lift >= 0 ? zero : zero - w, y: barY, width: Math.max(w, 0.6), height: 7,
                fill: CONDITION_COLOR[c],
            }));
        });
    });

    // The zero line last, so it sits over the bars and the eye finds it.
    root.append(el('line', {
        x1: zero, y1: padT - 6, x2: zero, y2: padT + rows.length * rowH + 2,
        stroke: INK, 'stroke-width': 1,
    }));
    root.append(el('line', {
        x1: padL + labelW, y1: padT + rows.length * rowH + 2,
        x2: padL + labelW + plotW, y2: padT + rows.length * rowH + 2, stroke: RULE,
    }));
    root.append(text('log2 of observed over expected, within the steps shown', {
        x: zero, y: padT + rows.length * rowH + 16,
        'text-anchor': 'middle', 'font-size': TYPE.tick, fill: MUTED,
    }));

    const key = el('g', { transform: `translate(${padL + labelW},${padT + rows.length * rowH + 26})` });
    conditions.forEach((c, i) => {
        key.append(el('rect', { x: i * 90, y: 0, width: 9, height: 7, fill: CONDITION_COLOR[c] }));
        key.append(text(CONDITION_LABEL[c] || c, {
            x: i * 90 + 13, y: 7, 'font-size': TYPE.caption, fill: INK,
        }));
    });
    root.append(key);

    return root;
}
