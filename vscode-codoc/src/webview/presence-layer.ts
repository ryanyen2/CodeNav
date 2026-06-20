/**
 * presence-layer.ts — agent-as-collaborator presence in the webview (P3 / spec §B).
 *
 * Renders a single floating `.ce-presence` avatar over the doc surface (and a label-less twin
 * over the tree pane) that glides to the feature an agent is touching, trails a soft comet
 * tail, and whispers what it is doing. Pure presentation: it reads `deriveAgentPresences` off
 * the already-plumbed sync signal (presence.ts) and never touches the doc model. Every motion
 * runs through motion.ts (reduced motion → the avatar simply APPEARS at the active feature
 * with its label, still legible — no glide/trail/breathe).
 *
 * The DOM anchors are queried live (`.codoc-feature-heading[data-fid]` in the doc surface and
 * `.row[data-id]` in the tree), so the layer needs no editor coupling beyond the two panes.
 */
import { icon } from './icons';
import { glideTo, presenceTrail, spinForever, popLanded, prefersReducedMotion, type TweenController } from './motion';
import {
    AgentPresence, RealizeProgress, presenceWhisper, phaseGlyph, roleInk,
    clampToViewport, overlayAnchor,
} from '../state/presence';

/** How long the whisper label lingers after a hop before auto-hiding (§B.1). */
const LABEL_HIDE_MS = 4000;
/** Stale-epoch grace: a presence with no fresh signal fades after this so a dead agent never
 *  haunts the doc (§B.5). */
const STALE_GRACE_MS = 12000;
/** Done-teardown grace (§B.2 continuity): wait before tearing down on `done` so a fast
 *  `editing` on the NEXT feature lets the avatar GLIDE there instead of blink-out/blink-in. */
const DONE_GRACE_MS = 450;

interface PaneRefs {
    /** The non-scrolling overlay parent (.doc-host) the avatar is absolutely positioned in —
     *  so it stays pinned to the viewport and tracks its heading as the surface scrolls. */
    docHost: () => HTMLElement | null;
    /** The scrolling doc surface (.ce-whole-surface) — the heading anchor + clamp viewport. */
    docSurface: () => HTMLElement | null;
    /** The tree pane (.tree) the twin floats over. */
    treePane: () => HTMLElement | null;
    /** Scroll the doc to a feature (the off-screen chevron click target). */
    scrollToFeature: (fid: string) => void;
}

export class PresenceLayer {
    private docAvatar: HTMLElement | null = null;
    private treeAvatar: HTMLElement | null = null;
    private label: HTMLElement | null = null;
    private current: AgentPresence | null = null;
    private lastPos: { top: number; left: number } | null = null;
    private spin: TweenController | null = null;
    private labelTimer = 0;
    private staleTimer = 0;
    private doneTimer = 0;
    private repaintRaf = 0;
    /** The tree row whose own active-write dot we hid for the twin (§B.3) — so we can restore it. */
    private twinSuppressedRow: HTMLElement | null = null;

    constructor(private readonly refs: PaneRefs) {}

    /** Update the presence from the latest sync signal. `presences` is the derived agent list
     *  (today at most one); `realize` is sync.realize for the whisper. Empty → tear down. */
    update(presences: AgentPresence[], realize?: RealizeProgress): void {
        const next = presences[0] ?? null;
        if (!next) { this.teardown(); return; }
        // a fresh presence cancels a pending done-teardown → the avatar GLIDES to the next
        // feature instead of blink-out/blink-in (§B.2 continuity).
        if (this.doneTimer) { clearTimeout(this.doneTimer); this.doneTimer = 0; }
        this.docAvatar?.classList.remove('fading');
        this.armStaleGrace();
        const moved = !this.current || this.current.fid !== next.fid;
        const phaseChanged = this.current?.phase !== next.phase;
        this.current = next;
        this.ensureAvatars();
        this.paintAvatars(next);
        this.placeTwin(next);
        if (moved) this.hopTo(next, realize);
        else this.refreshLabel(next, realize);
        if (moved || phaseChanged) this.applyWorkingPulse(next);
        if (next.phase === 'done') this.celebrateDone(next);
    }

