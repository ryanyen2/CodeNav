/**
 * Parse tree.codoc text into a structural model. Mirrors codoc/codoc_file/parse.py.
 *
 *   feature      "  - Title  ⟨f-id⟩"  (marker '-' live, '~' retired; id hidden by the IDE)
 *   description  indented prose beneath a feature; blank lines are paragraph
 *                breaks (kept). A node ends only at the next feature line, the
 *                "# ── pending changes" sentinel, or EOF — never at a blank line.
 *   proposals    everything past the sentinel is a display-only diff block.
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

export interface ParsedFeature {
    id: string | null;   // null = new / no ⟨f-id⟩
    title: string;
    description: string;
    parent_id: string | null;
    retired: boolean;
    line: number;        // 0-based line of the title (for navigation)
    refs: ParsedRef[];
}

export interface ProposalHunk {
    line: number;        // 0-based line of the hunk's title row
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
const DIFF_HUNK_RE = /^[+\-~] [-~] /;
const REF_RE = /\[([^\]]*)\]\(codoc:([^)#]+)(?:#([^)]+))?\)/g;

export function extractRefs(text: string): ParsedRef[] {
    const refs: ParsedRef[] = [];
    REF_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = REF_RE.exec(text)) !== null) {
        refs.push({ label: m[1], file: m[2], symbol: m[3] ?? null });
    }
    return refs;
}

export function parseTreeCodoc(text: string): ParseResult {
    const features: ParsedFeature[] = [];
    const proposals: ProposalHunk[] = [];

    const stack: Array<{ indent: number; id: string | null }> = [];
    let descOwner: ParsedFeature | null = null;
    let descBuf: string[] = [];
    let inPending = false;

    function flushDesc(): void {
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
            // Display-only diff block: harvest proposal hunks for the CodeLens.
            const ev = EVENT_ID_RE.exec(line);
            if (DIFF_HUNK_RE.test(line) && ev) {
                const head = line[0];
                const op = head === '+' ? 'add' : head === '-' ? 'retire' : 'move';
                proposals.push({ line: i, eventId: ev[1], op });
            }
            continue;
        }
        if (s.startsWith(PENDING_SENTINEL)) { flushDesc(); inPending = true; continue; }

        if (!s) { if (descOwner) descBuf.push(''); continue; }
        if (DIFF_HUNK_RE.test(line)) continue;
        if (s.startsWith('#')) continue;

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
                retired: marker === '~', line: i, refs: [],
            };
            features.push(feature);
            stack.push({ indent, id: fid });
            descOwner = feature;
            descBuf = [];
            continue;
        }

        if (descOwner !== null) descBuf.push(s);
    }

    flushDesc();
    return { features, proposals, pendingCount: proposals.length };
}
