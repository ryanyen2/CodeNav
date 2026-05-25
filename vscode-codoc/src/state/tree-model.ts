/**
 * Parse tree.codoc text into a structural model.
 *
 * Ports codoc/codoc_file/parse.py to TypeScript. Grammar rules:
 *   feature    "  - Title  ⟨f-id⟩" (marker '-' = live, '~' = retired)
 *   description  indented prose lines beneath a feature
 *   proposal   "? add \"Title\"  ⟨e-id⟩"  (action char: ?/+/-)
 *   comment    "# …" — ignored
 *   refs line  "↪ refs: …" — ignored (not part of description)
 *
 * Indentation depth determines parent. Identity comes from ⟨f-id⟩.
 */

export interface ParsedFeature {
    id: string | null;   // null = new / no ⟨f-id⟩
    title: string;
    description: string;
    parent_id: string | null;
    retired: boolean;
}

export interface ParseResult {
    features: ParsedFeature[];
    pendingCount: number;        // count of '?' proposal lines
    proposalActions: Map<string, string>;  // event_id → '?'/'+'/'-'
}

const FEATURE_RE = /^(?<indent>\s*)(?<marker>[-~])\s+(?<rest>.*\S)\s*$/;
const ID_RE = /⟨(f-[0-9a-f]+|new)⟩/;
const PROPOSAL_RE = /^\s*(?<action>[?+\-])\s+(?:add|amend|move|retire)\b.*⟨(?<eid>e-[0-9a-f]+)⟩/;

export function parseTreeCodoc(text: string): ParseResult {
    const features: ParsedFeature[] = [];
    const proposalActions = new Map<string, string>();
    let pendingCount = 0;

    // Stack entries: {indent, id} so we can resolve parent from indentation depth.
    const stack: Array<{ indent: number; id: string | null }> = [];
    let descOwner: ParsedFeature | null = null;
    const descBuf: string[] = [];

    function flushDesc(): void {
        if (descOwner !== null) {
            descOwner.description = descBuf.join('\n').replace(/^\s+|\s+$/g, '');
        }
        descOwner = null;
        descBuf.length = 0;
    }

    for (const raw of text.split('\n')) {
        const line = raw.trimEnd();
        const s = line.trim();

        if (!s) { flushDesc(); continue; }
        if (s.startsWith('#')) continue;
        if (s.startsWith('↪ refs:')) continue;

        const mp = PROPOSAL_RE.exec(line);
        if (mp?.groups) {
            flushDesc();
            const { action, eid } = mp.groups;
            proposalActions.set(eid, action);
            if (action === '?') pendingCount++;
            continue;
        }

        const mf = FEATURE_RE.exec(line);
        if (mf?.groups && !s.startsWith('?')) {
            flushDesc();
            const indent = mf.groups.indent.length;
            const marker = mf.groups.marker;
            const rest = mf.groups.rest.trim();

            const idMatch = ID_RE.exec(rest);
            const fid = idMatch ? (idMatch[1] === 'new' ? null : idMatch[1]) : null;
            const title = idMatch ? rest.slice(0, idMatch.index).trim() : rest;

            // Pop stack to find parent
            while (stack.length && stack[stack.length - 1].indent >= indent) {
                stack.pop();
            }
            const parent_id = stack.length ? stack[stack.length - 1].id : null;

            const feature: ParsedFeature = {
                id: fid,
                title,
                description: '',
                parent_id,
                retired: marker === '~',
            };
            features.push(feature);
            stack.push({ indent, id: fid });
            descOwner = feature;
            descBuf.length = 0;
            continue;
        }

        if (descOwner !== null) {
            descBuf.push(s);
        }
    }

    flushDesc();
    return { features, pendingCount, proposalActions };
}
