// Bundles each page into one file.
//
// esbuild rather than a framework, because these are two pages with a few views
// and a framework would be more code than the apps. It also pins the Firebase and
// d3 versions into the output, so what is deployed is what was tested.
import { build } from 'esbuild';
import { mkdirSync, copyFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';

const pages = ['experimenter', 'participant'];
const watch = process.argv.includes('--watch');

mkdirSync('dist', { recursive: true });

// A landing page, so the bare domain explains itself rather than showing
// Firebase's "page not found", which reads like the study is broken.
copyFileSync('index.html', join('dist', 'index.html'));

// The participant bundle, served from the site itself.
//
// It used to be emailed. Sending a file and sending a link are two steps that
// can disagree, and they did: a rebuilt bundle went out to nobody who had
// already been emailed the old one, and there was nothing on either side to say
// so. Built by scripts/build-participant-bundle.sh straight into bundles/, so
// deploying the site and publishing the bundle are one action.
if (existsSync('bundles')) {
    mkdirSync(join('dist', 'bundles'), { recursive: true });
    for (const file of readdirSync('bundles')) {
        if (file.startsWith('.')) continue;
        copyFileSync(join('bundles', file), join('dist', 'bundles', file));
        const mb = statSync(join('bundles', file)).size / 1e6;
        console.log(`bundle  ${file}  ${mb.toFixed(1)} MB`);
    }
} else {
    // Loud, because the download button on the participant's setup page points
    // at a path that would 404 with nothing on screen to explain it.
    console.warn('\n  no bundles/ — the setup page download will 404.'
        + '\n  build one first: docs/study-materials/scripts/build-participant-bundle.sh\n');
}

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
