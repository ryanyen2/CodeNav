// Which of the study's surfaces a file belongs to.
//
// Kept apart from extension.js so it can be tested without a running VS Code.
// The surfaces are what "switches between the description and the code" counts,
// so a file landing in the wrong one silently changes a result.

/** The written description, in either condition. */
function isDescription(file) {
    return file === 'CLAUDE.md' || file.endsWith('/tree.codoc') || file.endsWith('tree.codoc');
}

/**
 * The sample documents a project reads, and the files it writes out.
 *
 * Neither is code and neither is the description: looking at `report.txt` or at
 * the `report.md` the program just produced is checking OUTPUT, which is a
 * different act from reading the source that decided it. Counting them as code
 * inflated "did they open the code before acting", which is exactly the measure
 * that is supposed to separate reading from trusting.
 *
 * Keyed on the `fixtures/` directory rather than the extension, because the
 * discriminator is not the suffix: scribe reads `fixtures/report.txt` and writes
 * `fixtures/report.md` beside it. The directory also catches tally's `.csv`
 * samples, which used to fall through to `other` and vanish — so until now the
 * two projects were not even counted the same way.
 */
function isSample(file) {
    return file.startsWith('fixtures/') || file.includes('/fixtures/');
}

function surfaceOf(file) {
    if (!file) return 'other';
    if (isDescription(file)) return 'document';
    if (isSample(file)) return 'output';
    if (file.startsWith('tests/') || file.startsWith('test/')) return 'test';
    if (/\.(py|ts|js|toml|html|md|json|xml|css|txt)$/.test(file)) return 'code';
    return 'other';
}

/** Path relative to the project, so two machines produce comparable logs. */
function relativeTo(rootDir, fsPath) {
    if (!fsPath) return '';
    if (rootDir && fsPath.startsWith(rootDir)) {
        return fsPath.slice(rootDir.length).replace(/^[/\\]/, '');
    }
    const parts = fsPath.split(/[/\\]/);
    return parts[parts.length - 1] || '';
}

module.exports = { surfaceOf, isDescription, isSample, relativeTo };
