/**
 * suggestion-decorations.ts — anchors the resolution affordances for the suggestion
 * list in the whole-doc editor (R4). Since U3/U2b the human commits directly, so the
 * only suggestions here are agent code-ahead proposals (Reject / Accept → inbox.json
 * verdict). AMEND diffs render from the engine's insertion/deletion marks materialized
 * in the doc by the host (agent-proposals.applyAgentProposals). (Plus the per-feature
 * "Connections" threads line below.)
 *
 * TWO RULES, after the surface grew a Reject/Accept pair in four different places:
 *
 * 1. The PROPOSAL is drawn, not a card about it. An amend shows as tracked changes in
 *    the prose it changes; a retire strikes the heading it retires; an add is a dimmed
 *    placeholder node standing where the node will stand, at the end of its parent's
 *    subtree. A blue strip pinned under the parent's heading said "something is
 *    proposed near here" and read, wrongly, as an edit to the parent's own text.
 *
 * 2. One resolution surface per feature, on the feature. The verdict is a quiet pair
 *    of buttons at the end of the heading line (or of the placeholder), revealed on
 *    hover like the drag handle. At rest the reader sees the change itself and nothing
 *    else; when they want to act, the control is on the thing they are looking at.
 *    Everything at once still goes through the toolbar's Accept all / Reject all.
 */
import { nextDecorations } from './decoration-policy';
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import {
    directionLabel, directionActions, directionOrigin, directionNote,
    consequenceOf, consequenceVerb, consequenceNote, leavesForAgent, verdictHints,
} from '../../state/grammar';
import { icon } from '../icons';
import { launchPlane } from '../motion';
import { MAX_HEADING_LEVEL } from './feature-heading';
import type { Suggestion } from '../../state/suggestion-model';
import type { ThreadsData } from '../protocol';
import { THREADS_COLLAPSE_AT } from '../protocol';

export interface SuggestionHandlers {
    /** `edits` — the author amended an EDITABLE ghost before accepting: the verdict
     *  carries the edited title/description and the daemon applies the proposal
     *  with them in place of the proposed text. Absent = accept as proposed. */
    accept: (s: Suggestion, edits?: { title?: string; description?: string }) => void;
    reject: (s: Suggestion) => void;
}

export interface SuggestionDecorationsOptions {
    getSuggestions: () => Suggestion[];
    handlers: SuggestionHandlers;
    /** Features carrying an unlanded edit of the user's own (captured or handed off) —
     *  a proposal on one of these is contested, and the verdict strip says so. */
    getLocallyEdited?: () => ReadonlySet<string>;
}

export const SUGGESTIONS_UPDATED = 'codocSuggestionsUpdated';
const decoKey = new PluginKey('codocSuggestionDecorations');

function elc(tag: string, cls?: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

function actionButton(label: string, cls: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.className = 'ce-diff-btn ' + cls;
    b.textContent = label;
    b.type = 'button';
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onClick(); });
    return b;
}

/** A plan proposal describes code that does not exist yet; a reflection/drift
 *  proposal describes code that already landed. Encoded by TEXTURE (dashed vs solid),
 *  never by a new colour. Reads the same `consequenceOf` every other surface reads —
 *  it used to sniff the tag string here and nowhere else, so the texture and the
 *  button could disagree about the same proposal. */
function isPlanned(s: Suggestion): boolean {
    return s.direction === 'code-ahead' && consequenceOf(s.writesCode, s.tag) === 'build';
}

/**
 * The accept-time edits for a materialized ADD: what the node SAYS now, when that
 * differs from what was proposed.
 *
 * This replaced a module-level draft store keyed by suggestion id, and the store is not
 * missed. It existed because the proposal lived in a widget outside the document, so
 * anything the author typed into it had nowhere to be except a side map that had to be
 * pruned, survive decoration rebuilds, and never leak onto a later proposal reusing the
 * memory. A materialized node has somewhere to be: the document. Reading it back is one
 * function with no lifetime of its own.
 */
