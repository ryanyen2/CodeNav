import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { resolveCard, ResolvedCard } from '../state/registry-model';

/**
 * Tier-1 hover-preview for inline `codoc:` code citations in the raw `tree.codoc`
 * editor. Hovering `[label](codoc:file#symbol)` shows a small card — the owning
 * feature's title, a one-line gist (or a muted "No description yet"), the binding
 * count (or a "plan" marker for an unrealized placeholder) — plus a navigate link
 * that reuses `codoc.openRef` (the tier-2 escalation). A dead ref renders an
 * explicit unresolved state instead of a card.
 *
 * Everything is assembled host-side from the already-loaded registry + bindings
 * sidecar via the pure `resolveCard`; there is NO host→Python call. The webview
 * popover (a later unit) consumes the same `ResolvedCard` contract.
 */
const REF_RE = /\[[^\]]*\]\(codoc:([^)#]+)(?:#([^)]+))?\)/g;

export class CodocHoverProvider implements vscode.HoverProvider {
    constructor(private state: WorkspaceState) {}

    provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | null {
        if (document.languageId !== 'codoc') return null;

        const line = document.lineAt(position.line).text;
        REF_RE.lastIndex = 0;
        let m: RegExpExecArray | null;
        while ((m = REF_RE.exec(line)) !== null) {
            const start = m.index;
            const end = m.index + m[0].length;
            if (position.character < start || position.character > end) continue;

            const file = m[1];
            const symbol = m[2] ?? '';
            const card = resolveCard(this.state.registry, this.state.sidecar, file, symbol);
            const md = renderCard(card, file, symbol);
            const range = new vscode.Range(position.line, start, position.line, end);
            return new vscode.Hover(md, range);
        }

        // Not hovering over a codoc: ref.
        return null;
    }
}

/** A `command:codoc.openRef` markdown link with the (file, symbol) payload. */
function openRefLink(label: string, file: string, symbol: string): string {
    const args = encodeURIComponent(JSON.stringify([file, symbol]));
    return `[${label}](command:codoc.openRef?${args})`;
}

/** Render a resolved card / dead-ref state as a trusted markdown hover. The raw
 *  hovered `file`/`symbol` thread the navigate link (the card has no raw ref). */
export function renderCard(card: ResolvedCard, file: string, symbol: string): vscode.MarkdownString {
    const md = new vscode.MarkdownString('', true);
    md.isTrusted = true;

    if (!card.resolved) {
        md.appendMarkdown(
            `$(warning) **Unresolved reference**\n\n` +
            `\`${card.target}\` — the linked code can't be found.\n\n` +
            `_Flagged in the Connections panel._`,
        );
        return md;
    }

    if (card.kind === 'file') {
        const n = card.owners.length;
        md.appendMarkdown(`**\`${card.file}\`** — used by ${n} feature${n === 1 ? '' : 's'}\n\n`);
        if (n === 0) {
            md.appendMarkdown('_No owning features yet._');
        } else {
            for (const o of card.owners) {
                md.appendMarkdown(`- ${openRefLink(o.title, card.file, '')}\n`);
            }
        }
        return md;
    }

    // Symbol card.
    md.appendMarkdown(`**${escapeMd(card.title)}**\n\n`);
    md.appendMarkdown((card.gist ? escapeMd(card.gist) : '_No description yet_') + '\n\n');

    const meta = card.unrealized
        ? '$(lightbulb) plan'
        : `${card.bindingCount} ref${card.bindingCount === 1 ? '' : 's'}`;
    // Navigate (tier-2) reuses openRef; the card itself is the tier-1 preview.
    md.appendMarkdown(`${meta} · ${openRefLink('Open code', file, symbol)}`);
    return md;
}

/** Escape markdown control characters in plain-text fields (titles, gists). */
function escapeMd(text: string): string {
    return text.replace(/([\\`*_{}[\]()#+\-.!|])/g, '\\$1');
}
