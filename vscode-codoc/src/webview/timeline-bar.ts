/**
 * timeline-bar.ts — the scrubber over the tree's own history (W8).
 *
 * One row above the document: a caption naming what you are looking at, a range you
 * drag through the moments the tree has been in, and a way back to Now. Dragging left
 * replaces the page with the tree as it read then, the change made AT that moment
 * marked in the prose where it happened.
 *
 * ## Why a range input rather than a custom track
 *
 * It drags, it takes arrow keys and Home/End, it announces itself to a screen reader,
 * and it does all of that natively. A hand-rolled track would reimplement four
 * interactions to gain a few pixels of styling, and every one of them is a place to get
 * accessibility wrong. The ticks are painted behind it; the input itself stays real.
 *
 * ## Why the detail is a hover card, not a second row
 *
 * A moment's provenance — who, why, which directive, whose prompt, which session, which
 * files — is four lines when it is anything. Reserving four lines of the reading surface
 * for a stance a reader dips into would cost the document more than the history is worth
 * at rest. The caption carries the one-line answer and the card carries the rest, the
 * same trade the `/codoc:ask` bar makes with its question.
 *
 * Like `ask-bar.ts` this is plain DOM with no editor knowledge: it reports which moment
 * the reader moved to and lets the caller do the rendering, so the same bar works in the
 * extension and on the hub.
 */
import { actorLabel, actorRole, relativeTime, type BlameRole } from '../state/blame-model';
import { momentTrace } from '../state/provenance';
import { filesTouched, type Moment, type Timeline } from '../state/revision-model';
import { closeProvenanceCard, isProvenanceCardOpen, showProvenanceCard } from './provenance-card';

/** `null` = Now (the live document); otherwise an index into `timeline.moments`. */
export type HistoryIndex = number | null;

export interface TimelineBarHandle {
    element: HTMLElement;
    /** Re-seed from a fresh payload, preserving the viewed moment BY ID where it still
     *  exists — a pass that appends a new moment must not slide the reader forward. */
    setTimeline: (timeline: Timeline) => void;
    /** Move the scrubber without emitting `onScrub` (the caller is already in sync). */
    setIndex: (index: HistoryIndex) => void;
    index: () => HistoryIndex;
    destroy: () => void;
}

export interface TimelineBarOptions {
    /** The reader moved. `null` means they came back to Now. */
    onScrub: (index: HistoryIndex) => void;
    /** Open a code diff for the moment's touched files, against its directive's base
     *  commit. Absent ⇒ the affordance is not offered (the hub, where there is no
     *  local checkout to diff against). */
    onOpenDiff?: (moment: Moment, files: string[]) => void;
    /** Open the coding session a change was asked for in — the far end of the chain, and
     *  the only link in it that was ever a bare id rather than something to look at. */
    onOpenSession?: (sessionId: string) => void;
}

// ── pure captions (exported for tests; no DOM) ───────────────────────────────

/** How many features a moment touched, in words. The count is what makes a moment
 *  legible at a glance — "edited 3 features" is a different event from "edited a
 *  feature", and a reader scanning the ticks is looking for the big ones. */
export function momentScope(moment: Moment): string {
    const n = moment.fids.length;
    if (n === 0) return 'no features';
    return n === 1 ? '1 feature' : `${n} features`;
}

/** The verb a moment did, collapsed to the one a reader would use.
 *
 *  A moment is usually several ops — an amend plus the bindings it moved — and naming
 *  all of them ("edited, bound, unbound") describes the machinery rather than the
 *  change. The dominant intent wins, in the order a person would notice it. */
export function momentVerb(moment: Moment): string {
    const kinds = new Set(moment.entries.map(e => e.kind));
    if (kinds.has('add_node')) return kinds.size > 1 ? 'added and edited' : 'added';
    if (kinds.has('retire_node')) return 'retired';
    if (kinds.has('amend')) return 'edited';
    if (kinds.has('move_node')) return 'moved';
    if (kinds.has('attach') || kinds.has('detach')) return 'rebound code in';
    return 'changed';
}

/** The one-line answer: "codoc edited 3 features · 2h ago". */
export function momentCaption(moment: Moment, nowMs: number): string {
    const when = relativeTime(moment.at, nowMs);
    return `${actorLabel(moment.actor)} ${momentVerb(moment)} ${momentScope(moment)}${when ? ` · ${when}` : ''}`;
}

// ── the bar ──────────────────────────────────────────────────────────────────

function button(cls: string, label: string, title: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = label;
    b.title = title;
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return b;
}

const HOVER_DELAY_MS = 300;

