import * as vscode from 'vscode';

export interface LineRange {
    startLine: number;
    endLine: number;
}

export class LineTracker {
    private ranges = new Map<string, LineRange>();

    setRanges(ranges: Map<string, LineRange>): void {
        this.ranges = new Map(ranges);
    }

    onDocumentChange(event: vscode.TextDocumentChangeEvent): void {
        for (const change of event.contentChanges) {
            const lineDelta =
                (change.text.match(/\n/g) || []).length -
                (change.range.end.line - change.range.start.line);
            if (lineDelta === 0) continue;
            const pivotLine = change.range.start.line;
            for (const [uuid, r] of this.ranges) {
                if (r.startLine > pivotLine) {
                    this.ranges.set(uuid, {
                        startLine: r.startLine + lineDelta,
                        endLine: r.endLine + lineDelta,
                    });
                } else if (r.endLine > pivotLine) {
                    this.ranges.set(uuid, { ...r, endLine: r.endLine + lineDelta });
                }
            }
        }
    }

    getRanges(): Map<string, LineRange> {
        return this.ranges;
    }
}
