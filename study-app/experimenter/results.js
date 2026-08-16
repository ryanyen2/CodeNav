// The results view: every figure the paper needs, over the whole cohort.
//
// It recomputes from the raw actions every time it is opened. Nothing in
// Firestore is an authoritative summary, so redefining a measure is a code change
// rather than a lost measurement, and a figure can never be stale relative to the
// data behind it.
//
// Pilots are out by default. A pilot exists to find out that the instrument is
// broken; a study that quietly analyses them has no way left to say so.
import { AFTER_CONDITION, CONSTRUCTS } from '../participant/instrument.js';
import { likert, tally } from '../figures/likert.js';
import { timeShare, timeProfile } from '../figures/timeprofile.js';
import { authorship, provenance } from '../figures/provenance.js';
import { transitionLift, mediation, TRANSITIONS } from '../figures/mediation.js';
import { downloadSvg, downloadPng, downloadCsv } from '../figures/export.js';
import { esc } from '../shared/html.js';

const CONDITIONS = ['codoc', 'baseline'];

/**
 * Build every figure from a cohort.
 *
 * @param cohort [{ code, pilot, excluded, answers: {after-codoc,…}, sessions: {codoc:{actions},…} }]
 */
export function buildFigures(cohort, { includePilots = false } = {}) {
    const people = cohort.filter((p) => includePilots || !p.pilot).filter((p) => !p.excluded);

    const sessions = [];
    for (const p of people) {
        for (const c of CONDITIONS) {
            const s = (p.sessions || {})[c];
            if (s && (s.actions || []).length) {
                sessions.push({ code: p.code, condition: c, actions: s.actions });
            }
        }
    }

    const counts = {};
    for (const c of CONDITIONS) {
        counts[c] = tally(people.map((p) => (p.answers || {})[`after-${c}`]).filter(Boolean),
            AFTER_CONDITION);
    }

    // The per-participant ratings, which the counts have lost: they no longer
    // say which answer belonged to whom, and pairing needs exactly that.
    const ratings = [];
    for (const p of people) {
        for (const c of CONDITIONS) {
            const a = (p.answers || {})[`after-${c}`];
            if (!a) continue;
            for (const q of AFTER_CONDITION) {
                if (typeof a[q.id] === 'number') {
                    ratings.push({ code: p.code, condition: c, item: q.id, value: a[q.id] });
                }
            }
        }
    }

    const lift = {};
    for (const c of CONDITIONS) {
        lift[c] = transitionLift(sessions.filter((s) => s.condition === c));
    }

    const minutes = (s) => {
        const a = s.actions;
        if (a.length < 2) return null;
        return Math.round((a[a.length - 1].t - a[0].t) / 60000);
    };
    const med = (xs) => {
        const v = xs.filter((x) => x != null).sort((a, b) => a - b);
        return v.length ? v[Math.floor(v.length / 2)] : null;
    };

    return {
        n: people.length,
        sessions,
        figures: {
            likert: () => likert({
                items: AFTER_CONDITION, conditions: CONDITIONS, counts, points: 7, ratings,
            }),
            timeprofile: () => timeProfile(CONDITIONS.map((c) => {
                const of = sessions.filter((s) => s.condition === c);
                return {
                    condition: c, n: of.length,
                    medianMinutes: med(of.map(minutes)),
                    profile: timeShare(of),
                };
            })),
            provenance: () => provenance(authorship(sessions)),
            mediation: () => mediation(lift),
        },
        // The numbers behind each figure, so a reader can check the arithmetic.
        data: {
            likert: AFTER_CONDITION.flatMap((q) => CONDITIONS.flatMap((c) =>
                (counts[c][q.id] || []).map((n, i) => ({
                    item: q.id, text: q.text, reverse: !!q.reverse,
                    construct: q.c, condition: c, point: i + 1, n,
                })))),
            provenance: authorship(sessions),
            mediation: CONDITIONS.flatMap((c) => lift[c].map((r) => ({
                condition: c, from: r.from, to: r.to, label: r.label,
                sessions: r.n, meanObserved: r.obs, lift: r.lift,
            }))),
        },
    };
}

