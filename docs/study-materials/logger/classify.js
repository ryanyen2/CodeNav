// Which of the study's surfaces a file belongs to.
//
// Kept apart from extension.js so it can be tested without a running VS Code.
// The three surfaces are what "switches between the description and the code"
// counts, so a file landing in the wrong one silently changes a result.

/** The written description, in either condition. */
function isDescription(file) {
    return file === 'CLAUDE.md' || file.endsWith('/tree.codoc') || file.endsWith('tree.codoc');
}

function surfaceOf(file) {
    if (!file) return 'other';
    if (isDescription(file)) return 'document';
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

module.exports = { surfaceOf, isDescription, relativeTo };