export function nodeEditsFor(
    l: { heading: PMModelNode; body: { node: PMModelNode }[] }, s: Suggestion,
): { title?: string; description?: string } | undefined {
    const out: { title?: string; description?: string } = {};
    const title = headingText(l.heading).trim();
    const desc = l.body.map(b => textOfBlock(b.node)).filter(t => t.trim()).join('\n\n');
    if (title && title !== (s.titleNew ?? '').trim()) out.title = title;
    if (desc !== (s.descNew ?? '').trim()) out.description = desc;
    return out.title || out.description !== undefined ? out : undefined;
}

/** A block's text as the reader sees it — the same projection the heading uses. */
function textOfBlock(node: PMModelNode): string {
    let t = '';
    node.forEach(child => { t += child.isText ? (child.text ?? '') : ''; });
    return t;
}

/**
 * The verdict: a quiet Reject/Accept pair, hidden until the feature is hovered.
 *
 * It carries the origin in plain words rather than leaving direction to hue alone — the
 * colourblind/high-contrast floor (U6/R8) — and the cascade cue when the change
 * surfaced back from implementing one of the user's own edits.
 *
 * Those words come from `grammar.directionOrigin`, not from here. This function used to
 * print the literal "from code" on every proposal it drew, which was wrong for the one
 * kind that is not the machine's: a deferred edit of the reader's own (`yours`), shown
 * back to them attributed to the codebase.
 */
function verdictStrip(
    s: Suggestion, handlers: SuggestionHandlers, locallyEdited = false,
    getEdits?: () => { title?: string; description?: string } | undefined,
): HTMLElement {
    const cq = consequenceOf(s.writesCode, s.tag);
    const row = elc('span', 'ce-verdict ' + s.direction + ' cq-' + cq
        + (locallyEdited ? ' contested' : ''));
    row.contentEditable = 'false';
    row.setAttribute('data-suggestion', s.id);

    // A verdict already recorded and not yet drained. Re-offering Accept here would
    // read as "your click did nothing" and invite a second one; say what is true
    // instead. The proposal itself stays on screen — it has not been applied.
    if (s.verdictPending) {
        const wait = elc('span', 'ce-verdict-waiting', 'recorded · waiting to apply');
        wait.title = leavesForAgent(cq)
            ? 'Your verdict is recorded. It applies on the next pass that can hand '
              + 'code work to the agent (a live session, or `codoc sync`).'
            : 'Your verdict is recorded. It applies on the daemon\'s next pass.';
        row.append(wait);
        row.classList.add('waiting');
        return row;
    }

    if (s.direction !== 'doc-ahead') {
        const dir = elc('span', 'ce-tc-dir', directionOrigin(s.direction));
        // The tag names the origin in the daemon's vocabulary ("code drift", "agent
        // plan"); on a deferred edit of the reader's own it only repeats the chip, so
        // the hover carries the REASON instead — the one sentence that explains why
        // words they typed are sitting here un-applied.
        dir.title = s.direction === 'yours'
            ? directionNote(s.direction)
            : directionLabel(s.direction) + (s.tag ? ' · ' + s.tag : '');
        row.append(dir);
    }
    // The consequence, in one plain sentence, only where it is not the boring one.
    // A `record` accept says nothing here — the absence IS the signal, and printing
    // "no code changes" on the 4-in-5 case would be noise that trains people to skip
    // the line on the 1-in-5 case that matters.
    if (leavesForAgent(cq)) {
        const note = elc('span', 'ce-verdict-cq');
        note.append(icon('paper-plane-tilt'), document.createTextNode(consequenceNote(cq)));
        row.append(note);
    }
    // The user has their own unlanded edit on this feature. Accepting is then a
    // choice between two versions rather than a formality — especially for a retire,
    // where it discards words they just wrote. Say so instead of letting the click
    // look free. (Deliberately NOT auto-rejecting: deferring a decision, editing, and
    // coming back later is a normal way to work, not a verdict.)
    // …unless the proposal IS their unlanded edit (`yours`). "Accepting replaces it
    // with the agent's version" then describes the opposite of what the click does,
    // and the strip has already said whose words these are.
    if (locallyEdited && s.direction !== 'yours') {
        const warn = elc('span', 'ce-tc-contested', '· you edited this');
        warn.title = s.kind === 'retire'
            ? 'You have an unlanded edit here. Accepting removes this feature and your edit with it.'
            : 'You have an unlanded edit here. Accepting replaces it with the agent\'s version.';
        row.append(warn);
    }
    if (s.causedBy) {
        const c = elc('span', 'ce-tc-cascade', '↳ from your edit');
        c.title = `implements ${s.causedBy}`;
        row.append(c);
    }
    const actions = elc('span', 'ce-tc-btns');
    // Disable after the first click so one verdict can't fire twice while the control
    // is still on screen — the authoritative removal arrives with the next payload.
    const once = (fn: (s: Suggestion) => void, launch: boolean) => () => {
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        // MOTION as the third channel for the same bit: a bookkeeping accept settles
        // in place, a code-writing one LAUNCHES — the plane flies off exactly as it
        // does on Commit & send, because the same thing just happened (work left for
        // the agent). Gated on reduced motion inside launchPlane.
        if (launch) launchPlane(actions.querySelector<HTMLElement>('.ce-icon'));
        fn(s);
    };
    const [secondary] = directionActions(s.direction);
    // The VERB carries the consequence. "Accept" for the 4-in-5 that only rewrite
    // words; "Accept & build" / "Accept & delete code" when the click reaches code.
    const primary = s.direction === 'doc-ahead'
        ? directionActions(s.direction)[1] : consequenceVerb(cq);
    // An accept from an editable ghost carries whatever the author amended in place
    // (ghostEditsFor) — read at CLICK time, so edits typed after the strip rendered
    // still ride the verdict.
    const acceptBtn = actionButton(primary, 'accept',
        once(sug => handlers.accept(sug, getEdits?.()), leavesForAgent(cq)));
    // Both hovers come from the grammar, which is the only place that knows a `yours`
    // amend costs no code AND is still not "matching code that already exists".
    const hints = verdictHints(s.direction, cq);
    acceptBtn.title = hints.accept;
    if (leavesForAgent(cq)) acceptBtn.prepend(icon('paper-plane-tilt'));
    const rejectBtn = actionButton(secondary, 'reject', once(handlers.reject, false));
    rejectBtn.title = hints.reject;
    actions.append(rejectBtn, acceptBtn);
    row.append(actions);
    return row;
}


