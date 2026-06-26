import { describe, it, expect } from 'vitest';
import { isSaveChord } from '../webview/save-chord';

// U6 / R11, R12: the webview swallows the platform save chord from any focus context and
// repurposes it as a commit. These cover the chord-recognition seam; the window-level
// interception + preventDefault (AE3: no "content is newer" dialog) is verified manually.

describe('isSaveChord', () => {
    it('recognizes ⌘S on mac', () => {
        expect(isSaveChord({ metaKey: true, ctrlKey: false, key: 's' }, true)).toBe(true);
        expect(isSaveChord({ metaKey: true, ctrlKey: false, key: 'S' }, true)).toBe(true);
    });

    it('recognizes Ctrl-S off mac', () => {
        expect(isSaveChord({ metaKey: false, ctrlKey: true, key: 's' }, false)).toBe(true);
        expect(isSaveChord({ metaKey: false, ctrlKey: true, key: 'S' }, false)).toBe(true);
    });

    it('ignores the wrong modifier for the platform', () => {
        // Ctrl-S on mac is not the save chord (⌘ is); ⌘S off mac is not either (Ctrl is).
        expect(isSaveChord({ metaKey: false, ctrlKey: true, key: 's' }, true)).toBe(false);
        expect(isSaveChord({ metaKey: true, ctrlKey: false, key: 's' }, false)).toBe(false);
    });

    it('ignores other keys and bare s', () => {
        expect(isSaveChord({ metaKey: true, ctrlKey: false, key: 'k' }, true)).toBe(false);
        expect(isSaveChord({ metaKey: false, ctrlKey: false, key: 's' }, true)).toBe(false);
        expect(isSaveChord({ metaKey: false, ctrlKey: false, key: 's' }, false)).toBe(false);
    });
});