const FIGURE_TITLES = {
    likert: 'What people said',
    timeprofile: 'Where the time went',
    provenance: 'Who wrote what',
    mediation: 'Which moves follow which',
};

const FIGURE_NOTES = {
    likert: 'Counts on the left, because a mean of a seven point scale hides a split. The paired difference on the right, because reading one off two stacked bars by eye is guesswork. R marks an item where agreeing is the bad direction.',
    timeprofile: 'Each session stretched onto a common start-to-end axis, so a forty minute session and a seventy minute one can be compared. Absolute duration is gone; the median is in the panel title.',
    provenance: 'One dot per session, with the mean and its interval. The claim is that the description is written by both parties, and this shows both ways that can fail: one only the agent touches, or one only the person does.',
    mediation: 'Whether a move follows another more often than the two moves’ own rates predict. Writing to a description faithfully and never consulting it would show up here and nowhere else.',
};

/** Render the whole view into an element. */
export function renderResults(el, cohort, state = {}) {
    const includePilots = !!state.includePilots;
    const built = buildFigures(cohort, { includePilots });

    el.innerHTML = `
      <div class="detail-head">
        <h2>Results</h2>
        <span class="sub">${built.n} ${built.n === 1 ? 'person' : 'people'},
          ${built.sessions.length} ${built.sessions.length === 1 ? 'session' : 'sessions'}</span>
      </div>
      <div class="results-bar">
        <label><input type="checkbox" id="inc-pilots" ${includePilots ? 'checked' : ''}>
          Include pilots</label>
        <span class="hint">${includePilots
            ? 'Pilots are in. Turn this off before anything that goes in the paper.'
            : 'Pilots are out, which is what the paper reports.'}</span>
      </div>
      ${built.sessions.length === 0 ? `<div class="empty">
        <strong>Nothing to draw yet</strong>
        Figures appear as sessions arrive. Every one is recomputed from the raw
        actions each time this view opens, so none of them can go stale.
      </div>` : Object.keys(FIGURE_TITLES).map((k) => `
        <div class="card fig" data-fig="${k}">
          <h3>${esc(FIGURE_TITLES[k])}</h3>
          <p class="hint">${FIGURE_NOTES[k]}</p>
          <div class="fig-holder"></div>
          <div class="fig-actions">
            <button data-dl="svg">SVG</button>
            <button data-dl="png">PNG</button>
            <button data-dl="csv">CSV</button>
          </div>
        </div>`).join('')}`;

    const inc = el.querySelector('#inc-pilots');
    if (inc) inc.onchange = () => renderResults(el, cohort, { ...state, includePilots: inc.checked });

    for (const card of el.querySelectorAll('.fig')) {
        const key = card.dataset.fig;
        let node;
        try {
            node = built.figures[key]();
            card.querySelector('.fig-holder').append(node);
        } catch (err) {
            // One figure that cannot be drawn must not take the other three with
            // it, and least of all mid-session.
            card.querySelector('.fig-holder').innerHTML =
                `<div class="notice">This one could not be drawn: ${esc(err.message)}</div>`;
        }
        for (const b of card.querySelectorAll('[data-dl]')) {
            b.onclick = () => {
                const stamp = new Date().toISOString().slice(0, 10);
                if (b.dataset.dl === 'svg') downloadSvg(node, `${key}-${stamp}.svg`);
                else if (b.dataset.dl === 'png') void downloadPng(node, `${key}-${stamp}.png`);
                else downloadCsv(built.data[key] || [], `${key}-${stamp}.csv`);
            };
        }
    }
    return built;
}

export { CONSTRUCTS, TRANSITIONS };