/**
 * A proposed node, drawn where it will live: a dimmed title at the child level plus
 * its description, reading exactly like the accepted-but-unbuilt feature it becomes.
 * A MOVE shows the same way at its destination (the live node keeps its place until
 * the verdict lands, so the two ends of the move are both visible).
 *
 * An ADD ghost is EDITABLE in place: the title and description are live fields, and
 * whatever the author reshapes rides the Accept as accept-time edits (the daemon
 * applies the proposal with the edited text). A move ghost stays inert — its text
 * lives on the real node.
 */
function ghostFeatureDom(
    s: Suggestion, level: number, label: string, handlers: SuggestionHandlers,
): HTMLElement {
    const wrap = elc('div', 'ce-ghost-feature ' + s.kind + (isPlanned(s) ? ' planned' : ''));
    wrap.contentEditable = 'false';
    wrap.dataset.level = String(level);
    wrap.setAttribute('data-suggestion', s.id);
    const title = elc('div', 'ce-ghost-title', label || '(untitled)');
    title.title = s.kind === 'move'
        ? 'The agent proposes moving this feature here. Nothing has moved yet.'
        : 'The agent proposes this feature. ' + consequenceNote(consequenceOf(s.writesCode, s.tag))
          + ' You can edit the title and description before accepting — the edited version is what gets applied.';
    wrap.append(title);
    // A move's description is already on screen at the node's current home — repeating
    // it here would read as a second copy of the feature rather than as its destination.
    const desc = s.kind === 'move' ? '' : (s.descNew ?? '').trim();
    if (desc) wrap.append(elc('div', 'ce-ghost-desc', desc));
    wrap.append(verdictStrip(s, handlers, false));
    return wrap;
}

/** A heading's visible text — the title of the node a MOVE relocates (the suggestion
 *  itself carries no title for a move; the node it points at does). */
function headingText(node: PMModelNode): string {
    return node.textBetween(0, node.content.size, '', '').trim();
}

