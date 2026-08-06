/**
 * blame-model.ts — pure derivations for the History (blame) stance (W2).
 *
 * The daemon ships a bounded per-feature edit history in the sidecar
 * (`feature_history`: who/when/why per applied event, newest first). This module
 * turns a raw HLC timestamp into a readable relative time, classifies an actor
 * into a role (for the attribution rail's hue), and formats a one-line summary
 * of who last touched a feature. No DOM, no vscode — unit-tested directly.
 */
import type { HistoryEntry, SidecarData } from './bindings-model';

export type BlameRole = 'human' | 'agent' | 'loop';

/** The role behind an actor id — drives the attribution rail's tint. `"human"`
 *  is the author; `"loop"` is codoc's own deterministic maintenance; anything
 *  else (a named coding agent: claude-code, codex, …) is `"agent"`. */
export function actorRole(actor: string): BlameRole {
    if (actor === 'human' || actor === '') return 'human';
    if (actor === 'loop') return 'loop';
    return 'agent';
}

/** A friendly actor label for a blame line. Named agents keep their id; the
 *  deterministic machine pass reads as "codoc". */
export function actorLabel(actor: string): string {
    const role = actorRole(actor);
    if (role === 'human') return 'You';
    if (role === 'loop') return 'codoc';
    return actor;
}

/** Milliseconds since epoch encoded in an HLC string (`<wall>-<logical>-<node>`,
 *  wall in ms). NaN when unparseable — callers fall back to no relative time. */
export function hlcWallMs(at: string): number {
    const dash = (at ?? '').indexOf('-');
    const head = dash >= 0 ? at.slice(0, dash) : at;
    const n = Number(head);
    return Number.isFinite(n) ? n : NaN;
}

/** A compact relative time ("just now", "5m ago", "3h ago", "2d ago", or a date
 *  past a week). `nowMs` is injectable for tests. */
export function relativeTime(at: string, nowMs: number = Date.now()): string {
    const ms = hlcWallMs(at);
    if (!Number.isFinite(ms)) return '';
    const secs = Math.max(0, Math.round((nowMs - ms) / 1000));
    if (secs < 45) return 'just now';
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    if (days < 7) return `${days}d ago`;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
}

/** A human phrase for an op kind, for the timeline ("edited", "created", …). */
export function kindPhrase(kind: string): string {
    switch (kind) {
        case 'add_node': return 'created';
        case 'amend': return 'edited';
        case 'attach': return 'bound code to';
        case 'detach': return 'unbound code from';
        case 'refresh': return 'refreshed';
        case 'move_node': return 'moved';
        case 'retire_node': return 'retired';
        default: return kind;
    }
}

export interface BlameSummary {
    role: BlameRole;
    /** e.g. "You edited · 3h ago" */
    line: string;
    entry: HistoryEntry;
}

/** The one-line "who last touched this" summary from a feature's history array
 *  (newest first). Assumes non-empty — callers guard. */
export function blameSummaryFrom(history: HistoryEntry[], nowMs: number = Date.now()): BlameSummary {
    const e = history[0];
    const when = relativeTime(e.at, nowMs);
    const line = `${actorLabel(e.actor)} ${kindPhrase(e.kind)}${when ? ' · ' + when : ''}`;
    return { role: actorRole(e.actor), line, entry: e };
}

/** The one-line "who last touched this" summary — the resting blame label on a
 *  heading. `null` when the feature has no recorded history in the window. */
export function blameSummary(
    sidecar: SidecarData, featureId: string, nowMs: number = Date.now(),
): BlameSummary | null {
    const hist = sidecar.feature_history?.[featureId];
    if (!hist || !hist.length) return null;
    return blameSummaryFrom(hist, nowMs);
}

/** The full timeline for a feature (newest first), empty when none is recorded. */
export function featureHistory(sidecar: SidecarData, featureId: string): HistoryEntry[] {
    return sidecar.feature_history?.[featureId] ?? [];
}
