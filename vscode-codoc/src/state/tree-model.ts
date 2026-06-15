/**
 * Parse tree.codoc text into a structural model. Mirrors codoc/codoc_file/parse.py.
 *
 *   feature      "  - Title  ⟨f-id⟩"  (marker '-' live, '~' retired; id hidden by the IDE)
 *   description  indented prose beneath a feature; blank lines are paragraph
 *                breaks (kept). A node ends only at the next feature line, an
 *                in-situ proposal hunk, or EOF — never at a blank line.
 *   steering     "> …" lines inside a description are notes TO THE AGENT, not
 *                prose: collected per-feature into `comments` (a contiguous run
 *                is one comment) and EXCLUDED from `description`. Loop B drains
 *                them into realize directives and the next render consumes them.
 *   proposals    in-situ diff hunks: a col-0 op char (+/-/~) then a node bearing
 *                a hidden ⟨e-id⟩, terminated by a blank line. Display-only here;
 *                harvested with their line range for the gutter/lens.
 *   comment      "# …" — ignored.
 *
 * Indentation depth determines parent. Inline "[label](codoc:file#symbol)" refs
 * stay verbatim in the description.
 */

export const PENDING_SENTINEL = '# ── pending changes';

export interface ParsedRef {
    label: string;
    file: string;
    symbol: string | null;
}

export interface ParsedComment {
    text: string;        // the `>` run's text, lines joined with '\n'
    line: number;        // 0-based line of the run's first `>` line
}

export interface ParsedFeature {
    id: string | null;   // null = new / no ⟨f-id⟩
    title: string;
    description: string;
    parent_id: string | null;
    retired: boolean;
    line: number;        // 0-based line of the title (for navigation)
    refs: ParsedRef[];
    comments: ParsedComment[];  // steering notes to the agent (not prose)
}

export interface ProposalHunk {
    line: number;        // 0-based line of the hunk's title row
    endLine: number;     // last line of the hunk block (for whole-block colouring)
    eventId: string;     // recovered from the hidden ⟨e-id⟩
    op: 'add' | 'retire' | 'move' | 'amend';
}

export interface ParseResult {
    features: ParsedFeature[];
    proposals: ProposalHunk[];
    pendingCount: number;
}

const FEATURE_RE = /^(?<indent>\s*)(?<marker>[-~])\s+(?<rest>.*\S)\s*$/;
const ID_RE = /⟨(f-[0-9a-f]+|new)⟩/;
const EVENT_ID_RE = /⟨(e-[0-9a-f]+)⟩/;
// In-situ proposal title: col-0 op char, space, optional tree indent, marker.
const PROPOSAL_TITLE_RE = /^[+\-~] \s*[-~] /;
// Legacy depth-0 hunk for trees written before in-situ proposals.
const DIFF_HUNK_RE = /^[+\-~] [-~] /;
const REF_RE = /\[([^\]]*)\]\(codoc:([^)#]+)(?:#([^)]+))?\)/g;
// External markdown link `[label](https://…)` — a page the realizing agent should
// consult (mirrors Python parse._LINK_RE). `codoc:` links are refs, not links.
const LINK_RE = /\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g;

export function extractRefs(text: string): ParsedRef[] {
    const refs: ParsedRef[] = [];
    REF_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = REF_RE.exec(text)) !== null) {
        refs.push({ label: m[1], file: m[2], symbol: m[3] ?? null });
    }
    return refs;
}

/** Pull external `[label](https://…)` links out of prose — the Consult strand
 *  (the realizing agent WebFetches them). `codoc:` links are excluded by the
 *  `https?://` scheme guard. Mirrors Python parse.extract_links. */
export function extractLinks(text: string): { label: string; url: string }[] {
    const links: { label: string; url: string }[] = [];
    LINK_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = LINK_RE.exec(text)) !== null) {
        links.push({ label: m[1], url: m[2] });
    }
    return links;
}

