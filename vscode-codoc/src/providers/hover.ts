import * as vscode from 'vscode';
import * as path from 'path';
import { ServerState } from '../state/server';

const FEATURE_LINE_RE = /^\s*[-~]\s+(\S+)/;
const HLC_RE = /#\s*\?([0-9a-zA-Z:\-_]+)/;
const AT_REF_RE = /(?<!\w)@([\w.]+(?:::[\w.]+)*)/g;

const STATE_DESCRIPTIONS: Record<string, string> = {
    stable:     'All bindings healthy and current.',
    drafting:   'Feature defined but not all bindings resolved.',
    stub:       'Placeholder — intent not yet written.',
    strained:   'One or more bindings may be stale or missing.',
    deprecated: 'Feature marked for removal.',
    severed:    'All bindings missing; no live code anchor.',
};

export class CodocHoverProvider implements vscode.HoverProvider {
    constructor(private _server: ServerState) {}

    async provideHover(document: vscode.TextDocument, position: vscode.Position): Promise<vscode.Hover | null> {
        if (document.languageId !== 'codoc') return null;
        const line = document.lineAt(position.line).text;

        // Hover on state badge [Stable] etc.
        const stateMatch = /\[([A-Za-z]+)\]/.exec(line);
        if (stateMatch) {
            const state = stateMatch[1].toLowerCase();
            const desc = STATE_DESCRIPTIONS[state];
            if (desc) {
                const start = line.indexOf(stateMatch[0]);
                const end = start + stateMatch[0].length;
                if (position.character >= start && position.character <= end) {
                    return new vscode.Hover(
                        new vscode.MarkdownString(`**${stateMatch[1]}** — ${desc}`),
                        new vscode.Range(position.line, start, position.line, end),
                    );
                }
            }
        }

        // Hover on proposal HLC comment — show keyboard shortcut hint.
        const hlcMatch = HLC_RE.exec(line);
        if (hlcMatch) {
            const hlcStart = line.indexOf(hlcMatch[0]);
            if (position.character >= hlcStart) {
                const md = new vscode.MarkdownString(
                    `Pending proposal — HLC \`${hlcMatch[1].slice(0, 20)}…\`  \n` +
                    `**Cmd+Enter** to accept · **Cmd+Shift+Backspace** to reject`,
                );
                return new vscode.Hover(md);
            }
        }

        // Hover on feature slug — show slug and hint.
        const featureMatch = FEATURE_LINE_RE.exec(line);
        if (featureMatch) {
            const slug = featureMatch[1];
            const slugStart = line.indexOf(slug, featureMatch[0].length - slug.length);
            if (position.character >= slugStart && position.character <= slugStart + slug.length) {
                const md = new vscode.MarkdownString(
                    `**${slug}**  \nUse \`codoc show ${slug}\` to view details.`,
                );
                return new vscode.Hover(md, new vscode.Range(position.line, slugStart, position.line, slugStart + slug.length));
            }
        }

        // Hover on @symbol inline ref — show source preview
        AT_REF_RE.lastIndex = 0;
        let atMatch: RegExpExecArray | null;
        while ((atMatch = AT_REF_RE.exec(line)) !== null) {
            const matchStart = atMatch.index;
            const matchEnd = matchStart + atMatch[0].length;
            if (position.character >= matchStart && position.character <= matchEnd) {
                const ref = atMatch[1];
                const range = new vscode.Range(position.line, matchStart, position.line, matchEnd);
                const md = await this._symbolHover(ref);
                if (md) return new vscode.Hover(md, range);
            }
        }

        return null;
    }

    private async _symbolHover(ref: string): Promise<vscode.MarkdownString | null> {
        const server = this._server;
        if (!server.connected || !server.client || !server.rootDir) {
            return new vscode.MarkdownString(`\`@${ref}\``);
        }
        const sepIdx = ref.indexOf('::');
        const file = sepIdx >= 0 ? ref.slice(0, sepIdx) : '';
        const symbol = sepIdx >= 0 ? ref.slice(sepIdx + 2) : ref;
        try {
            const pos = await server.client.resolveAnchor(file, symbol, null, 0);
            if (!pos || !file) {
                return new vscode.MarkdownString(`\`@${ref}\``);
            }
            const fileUri = vscode.Uri.file(path.join(server.rootDir, file));
            const doc = await vscode.workspace.openTextDocument(fileUri);
            const previewStart = pos.start_line;
            const previewEnd = Math.min(pos.start_line + 8, pos.end_line, doc.lineCount - 1);
            const lines: string[] = [];
            for (let i = previewStart; i <= previewEnd; i++) {
                lines.push(doc.lineAt(i).text);
            }
            const ext = path.extname(file).slice(1) || 'python';
            const md = new vscode.MarkdownString();
            md.appendMarkdown(`**\`@${symbol}\`** — \`${file}\` (line ${previewStart + 1})\n\n`);
            md.appendCodeblock(lines.join('\n'), ext);
            md.isTrusted = true;
            return md;
        } catch {
            return new vscode.MarkdownString(`\`@${ref}\``);
        }
    }
}