export function createTimelineBar(opts: TimelineBarOptions): TimelineBarHandle {
    const root = document.createElement('div');
    root.className = 'ce-timeline';
    root.hidden = true;

    const caption = document.createElement('button');
    caption.type = 'button';
    caption.className = 'ce-tl-caption';
    caption.addEventListener('mousedown', ev => ev.preventDefault());

    const track = document.createElement('div');
    track.className = 'ce-tl-track';
    const ticks = document.createElement('div');
    ticks.className = 'ce-tl-ticks';
    ticks.setAttribute('aria-hidden', 'true');
    const range = document.createElement('input');
    range.type = 'range';
    range.className = 'ce-tl-range';
    range.min = '0';
    range.step = '1';
    range.setAttribute('aria-label', 'Tree history');
    track.append(ticks, range);

    const nowBtn = button('ce-tl-now', 'Now',
        'Return to the live document (End)', () => moveTo(null, true));

    root.append(caption, track, nowBtn);

    let timeline: Timeline = { moments: [], directives: {}, truncated: false };
    let index: HistoryIndex = null;
    let card: HTMLElement | null = null;
    let hoverTimer: ReturnType<typeof setTimeout> | null = null;

    // ── the provenance card ──────────────────────────────────────────────────
    //    Shared with the History stance's per-feature label (webview/provenance-card.ts):
    //    the same chain asked from two ends deserves the same card.
    const closeCard = (): void => { card = null; closeProvenanceCard(); };

    const openCard = (): void => {
        const moment = index === null ? null : timeline.moments[index];
        if (!moment) { closeCard(); return; }
        const files = filesTouched(moment);
        const directive = moment.causedBy ? timeline.directives[moment.causedBy] : undefined;
        const actions = [];
        if (files.length && opts.onOpenDiff) {
            actions.push({
                label: `Open the code diff (${files.length})`,
                title: 'Compare this code against the commit this change started from',
                run: () => opts.onOpenDiff?.(moment, files),
            });
        }
        if (directive?.session_id && opts.onOpenSession) {
            actions.push({
                label: 'Open the conversation',
                title: 'The coding session this change was asked for in',
                run: () => opts.onOpenSession?.(directive.session_id ?? ''),
            });
        }
        card = showProvenanceCard({
            anchor: caption,
            head: `${actorLabel(moment.actor)} · ${relativeTime(moment.at, Date.now())}`,
            rows: momentTrace(moment, timeline),
            actions,
        });
    };

    // ── movement ─────────────────────────────────────────────────────────────
    function render(): void {
        const n = timeline.moments.length;
        // Visibility belongs to the CALLER (the History stance owns it). This used to
        // force the bar visible on every render, so turning History off while parked in
        // the past un-hid the bar the caller had just hidden, and it stayed until the
        // next payload.
        range.max = String(n);
        range.value = String(index === null ? n : index);
        range.disabled = n === 0;
        const atNow = index === null;
        root.classList.toggle('past', !atNow);
        nowBtn.hidden = atNow;

        if (n === 0) {
            caption.textContent = 'No recorded history yet';
            caption.title = 'codoc records a moment each time the tree changes. '
                + 'Edit a feature, or let the loop reflect a code change, and it appears here.';
            range.setAttribute('aria-valuetext', 'no history');
            ticks.replaceChildren();
            return;
        }

        const moment = atNow ? null : timeline.moments[index as number];
        if (moment) {
            caption.textContent = momentCaption(moment, Date.now());
            caption.title = 'What changed at this point — hover for who asked, and why';
            range.setAttribute('aria-valuetext', caption.textContent);
        } else {
            const oldest = timeline.moments[0];
            caption.textContent = 'Now — the live document';
            caption.title = timeline.truncated
                ? `Drag back through ${n} recorded moments. There is older history than codoc keeps.`
                : `Drag back through ${n} recorded moments, to ${relativeTime(oldest.at, Date.now())}.`;
            range.setAttribute('aria-valuetext', 'now');
        }

        // Ticks: one per moment plus the Now stop, positioned by index (not by time) so
        // a burst of edits stays individually reachable. A time axis would collapse a
        // whole afternoon's work into a pixel and strand the reader on the gaps.
        const marks = timeline.moments.map((m, i) => {
            const t = document.createElement('span');
            t.className = `ce-tl-tick role-${actorRole(m.actor) as BlameRole}`;
            t.style.left = `${(i / n) * 100}%`;
            t.classList.toggle('here', i === index);
            return t;
        });
        ticks.replaceChildren(...marks);
    }

    function moveTo(next: HistoryIndex, emit: boolean): void {
        if (next === index) return;
        index = next;
        closeCard();
        render();
        if (emit) opts.onScrub(index);
    }

    range.addEventListener('input', () => {
        const v = Number(range.value);
        moveTo(v >= timeline.moments.length ? null : v, true);
    });

    return {
        element: root,
        setTimeline(next: Timeline) {
            const heldId = index === null ? null : timeline.moments[index]?.id ?? null;
            timeline = next;
            if (heldId !== null) {
                // Track the viewed moment by IDENTITY across a refresh. A pass that
                // appends a moment shifts nothing, but one that drops the oldest (the
                // window is bounded) shifts every index by one — and a reader whose page
                // silently jumped to a different day would have no way to tell.
                const at = next.moments.findIndex(m => m.id === heldId);
                index = at >= 0 ? at : null;
            }
            render();
        },
        setIndex(next: HistoryIndex) { moveTo(next, false); },
        index: () => index,
        destroy() {
            if (hoverTimer) clearTimeout(hoverTimer);
            closeCard();
            root.remove();
        },
    };
}