interface FeatureLoc {
    headingPos: number;
    heading: PMModelNode;
    level: number;
    /** End of this feature's OWN blocks — the next heading at any level. What a
     *  retire actually removes: children are promoted, not retired with it. */
    bodyEnd: number;
    /** End of this feature's whole subtree — the next heading at this level or
     *  shallower. Where a new child would be inserted. */
    subtreeEnd: number;
    /** This feature's own description blocks, as {pos,node} in document order. */
    body: { pos: number; node: PMModelNode }[];
}

/** Index every feature heading with its outline level, its own body blocks, and the
 *  two boundaries the proposal decorations need. */
export function locateFeatures(doc: PMModelNode): Map<string, FeatureLoc> {
    const heads: { pos: number; node: PMModelNode; level: number }[] = [];
    const blocks: { pos: number; node: PMModelNode }[] = [];
    doc.forEach((node, pos) => {
        blocks.push({ pos, node });
        if (node.type.name === 'featureHeading') heads.push({ pos, node, level: Number(node.attrs.level ?? 0) });
    });
    const docEnd = doc.content.size;
    const loc = new Map<string, FeatureLoc>();
    heads.forEach((h, i) => {
        const next = heads[i + 1];
        const bodyEnd = next ? next.pos : docEnd;
        // The subtree runs until the next heading at this level or shallower.
        let subtreeEnd = docEnd;
        for (let j = i + 1; j < heads.length; j++) {
            if (heads[j].level <= h.level) { subtreeEnd = heads[j].pos; break; }
        }
        // Keyed by `fid`, else by the proposal that put the node there. A materialized
        // plan node has no fid — the store has not minted one and must not appear to
        // have — so without the second rung its own verdict strip would have nothing to
        // anchor to and the reader would see the proposed node with no way to answer it.
        const attrs = h.node.attrs as { fid?: string | null; proposed?: string | null };
        const fid = attrs.fid ?? attrs.proposed ?? null;
        if (!fid) return;
        const body = blocks.filter(b => b.pos > h.pos && b.pos < bodyEnd);
        loc.set(fid, { headingPos: h.pos, heading: h.node, level: h.level, bodyEnd, subtreeEnd, body });
    });
    return loc;
}