    /** Re-place the avatar on scroll/resize — THROTTLED through rAF so a scroll burst coalesces
     *  into one layout read per frame (the rects read here force layout; doing it per scroll
     *  event would thrash the hot path). */
    reposition(): void {
        if (!this.current || this.repaintRaf) return;
        this.repaintRaf = requestAnimationFrame(() => {
            this.repaintRaf = 0;
            if (!this.current) return;
            this.placeAvatar(this.current, false);
            this.placeTwin(this.current);
        });
    }

    destroy(): void { this.teardown(); }

    // ── internals ──────────────────────────────────────────────────────────────
    private ensureAvatars(): void {
        // The doc-host + tree pane are recreated on a reconcile, orphaning the old avatars.
        // Re-create (or re-parent) when the current node isn't inside the live pane. The avatar
        // lives on the NON-scrolling .doc-host (not the scroll surface) so it stays pinned to the
        // viewport and tracks its heading across scroll (the drift fix).
        const host = this.refs.docHost();
        if (host && (!this.docAvatar || !host.contains(this.docAvatar))) {
            this.docAvatar?.remove();
            this.docAvatar = document.createElement('div');
            this.docAvatar.className = 'ce-presence';
            // off-screen chevron click → scroll to the agent's feature (§B.5 Figma-cursor style).
            this.docAvatar.addEventListener('click', () => {
                if (this.current && this.docAvatar?.classList.contains('offscreen')) {
                    this.refs.scrollToFeature(this.current.fid);
                }
            });
            this.label = document.createElement('div');
            this.label.className = 'ce-presence-label';
            this.docAvatar.append(this.label);
            host.appendChild(this.docAvatar);
            this.lastPos = null; // fresh host → no glide-from baseline
        }
        const tree = this.refs.treePane();
        if (tree && (!this.treeAvatar || !tree.contains(this.treeAvatar))) {
            this.treeAvatar?.remove();
            this.treeAvatar = document.createElement('div');
            this.treeAvatar.className = 'ce-presence ce-presence-twin';
            tree.appendChild(this.treeAvatar);
        }
    }

    /** Set the avatar ring tint + inner glyph for the agent's role + phase (§B.1). */
    private paintAvatars(p: AgentPresence): void {
        for (const av of [this.docAvatar, this.treeAvatar]) {
            if (!av) continue;
            av.dataset.ink = roleInk(p.role);
            av.dataset.phase = p.phase;
            // the inner glyph (skip the label child on the doc avatar)
            av.querySelector('.ce-presence-glyph')?.remove();
            const g = document.createElement('span');
            g.className = 'ce-presence-glyph';
            g.append(icon(phaseGlyph(p.phase)));
            av.insertBefore(g, av.firstChild);
        }
    }

    /** Glide the doc avatar to the feature, drop a comet trail, then (re)show the whisper. The
     *  destination is clamped to the viewport edges with a chevron when off-screen (§B.5). */
    private hopTo(p: AgentPresence, realize?: RealizeProgress): void {
        const anchor = this.docAnchor(p.fid);
        const host = this.refs.docHost();
        if (!anchor || !this.docAvatar || !host) { this.placeAvatar(p, false); this.refreshLabel(p, realize); return; }
        const clamped = clampToViewport(anchor.top, anchor.clamp.top, anchor.clamp.bottom);
        const dest = { top: clamped.y, left: anchor.left };
        this.docAvatar.dataset.chevron = clamped.chevron ?? '';
        this.docAvatar.classList.toggle('offscreen', clamped.chevron != null);
        const from = this.lastPos ?? dest;
        const dist = Math.hypot(dest.top - from.top, dest.left - from.left);
        // soft comet trail along the path (gated) — parented to the same non-scrolling overlay.
        presenceTrail((top, left) => {
            const dot = document.createElement('div');
            dot.className = 'ce-presence-trail';
            dot.dataset.ink = roleInk(p.role);
            dot.style.top = `${top}px`;
            dot.style.left = `${left}px`;
            host.appendChild(dot);
            return dot;
        }, from, dest);
        glideTo(this.docAvatar, dest, dist);
        this.lastPos = dest;
        this.refreshLabel(p, realize);
    }

