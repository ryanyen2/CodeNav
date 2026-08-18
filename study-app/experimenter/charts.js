// Every visual on the dashboard.
//
// The session timeline is the one that matters. It answers, at a glance, what
// somebody spent a session doing and whether anything is still arriving. Reading
// actions have a duration so they are bars; the rest are moments so they are
// ticks. A long gap is drawn rather than closed up, because a gap is a finding.
import * as d3 from 'd3';

const LANES = [
    { key: 'doc', label: 'Description', actions: ['READ_DOC', 'EDIT_DOC', 'AGENT_DOC'] },
    { key: 'code', label: 'Code', actions: ['READ_CODE', 'EDIT_CODE', 'AGENT_EDIT'] },
    { key: 'test', label: 'Tests', actions: ['READ_TEST', 'EDIT_TEST'] },
    { key: 'output', label: 'Samples & output', actions: ['READ_OUTPUT', 'EDIT_OUTPUT'] },
    { key: 'agent', label: 'Agent', actions: ['PROMPT', 'ASK', 'ACCEPT', 'REJECT'] },
    { key: 'run', label: 'Runs', actions: ['RUN_TEST', 'RUN_BUILD'] },
];

const COLOR = {
    doc: 'var(--doc)', code: 'var(--code)', test: 'var(--test)',
    output: 'var(--ok, #3f7f7a)', agent: 'var(--agent)', run: 'var(--warn)',
};

const laneOf = (action) => LANES.find((l) => l.actions.includes(action));

let tip;
function tooltip() {
    if (!tip) {
        tip = d3.select('body').append('div').attr('class', 'tip');
    }
    return tip;
}

function showTip(event, html) {
    tooltip().html(html).classed('on', true)
        .style('left', `${event.clientX + 12}px`)
        .style('top', `${event.clientY - 10}px`);
}
const hideTip = () => tooltip().classed('on', false);

/**
 * Draw or update the session timeline.
 *
 * Called again on every new batch, so it joins on a stable key and transitions
 * rather than clearing and redrawing. Marks that were already on screen stay
 * still; only what changed moves.
 */
export function timeline(el, actions, { height = 230, animate = true } = {}) {
    const node = d3.select(el);
    const width = el.clientWidth || 800;
    const margin = { top: 10, right: 14, bottom: 26, left: 86 };
    const inner = height - margin.top - margin.bottom;

    let svg = node.select('svg');
    if (svg.empty()) {
        svg = node.append('svg');
        svg.append('g').attr('class', 'lanes');
        svg.append('g').attr('class', 'marks');
        svg.append('g').attr('class', 'axis x-axis');
    }
    svg.attr('viewBox', `0 0 ${width} ${height}`).attr('height', height);

    if (!actions.length) {
        svg.select('.marks').selectAll('*').remove();
        return;
    }

    const t0 = d3.min(actions, (a) => a.t);
    const t1 = d3.max(actions, (a) => a.t + (a.spanMs || a.ms || 0));
    const x = d3.scaleLinear()
        .domain([0, Math.max((t1 - t0) / 60000, 1)])
        .range([margin.left, width - margin.right]);
    const laneY = d3.scalePoint()
        .domain(LANES.map((l) => l.key))
        .range([margin.top + 14, margin.top + inner - 14]);

    // Lanes: a label and a hairline each, drawn once.
    const lanes = svg.select('.lanes').selectAll('g.lane').data(LANES, (d) => d.key);
    const laneEnter = lanes.enter().append('g').attr('class', 'lane');
    laneEnter.append('text').attr('class', 'lane-label');
    laneEnter.append('line').attr('class', 'lane-rule');
    const laneAll = laneEnter.merge(lanes);
    laneAll.select('text')
        .attr('x', margin.left - 12).attr('y', (d) => laneY(d.key))
        .text((d) => d.label);
    laneAll.select('line')
        .attr('x1', margin.left).attr('x2', width - margin.right)
        .attr('y1', (d) => laneY(d.key)).attr('y2', (d) => laneY(d.key));

    const minutes = (ms) => (ms - t0) / 60000;
    const placed = actions
        .map((a, i) => ({ ...a, i, lane: laneOf(a.a) }))
        .filter((a) => a.lane || a.a === 'IDLE');

    const marks = svg.select('.marks').selectAll('g.mark')
        .data(placed, (d) => `${d.t}-${d.a}-${d.i}`);

    marks.exit().remove();

    // Entering marks are visible immediately. They used to fade in from zero,
    // which meant a mark was invisible until a transition finished and stayed
    // invisible if anything interrupted it: a resize, a new batch arriving
    // mid-fade, or a backgrounded tab stalling d3's timer. Nothing that carries
    // the data should depend on an animation having run.
    const enter = marks.enter().append('g').attr('class', 'mark');
    enter.append('rect').attr('width', 0);
    enter.merge(marks)
        .on('mousemove', (event, d) => {
            const secs = Math.round((d.spanMs || d.ms || 0) / 100) / 10;
            showTip(event, `<b>${d.a}</b>${d.file ? `<br>${d.file}` : ''}`
                + (secs ? `<br>${secs}s` : '')
                + (d.count > 1 ? `<br>${d.count} events joined` : ''));
        })
        .on('mouseleave', hideTip)
        .select('rect')
        .attr('rx', 2)
        .attr('x', (d) => x(minutes(d.t)))
        .attr('y', (d) => (d.a === 'IDLE' ? margin.top : laneY(d.lane.key) - 7))
        .attr('height', (d) => (d.a === 'IDLE' ? inner : 14))
        .attr('fill', (d) => (d.a === 'IDLE' ? 'var(--idle)' : COLOR[d.lane.key]))
        .attr('opacity', (d) => (d.a === 'IDLE' ? 0.5 : d.a.startsWith('AGENT') ? 0.55 : 0.92))
        .call((sel) => {
            const w = (d) => {
                const span = d.spanMs || d.ms || 0;
                const px = x(minutes(d.t + span)) - x(minutes(d.t));
                return Math.max(px, d.a === 'IDLE' ? 1 : 2.5);
            };
            // Growing from the left reads as time passing and, unlike a fade,
            // leaves the mark on screen whatever happens to the transition.
            if (animate) sel.transition().duration(240).attr('width', w);
            else sel.attr('width', w);
        });

    // The axis is redrawn rather than transitioned. Sliding tick labels while
    // reading them is worse than not, and animating an axis is the one part of
    // this that a headless DOM cannot do, which would leave test output full of
    // errors that hide real ones.
    svg.select('.x-axis')
        .attr('transform', `translate(0,${margin.top + inner})`)
        .call(d3.axisBottom(x).ticks(Math.min(10, Math.ceil(x.domain()[1]))).tickFormat((d) => `${d}m`));
}