function buildDecorations(
    doc: PMModelNode, suggestions: Suggestion[], handlers: SuggestionHandlers,
    locallyEdited: ReadonlySet<string> = new Set(),
): DecorationSet {
    const loc = locateFeatures(doc);
    const docEnd = doc.content.size;
    const decos: Decoration[] = [];
    for (const s of suggestions) {
        // An ADD is no longer a widget beside the document: `plan-materialize` puts the
        // proposed node IN the tree, at the rank it will take, drawn in the plan
        // channel's faded ink. So it resolves exactly like an amend or a retire — one
        // verdict strip on the node itself — and the "edit it before accepting" affordance
        // is now just editing the document, which is both less machinery and a truer
        // preview than a pair of fields in a card.
        if (s.kind === 'move') {
            // The placeholder lands where the node itself will: last child of the
            // destination parent, i.e. the end of that parent's subtree. Anchoring it
            // right under the parent's heading (the old behaviour) put it between the
            // parent's title and the parent's own prose, where it read as an edit to it.
            const parent = s.parentId ? loc.get(s.parentId) : null;
            let at = parent ? parent.subtreeEnd : docEnd;
            let level = parent ? Math.min(parent.level + 1, MAX_HEADING_LEVEL - 1) : 0;
            let side: -1 | 1 = 1;
            // Sibling anchors: apply honours after_id/before_id on accept
            // (rank_between), so when the proposal names them the ghost draws in
            // that exact slot — otherwise it appeared "last child" and the real
            // node jumped elsewhere the moment the user accepted it.
            const after = s.afterId ? loc.get(s.afterId) : null;
            const before = !after && s.beforeId ? loc.get(s.beforeId) : null;
            if (after) { at = after.subtreeEnd; level = after.level; }
            else if (before) { at = before.headingPos; level = before.level; side = -1; }
            // A MOVE keeps its ghost: nothing is materialized for it, because the node
            // itself stays where it is until the verdict lands — so both ends of the
            // move have to be visible at once, the live node and its destination.
            let label = s.titleNew ?? '';
            if (s.featureId) {
                const src = loc.get(s.featureId);
                if (src) {
                    label = label || headingText(src.heading);
                    decos.push(Decoration.node(src.headingPos, src.headingPos + src.heading.nodeSize,
                                               { class: 'ce-move-source' }));
                }
            }
            decos.push(Decoration.widget(at, () => ghostFeatureDom(s, level, label, handlers),
                                         { side, key: 'sug-' + s.id }));
            continue;
        }
        // An add is filed under its own proposal id (see `locateFeatures`); everything
        // else under the feature it targets.
        const key = s.kind === 'add' ? s.id : s.featureId;
        const l = key ? loc.get(key) : null;
        if (!l) continue;
        // retire — the strike on the heading IS the proposal; amend — the tracked-change
        // ins/del marks the host materialized in the prose are. Either way the only thing
        // to add is the verdict, inline at the end of the heading line so there is exactly
        // one place per feature to resolve it.
        if (s.kind === 'retire') {
            // The WHOLE node is going away, so the whole node has to look like it: the
            // title alone struck while its description sat at full ink read as "the title
            // is being deleted". Scoped to this feature's own blocks (`bodyEnd`, the next
            // heading at ANY level) because apply.py promotes live children to the
            // grandparent rather than retiring them — striking the subtree would claim
            // they are going too.
            decos.push(Decoration.node(l.headingPos, l.headingPos + l.heading.nodeSize,
                                       { class: 'ce-retire-proposed' }));
            for (const b of l.body) {
                decos.push(Decoration.node(b.pos, b.pos + b.node.nodeSize,
                                           { class: 'ce-retire-proposed-body' }));
            }
        }
        decos.push(Decoration.widget(l.headingPos + 1 + l.heading.content.size,
                                     () => verdictStrip(
                                         s, handlers, locallyEdited.has(s.featureId ?? ''),
                                         // An ADD is editable where it stands, because it
                                         // IS the document now — so the accept carries
                                         // whatever the author reshaped, read back off the
                                         // node rather than out of a parallel draft store.
                                         s.kind === 'add' ? () => nodeEditsFor(l, s) : undefined),
                                     { side: 1, key: 'sug-' + s.id + (locallyEdited.has(s.featureId ?? '') ? ':edited' : '') }));
    }
    return DecorationSet.create(doc, decos);
}

// ── Unified "Connections" under each heading + on-demand peek (U4 → U5) ────────
// One quiet in-flow line per feature, four strands:
//   ↳ Depends-on (reads) · ↰ Used-by · ⟢ Bound code · ◷ Consult (external links)
// replacing the old ce-deps chips, the legacy xrefs, AND the tree-pane refs pill.
// reads/used-by are RANKED by coupling weight (heaviest first); each strand caps at
// THREADS_COLLAPSE_AT and, when it overflows, shows a "+N" that opens a peek popover
// with the full ranked neighbourhood (client-side from the same payload — no extra
// round-trip; KTD5/H1). The cap matches the assembler's `collapsed` flag.
// COLOUR = direction (reads vs used-by); SHAPE = kind (per-edge call/import marker).
export interface DependencyDecorationsOptions {
    getThreads: () => Record<string, ThreadsData>;
    onNavigate: (fid: string) => void;
    onOpenBinding: (file: string, symbol: string) => void;
    onConsult: (url: string) => void;
}
export const DEPS_UPDATED = 'codocThreadsUpdated';
const depKey = new PluginKey('codocThreadDecorations');

const THREAD_MAX = THREADS_COLLAPSE_AT; // named items per strand before a "+N" peek

// DISPLAY variant — deliberately NOT the canonical `symbolLeaf` (registry-model.ts):
// strips only the `file::` qualifier (keeps `Class.method`) and maps `__module__` to
// the `‹module›` glyph for the Connections "bound code" rows. Converging it would
// drop the `Class.` nesting from the displayed symbol label.
function leafSym(symbol: string): string {
    const i = symbol.indexOf('::');
    const tail = i >= 0 ? symbol.slice(i + 2) : symbol;
    return tail === '__module__' ? '‹module›' : (tail.split('::').pop() ?? tail);
}

/** Per-edge SHAPE glyph (shape = kind, never a new colour). A pure call edge →
 *  `()`; a pure import edge → `⊂`; mixed / unknown → none. Rendered as a quiet
 *  superscript marker on the feature link. */