    /** Place the doc avatar at the feature's heading without a glide (scroll/initial). Clamps
     *  to the viewport edges with a chevron when the heading is off-screen (§B.5). */
    private placeAvatar(p: AgentPresence, withLabel: boolean): void {
        const anchor = this.docAnchor(p.fid);
        if (!anchor || !this.docAvatar) return;
        const clamped = clampToViewport(anchor.top, anchor.clamp.top, anchor.clamp.bottom);
        this.docAvatar.style.top = `${clamped.y}px`;
        this.docAvatar.style.left = `${anchor.left}px`;
        this.docAvatar.dataset.chevron = clamped.chevron ?? '';
        this.docAvatar.classList.toggle('offscreen', clamped.chevron != null);
        this.lastPos = { top: clamped.y, left: anchor.left };
        if (withLabel) this.refreshLabel(p);
    }

    /** The tree-twin rides the active feature's row right edge (§B.3), REPLACING the row's own
     *  active-write dot (not coexisting) — so one fact isn't double-signalled at the row edge. */
    private placeTwin(p: AgentPresence): void {
        const tree = this.refs.treePane();
        const row = tree?.querySelector<HTMLElement>(`.row[data-id="${cssEscape(p.fid)}"]`);
        if (!tree || !row || !this.treeAvatar) {
            if (this.treeAvatar) this.treeAvatar.style.display = 'none';
            this.restoreTwinRow();
            return;
        }
        // §B.3: the row's own active-write dot (+ the P2 ce-touch-pulse twin) is redundant with
        // the avatar parked there — suppress it while the twin is on this row.
        if (this.twinSuppressedRow !== row) this.restoreTwinRow();
        row.classList.add('ce-twin-here');
        this.twinSuppressedRow = row;
        this.treeAvatar.style.display = '';
        this.treeAvatar.style.top = `${row.offsetTop + (row.offsetHeight - 16) / 2}px`;
        this.treeAvatar.style.left = `${tree.clientWidth - 22}px`;
        this.treeAvatar.dataset.ink = roleInk(p.role);
    }

    /** Un-suppress the previously-twinned row's active-write dot (§B.3). */
    private restoreTwinRow(): void {
        this.twinSuppressedRow?.classList.remove('ce-twin-here');
        this.twinSuppressedRow = null;
    }

    /** The whisper label: phase verb + title (+ realize progress while editing), auto-hidden
     *  after 4 s of no movement (the avatar persists, §B.1). */
    private refreshLabel(p: AgentPresence, realize?: RealizeProgress): void {
        if (!this.label) return;
        const title = this.featureTitle(p.fid);
        this.label.textContent = presenceWhisper(p.name, p.phase, title, realize);
        this.label.classList.remove('hidden');
        if (this.labelTimer) clearTimeout(this.labelTimer);
        // never auto-hide under reduced motion — the label IS the presence then (no glide).
        if (!prefersReducedMotion()) {
            this.labelTimer = window.setTimeout(() => this.label?.classList.add('hidden'), LABEL_HIDE_MS);
        }
    }

    /** The parked working pulse: editing → slow breathe (CSS), reflecting → spin the glyph,
     *  read/done → no loop. (CSS drives the breathe; JS owns only the spin loop so it can stop.) */
    private applyWorkingPulse(p: AgentPresence): void {
        if (this.spin) { this.spin.cancel(); this.spin = null; }
        if (p.phase === 'reflecting') {
            const glyph = this.docAvatar?.querySelector<HTMLElement>('.ce-presence-glyph') ?? null;
            this.spin = spinForever(glyph);
        }
    }