/** A small legend, so the lanes do not need explaining. */
export function legend(el) {
    d3.select(el).selectAll('span').data(LANES).join('span')
        .html((d) => `<i style="background:${COLOR[d.key]}"></i>${d.label}`);
}

/**
 * The sequence as the letters a pattern is counted in. Seeing it next to the
 * timeline is what makes the vocabulary feel like a description of the session
 * rather than a table of numbers.
 */
export function ribbon(el, actions, { limit = 160 } = {}) {
    const shown = actions.slice(-limit);
    // No fade-in here either, for the same reason as the marks: a word that is
    // invisible until an animation finishes is a word that can stay invisible.
    d3.select(el).selectAll('span')
        .data(shown, (d, i) => `${d.t}-${i}`)
        .join('span')
        .attr('class', (d) => (d.a === 'IDLE' ? 'idle' : null))
        .text((d) => (d.a === 'IDLE' ? '·' : d.a.toLowerCase().replace('_', ' ')));
}

/**
 * The patterns that recur, ranked by how much more often they happen than their
 * parts would predict.
 *
 * Two numbers per row on purpose. The bar is how often it happened, because a
 * pattern seen three times is not a finding however striking its score. The
 * number beside it is how much more often than chance, because the longest bar
 * will otherwise always be whichever two actions are individually commonest.
 */
export function patterns(el, rows, { limit = 8 } = {}) {
    const data = rows.slice(0, limit);
    const node = d3.select(el);

    let table = node.select('div.pat');
    if (table.empty()) table = node.append('div').attr('class', 'pat');

    const max = d3.max(data, (d) => d.count) || 1;
    table.selectAll('div.pat-row').data(data, (d) => d.gram).join(
        (enter) => {
            const row = enter.append('div').attr('class', 'pat-row');
            row.append('span').attr('class', 'pat-label');
            row.append('span').attr('class', 'pat-bar').append('i');
            row.append('span').attr('class', 'pat-n');
            row.append('span').attr('class', 'pat-lift');
            return row;
        },
        (update) => update,
        (exit) => exit.remove(),
    ).call((row) => {
        row.select('.pat-label').text((d) => label(d.gram));
        row.select('.pat-bar i').style('width', (d) => `${(d.count / max) * 100}%`);
        row.select('.pat-n').text((d) => d.count);
        row.select('.pat-lift')
            .text((d) => `${d.lift > 0 ? '+' : ''}${d.lift.toFixed(1)}`)
            .attr('title', (d) => `expected about ${d.expected.toFixed(1)} by chance`);
    });
}

/** A pattern written the way it is read aloud. Kept here so charts stay standalone. */
function label(gram) {
    return gram.split(' ').map((a) => a.toLowerCase().replace(/_/g, ' ')).join(' → ');
}