function kindShape(kinds: string[] | undefined): string {
    const has = (k: string): boolean => (kinds ?? []).some(x => x.includes(k));
    const call = has('call');
    const imp = has('import');
    if (call && !imp) return '()';
    if (imp && !call) return '⊂';
    return '';
}

function threadLink(text: string, title: string, onClick: () => void, fid?: string, shape?: string): HTMLElement {
    const a = elc('span', 'ce-thread', text || '(untitled)');
    a.title = title;
    // Tag a feature link with its fid so the hover-card handler (U4) can resolve it
    // by feature id — a decoration data-attr only, never serialized into the doc —
    // and make it keyboard-reachable so the card opens on Enter/Space.
    if (fid) { a.dataset.fid = fid; a.tabIndex = 0; }
    if (shape) a.append(elc('sup', 'ce-thread-kind', shape));
    a.addEventListener('mousedown', ev => ev.preventDefault());
    a.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return a;
}

// ── peek popover (the full neighbourhood, client-side) ────────────────────────
let openPeekEl: HTMLElement | null = null;
function closePeek(): void { openPeekEl?.remove(); openPeekEl = null; }
// Close the transient threads peek on window resize so it never sits at a stale position (U5).
if (typeof window !== 'undefined') window.addEventListener('resize', closePeek);

