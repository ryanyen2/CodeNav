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
    ['scribe/build.py', 'code'],
    ['tally/digest.py', 'code'],
    ['templates/index.html', 'code'],
    ['feeds.toml', 'code'],
    ['scribe.toml', 'code'],
    // Tests are their own surface: opening a test is not the same act as
    // opening the code under test.
    ['tests/test_build.py', 'test'],
    ['test/x.py', 'test'],
    // Everything else.
    ['', 'other'],
    ['LICENSE', 'other'],
    // The sample documents, and what the program writes beside them. Looking at
    // output is not looking at the code that decided it, and counting it as code
    // inflated "did they open the code before acting" — the measure meant to tell
    // reading from trusting. The directory is the discriminator, not the suffix:
    // scribe reads fixtures/report.txt and writes fixtures/report.md.
    ['fixtures/report.txt', 'output'],
    ['fixtures/report.md', 'output'],
    ['fixtures/memo.txt', 'output'],
    // tally's samples are .csv, which used to fall through to 'other' and vanish
    // entirely — so the two projects were not even counted the same way.
    ['fixtures/current.csv', 'output'],
    ['scribe/fixtures/handbook.txt', 'output'],
    // Ordinary source keeps its surface; a .md that is not the description is code.
    ['README.md', 'code'],
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
assert.strictEqual(relativeTo('/a/b', '/a/b/scribe/x.py'), 'scribe/x.py');
assert.strictEqual(relativeTo('/a/b', '/other/y.py'), 'y.py');
assert.strictEqual(relativeTo('', '/a/b/c.py'), 'c.py');

if (failed) {
    console.error(`${failed} failure(s)`);
    process.exit(1);
}
console.log(`study logger: ${cases.length} surface cases + 5 assertions pass`);