export function parseTreeCodoc(text: string): ParseResult {
    const features: ParsedFeature[] = [];
    const proposals: ProposalHunk[] = [];

    const stack: Array<{ indent: number; id: string | null }> = [];
    let descOwner: ParsedFeature | null = null;
    let descBuf: string[] = [];
    let inPending = false;
    let inProposal = false;                 // inside an in-situ proposal block
    let curProposal: ProposalHunk | null = null;

    let commentBuf: string[] = [];          // current contiguous `>` run
    let commentLine = -1;                   // first line of the run

    function flushComment(): void {
        if (descOwner !== null && commentBuf.length) {
            const text = commentBuf.join('\n').trim();
            if (text) descOwner.comments.push({ text, line: commentLine });
        }
        commentBuf = [];
        commentLine = -1;
    }

    function flushDesc(): void {
        flushComment();
        if (descOwner !== null) {
            const lines = descBuf.map(l => l.trim());
            while (lines.length && !lines[0]) lines.shift();
            while (lines.length && !lines[lines.length - 1]) lines.pop();
            descOwner.description = lines.join('\n');
            descOwner.refs = extractRefs(descOwner.description);
        }
        descOwner = null;
        descBuf = [];
    }

    const rawLines = text.split('\n');
    for (let i = 0; i < rawLines.length; i++) {
        const line = rawLines[i].replace(/\s+$/, '');
        const s = line.trim();

        if (inPending) {
            // Legacy bottom block: harvest hunk titles for the CodeLens.
            const ev = EVENT_ID_RE.exec(line);
            if (DIFF_HUNK_RE.test(line) && ev) {
                const head = line[0];
                const op = head === '+' ? 'add' : head === '-' ? 'retire' : 'move';
                proposals.push({ line: i, endLine: i, eventId: ev[1], op });
            }
            continue;
        }
        if (s.startsWith(PENDING_SENTINEL)) { flushDesc(); inPending = true; continue; }

        // In-situ proposal block: skip lines until the terminating blank,
        // extending the harvested hunk's range over each continuation line.
        if (inProposal) {
            if (!s) { inProposal = false; curProposal = null; continue; }
            if (curProposal) curProposal.endLine = i;
            continue;
        }
        if (PROPOSAL_TITLE_RE.test(line)) {
            const ev = EVENT_ID_RE.exec(line);
            if (ev) {
                flushDesc();
                const head = line[0];
                const op = head === '+' ? 'add' : head === '-' ? 'retire' : 'move';
                curProposal = { line: i, endLine: i, eventId: ev[1], op };
                proposals.push(curProposal);
                inProposal = true;
                continue;
            }
        }

        if (!s) {
            if (descOwner) {
                if (commentBuf.length) {
                    // The blank ends a steering-comment run; the comment "owns"
                    // one paragraph break — don't double it (mirrors parse.py).
                    flushComment();
                    if (descBuf.length && descBuf[descBuf.length - 1] !== '') descBuf.push('');
                } else {
                    descBuf.push('');
                }
            }
            continue;
        }
        if (DIFF_HUNK_RE.test(line)) { flushComment(); continue; }
        if (s.startsWith('#')) { flushComment(); continue; }

        const mf = FEATURE_RE.exec(line);
        if (mf?.groups) {
            flushDesc();
            const indent = mf.groups.indent.length;
            const marker = mf.groups.marker;
            const rest = mf.groups.rest.trim();
            const idMatch = ID_RE.exec(rest);
            const fid = idMatch ? (idMatch[1] === 'new' ? null : idMatch[1]) : null;
            const title = idMatch ? rest.slice(0, idMatch.index).trim() : rest;

            while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
            const parent_id = stack.length ? stack[stack.length - 1].id : null;

            const feature: ParsedFeature = {
                id: fid, title, description: '', parent_id,
                retired: marker === '~', line: i, refs: [], comments: [],
            };
            features.push(feature);
            stack.push({ indent, id: fid });
            descOwner = feature;
            descBuf = [];
            continue;
        }

        if (descOwner !== null) {
            if (s.startsWith('>')) {
                if (!commentBuf.length) commentLine = i;
                commentBuf.push(s.slice(1).replace(/^\s+/, ''));
            } else {
                flushComment();
                descBuf.push(s);
            }
        }
    }

    flushDesc();
    return { features, proposals, pendingCount: proposals.length };
}