function openThreadsPeek(
    anchor: HTMLElement, t: ThreadsData,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): void {
    closePeek();
    const pop = elc('div', 'ce-peek');
    const section = (label: string, items: HTMLElement[]): void => {
        if (!items.length) return;
        const sec = elc('div', 'ce-peek-sec');
        sec.append(elc('div', 'ce-peek-label', label));
        const list = elc('div', 'ce-peek-list');
        items.forEach(i => list.append(i));
        sec.append(list);
        pop.append(sec);
    };
    section('depends on', t.reads.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); }, d.toId, kindShape(d.kinds))));
    section('used by', t.usedBy.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); }, d.toId, kindShape(d.kinds))));
    section('bound code', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => { closePeek(); onOpenBinding(r.file, r.symbol); })));
    section('consult', (t.consult ?? []).map(l => threadLink(l.label, l.url, () => { closePeek(); onConsult(l.url); })));
    document.body.append(pop);
    const rect = anchor.getBoundingClientRect();
    pop.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - pop.offsetHeight - 8)}px`;
    pop.style.left = `${Math.min(rect.left, window.innerWidth - pop.offsetWidth - 8)}px`;
    openPeekEl = pop;
    // dismiss on outside click / Esc (registered next tick so the opening click doesn't close it)
    const cleanup = (): void => {
        document.removeEventListener('mousedown', onDoc, true);
        document.removeEventListener('keydown', onKey, true);
    };
    const onDoc = (e: MouseEvent): void => { if (!pop.contains(e.target as Node)) { closePeek(); cleanup(); } };
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') { closePeek(); cleanup(); } };
    setTimeout(() => {
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('keydown', onKey, true);
    }, 0);
}

function makeThreadsRow(
    t: ThreadsData,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): HTMLElement {
    const row = elc('div', 'ce-threads');
    row.contentEditable = 'false';
    // dir ∈ reads (Depends-on) | used (Used-by) | refs (Bound code) | consult — the
    // CLASS, not a hue: colour = direction is applied in CSS off `.ce-strand.<dir>`.
    const strand = (dir: string, glyph: string, glyphTitle: string, items: HTMLElement[], moreLabel: string): void => {
        if (!items.length) return;
        const s = elc('span', 'ce-strand ' + dir);
        const g = elc('span', 'ce-strand-glyph', glyph); g.title = glyphTitle; s.append(g);
        items.slice(0, THREAD_MAX).forEach((el, i) => { if (i) s.append(document.createTextNode(', ')); s.append(el); });
        // Collapse: beyond THREADS_COLLAPSE_AT rows, a "+N" reveals the full ranked list
        // in the peek — a display swap (popover), no transition (reduced-motion safe).
        if (items.length > THREAD_MAX) {
            const more = elc('span', 'ce-more', `+${items.length - THREAD_MAX} more`);
            more.title = `Show all ${items.length} ${moreLabel}`;
            more.addEventListener('mousedown', ev => ev.preventDefault());
            more.addEventListener('click', ev => { ev.preventDefault(); openThreadsPeek(row, t, onNavigate, onOpenBinding, onConsult); });
            s.append(more);
        }
        row.append(s);
    };
    // The in-situ line now carries only this feature's OWN attachments — bound code +
    // external consults. Feature-to-feature relationships (depends-on / used-by) moved to
    // the navigator's Focus mode (the tree dims to a feature's dependency neighbourhood),
    // so the doc stays calm and uncluttered. The full ranked depends-on / used-by lists are
    // still reachable from the "+N" peek below.
    strand('refs', '⟢', 'bound code', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => onOpenBinding(r.file, r.symbol))), 'bound code');
    strand('consult', '◷', 'consult', (t.consult ?? []).map(l => threadLink(l.label, l.url, () => onConsult(l.url))), 'consult links');
    return row;
}

/** The inline line renders only refs + consult now; reads/usedBy live in the tree's Focus
 *  mode. So a feature with ONLY relationships (no bound code, no consult) renders no line. */
function inlineThreadsEmpty(t: ThreadsData): boolean {
    return !t.refs.length && !(t.consult ?? []).length;
}

function buildThreadDecorations(
    doc: PMModelNode, threadsMap: Record<string, ThreadsData>,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const t = threadsMap[fid];
        if (!t || inlineThreadsEmpty(t)) return;
        const after = pos + node.nodeSize;
        decos.push(Decoration.widget(after, () => makeThreadsRow(t, onNavigate, onOpenBinding, onConsult), { side: -1, key: 'thr-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const DependencyDecorations = Extension.create<DependencyDecorationsOptions>({
    name: 'dependencyDecorations',
    addOptions() {
        return { getThreads: () => ({}), onNavigate: () => {}, onOpenBinding: () => {}, onConsult: () => {} };
    },
    addProseMirrorPlugins() {
        const getThreads = (): Record<string, ThreadsData> => this.options.getThreads();
        const onNavigate = this.options.onNavigate;
        const onOpenBinding = this.options.onOpenBinding;
        const onConsult = this.options.onConsult;
        return [
            new Plugin({
                key: depKey,
                state: {
                    init: (_c, state) => buildThreadDecorations(state.doc, getThreads(), onNavigate, onOpenBinding, onConsult),
                    // Structure-keyed — see decoration-policy.ts. Threads hang off
                    // headings and come from the payload, not from the prose.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(DEPS_UPDATED),
                        () => buildThreadDecorations(newState.doc, getThreads(), onNavigate, onOpenBinding, onConsult),
                    ),
                },
                props: { decorations(state) { return depKey.getState(state); } },
            }),
        ];
    },
});

export const SuggestionDecorations = Extension.create<SuggestionDecorationsOptions>({
    name: 'suggestionDecorations',

    addOptions() {
        return {
            getSuggestions: () => [], handlers: { accept: () => {}, reject: () => {} },
            getLocallyEdited: () => new Set<string>(),
        };
    },

    addProseMirrorPlugins() {
        const getSuggestions = (): Suggestion[] => this.options.getSuggestions();
        const handlers = this.options.handlers;
        const getEdited = (): ReadonlySet<string> =>
            this.options.getLocallyEdited?.() ?? new Set<string>();
        const build = (doc: PMModelNode): DecorationSet =>
            buildDecorations(doc, getSuggestions(), handlers, getEdited());
        return [
            new Plugin({
                key: decoKey,
                state: {
                    init: (_config, state) => build(state.doc),
                    // Structure-keyed — see decoration-policy.ts. These hang off headings
                    // and come from the payload, never from the prose, so typing inside a
                    // description only MOVES them. Rebuilding per keystroke also tore down
                    // and recreated the verdict buttons mid-interaction, which drops
                    // keyboard focus and re-runs their entrance every character.
                    apply: (tr, old, _oldState, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(SUGGESTIONS_UPDATED), () => build(newState.doc),
                    ),
                },
                props: {
                    decorations(state) { return decoKey.getState(state); },
                },
            }),
        ];
    },
});
