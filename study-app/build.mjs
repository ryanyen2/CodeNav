// Bundles each page into one file.
//
// esbuild rather than a framework, because these are two pages with a few views
// and a framework would be more code than the apps. It also pins the Firebase and
// d3 versions into the output, so what is deployed is what was tested.
import { build } from 'esbuild';
import { mkdirSync, copyFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const pages = ['experimenter', 'participant'];
const watch = process.argv.includes('--watch');

mkdirSync('dist', { recursive: true });

// A landing page, so the bare domain explains itself rather than showing
// Firebase's "page not found", which reads like the study is broken.
copyFileSync('index.html', join('dist', 'index.html'));

for (const page of pages) {
    let entries;
    try {
        entries = readdirSync(page).filter((f) => f === 'app.js');
    } catch {
        continue;   // a page that does not exist yet
    }
    if (!entries.length) continue;

    mkdirSync(join('dist', page), { recursive: true });
    for (const asset of readdirSync(page)) {
        if (asset.endsWith('.html') || asset.endsWith('.css')) {
            copyFileSync(join(page, asset), join('dist', page, asset));
        }
    }

    const ctx = await build({
        entryPoints: [join(page, 'app.js')],
        bundle: true,
        format: 'esm',
        target: 'es2022',
        minify: !watch,
        sourcemap: watch,
        outfile: join('dist', page, 'app.js'),
        logLevel: 'info',
    });
    void ctx;
}

console.log('built', pages.join(' and '));