    /** done → fade out + a one-shot green landed check on the heading (§B.2, shared with accept). */
    private celebrateDone(p: AgentPresence): void {
        const surface = this.refs.docSurface();
        const heading = surface?.querySelector<HTMLElement>(`.codoc-feature-heading[data-fid="${cssEscape(p.fid)}"]`);
        if (heading && !heading.querySelector('.ce-presence-landed')) {
            const check = document.createElement('span');
            check.className = 'ce-presence-landed';
            check.append(icon('check-circle'));
            heading.append(check);
            popLanded(check.querySelector<HTMLElement>('.ce-icon'));
            window.setTimeout(() => check.remove(), 700);
        }
        // fade the avatar, but hold a grace before teardown — if the NEXT feature's `editing`
        // arrives shortly, update() cancels this and the avatar GLIDES on (§B.2 continuity).
        this.docAvatar?.classList.add('fading');
        if (this.doneTimer) clearTimeout(this.doneTimer);
        this.doneTimer = window.setTimeout(() => {
            this.doneTimer = 0;
            if (this.current?.phase === 'done') this.teardown();
        }, DONE_GRACE_MS);
    }

    /** The overlay-relative anchor for a feature heading's right edge (where the avatar parks).
     *  The avatar lives on the NON-scrolling .doc-host, so `overlayAnchor` returns the heading's
     *  CURRENT viewport position minus the overlay's — which TRACKS the heading as the surface
     *  scrolls (the drift fix). `clamp` is the off-screen edge band in the SAME overlay
     *  coordinates (the surface's visible band) so the §B.5 chevron clamp still applies. */
    private docAnchor(fid: string): { top: number; left: number; clamp: { top: number; bottom: number } } | null {
        const host = this.refs.docHost();
        const surface = this.refs.docSurface();
        const heading = surface?.querySelector<HTMLElement>(`.codoc-feature-heading[data-fid="${cssEscape(fid)}"]`);
        if (!host || !surface || !heading) return null;
        const oRect = host.getBoundingClientRect();
        const sRect = surface.getBoundingClientRect();
        const { top, left } = overlayAnchor(heading.getBoundingClientRect(), oRect);
        // the visible band, in overlay coords: where the scroll surface sits within .doc-host.
        return { top, left, clamp: { top: sRect.top - oRect.top, bottom: sRect.bottom - oRect.top } };
    }

    private featureTitle(fid: string): string {
        const surface = this.refs.docSurface();
        const heading = surface?.querySelector<HTMLElement>(`.codoc-feature-heading[data-fid="${cssEscape(fid)}"]`);
        return (heading?.textContent ?? '').trim();
    }

    /** Re-arm the 12 s stale grace: if no fresh update arrives, fade the avatar (§B.5). */
    private armStaleGrace(): void {
        if (this.staleTimer) clearTimeout(this.staleTimer);
        this.staleTimer = window.setTimeout(() => this.teardown(), STALE_GRACE_MS);
    }

    private teardown(): void {
        if (this.labelTimer) { clearTimeout(this.labelTimer); this.labelTimer = 0; }
        if (this.staleTimer) { clearTimeout(this.staleTimer); this.staleTimer = 0; }
        if (this.doneTimer) { clearTimeout(this.doneTimer); this.doneTimer = 0; }
        if (this.repaintRaf) { cancelAnimationFrame(this.repaintRaf); this.repaintRaf = 0; }
        if (this.spin) { this.spin.cancel(); this.spin = null; }
        this.restoreTwinRow();   // §B.3: give the row its active-write dot back
        this.docAvatar?.remove(); this.docAvatar = null;
        this.treeAvatar?.remove(); this.treeAvatar = null;
        this.label = null;
        this.current = null;
        this.lastPos = null;
    }
}

function cssEscape(s: string): string {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, '\\$&');
}
