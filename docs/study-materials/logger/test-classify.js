// Pins how files are sorted into surfaces. A file landing in the wrong surface
// silently changes "switches between the description and the code", which is a
// reported measure, so this runs in the bundle build.
//
//   node test-classify.js
const assert = require('assert');
const { surfaceOf, isDescription, relativeTo } = require('./classify');

const cases = [
    // The written description, in both conditions. These must never be "code",
    // or every switch between description and code disappears from the data.
    ['CLAUDE.md', 'document'],
    ['.codoc/tree.codoc', 'document'],
    ['tree.codoc', 'document'],
    // Source.
    ['hearth/build.py', 'code'],
    ['ember/digest.py', 'code'],
    ['templates/index.html', 'code'],
    ['feeds.toml', 'code'],
    ['hearth.toml', 'code'],
    // Tests are their own surface: opening a test is not the same act as
    // opening the code under test.
    ['tests/test_build.py', 'test'],
    ['test/x.py', 'test'],
    // Everything else.
    ['', 'other'],
    ['LICENSE', 'other'],
    ['_site/posts/a/index.html', 'code'],
];

let failed = 0;
for (const [file, want] of cases) {
    const got = surfaceOf(file);
    if (got !== want) {
        console.error(`FAIL  surfaceOf(${JSON.stringify(file)}) = ${got}, expected ${want}`);
        failed++;
    }
}

assert.strictEqual(isDescription('CLAUDE.md'), true);
assert.strictEqual(isDescription('README.md'), false);

// Paths are recorded relative to the project so two machines produce comparable
// logs. Anything outside the project falls back to its basename rather than
// leaking an absolute path from someone's home directory.
assert.strictEqual(relativeTo('/a/b', '/a/b/hearth/x.py'), 'hearth/x.py');
assert.strictEqual(relativeTo('/a/b', '/other/y.py'), 'y.py');
assert.strictEqual(relativeTo('', '/a/b/c.py'), 'c.py');

if (failed) {
    console.error(`${failed} failure(s)`);
    process.exit(1);
}
console.log(`study logger: ${cases.length} surface cases + 5 assertions pass`);
