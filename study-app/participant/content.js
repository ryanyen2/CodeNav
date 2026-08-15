// The project briefings, on the page.
//
// These used to be a markdown file the researcher shared on the call, and the
// page said "the researcher will show you a short page about this project". That
// was one context switch per condition at exactly the moment the participant was
// forming their model of the codebase, and it made the briefing depend on
// somebody remembering to send the right file. Everything is here now.
//
// Structured rather than markdown so it renders as real tables and code blocks,
// and so the same text can be checked by a test.

const HEARTH = {
    name: 'hearth',
    what: [
        'Hearth is a small static site generator. You give it a folder of markdown files and it gives you back a folder of finished HTML you could upload anywhere.',
        'It does the same job as Jekyll or Hugo, but someone wrote it from scratch in plain Python with no libraries. One person\'s blog runs on it.',
    ],
    flow: {
        caption: 'What it does with your files',
        lines: [
            ['content/posts/*.md', '_site/posts/<name>/index.html', 'one page per post'],
            ['content/*.md', '_site/<name>/index.html', 'about, colophon, and so on'],
            ['', '_site/index.html', 'the home page, newest first'],
            ['', '_site/tags/<tag>/index.html', 'one page per tag'],
            ['', '_site/archive/index.html', 'everything by year'],
            ['', '_site/feed.xml', 'the RSS feed'],
        ],
    },
    sample: {
        caption: 'Every markdown file starts with a block of settings between two lines of three dashes. Blogging tools call it the frontmatter.',
        code: `---
title: A Winter Stock
date: 2026-01-14
tags: [cooking, winter]
---

The stockpot came out on the first cold morning…`,
    },
    commands: [
        ['.venv/bin/hearth build', 'Build the site into _site/'],
        ['.venv/bin/hearth serve', 'Build, serve at localhost:8000, rebuild as you edit'],
        ['.venv/bin/hearth clean', 'Delete _site/ and start again'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ],
    commandNote: 'Run these inside the project folder. The .venv/bin/ at the front matters, because it runs the project\'s own copy of Python. A build prints one line, e.g. 12 pages, 3 rebuilt, aggregates rebuilt, took 0.04s.',
    layout: [
        ['hearth/', 'the source code, 13 files. This is what you will change.'],
        ['content/', "the sample site's markdown, 10 posts and 2 pages"],
        ['templates/', 'the HTML templates'],
        ['tests/', 'the tests'],
        ['_site/', 'the built site. Hearth generates it, so it is not checked in.'],
    ],
    words: [
        ['a draft', 'A post you have started but are not ready to publish. It sits alongside your finished posts, and the tool knows to leave it out of what gets published.'],
        ['the dev server', 'The local preview you get from hearth serve. It is for the author looking at their own site before publishing, and nobody else sees it.'],
    ],
};

const EMBER = {
    name: 'ember',
    what: [
        'Ember is a feed reader that writes a daily digest. It reads blog feeds into a local database, then builds one HTML page per day listing what arrived, plus a browsable archive and a log of new items.',
        'Someone wrote it from scratch in plain Python with no libraries. One person runs it on their own machine to keep up with a handful of blogs.',
    ],
    flow: {
        caption: 'What it does with your feeds',
        lines: [
            ['feeds.toml', 'a local sqlite database of items', 'the feeds to read'],
            ['fixtures/feeds/*.xml', '', 'the feed files themselves, RSS and Atom'],
            ['', '_digest/2026-08-11.html', 'one page per day of arrivals'],
            ['', '_digest/latest.html', 'a copy of the newest day'],
            ['', '_digest/archive/index.html', 'every day and every feed'],
            ['', '_digest/archive/search.json', 'the same items as data'],
            ['', '_digest/notifications.log', 'a line per new item'],
        ],
    },
    sample: {
        caption: 'Network access is switched off in this build, so a feed\'s url points at a local file and everything runs offline. Each feed is listed in feeds.toml like this.',
        code: `[[feed]]
name = "saltbox-kitchen"
url = "fixtures/feeds/saltbox-kitchen.xml"
max_items = 12`,
    },
    commands: [
        ['.venv/bin/python -m ember refresh', 'Read every feed and store what it holds'],
        ['.venv/bin/python -m ember digest', 'Write the digest pages, the archive, and the log'],
        ['.venv/bin/python -m ember archive', 'Write the archive pages on their own'],
        ['.venv/bin/python -m ember status', 'Show what has been read, and when'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ],
    commandNote: 'Run these inside the project folder. The .venv/bin/ at the front matters, because it runs the project\'s own copy of Python. A digest run prints one line, e.g. 14 days, 3 digests written, latest 2026-08-11. When there is nothing to do it says nothing to write.',
    layout: [
        ['ember/', 'the source code, 15 files. This is what you will change.'],
        ['feeds.toml', 'the feeds to read'],
        ['fixtures/', 'the sample feed files'],
        ['templates/', 'the HTML templates'],
        ['tests/', 'the tests'],
        ['_digest/', 'the generated pages. Ember writes them, so they are not checked in.'],
    ],
    words: [
        ['to mute a feed', 'To stop seeing it, without unsubscribing. You stay subscribed and the items keep arriving, you just do not want them in front of you.'],
        ['the digest', 'The daily page ember writes, listing what arrived that day.'],
    ],
};

export const PROJECTS = Object.freeze({ hearth: HEARTH, ember: EMBER });

/** Said once per condition, and identical in both, which is the point. */
export const RESPONSIBILITY = Object.freeze([
    'Work however you normally would, and use the coding agent as much or as little as you like.',
    'You are responsible for the result being correct, not only for the tests passing. Treat it as code you would put your name on.',
    'Afterwards we will ask you to explain the code: what it does, why it is built that way, and what you would change to extend it.',
]);

/**
 * How each condition is started, which was also read off a script before.
 *
 * The two differ, and that difference is the manipulation, so it is written down
 * once here rather than improvised on a call where one participant gets a fuller
 * explanation than the next.
 */
export const HOW_TO_START = Object.freeze({
    codoc: {
        title: 'The way of working with codoc',
        folder: (p) => `~/codoc-study/${p}`,
        steps: [
            ['Open the folder in VS Code.', null],
            ['Open a terminal inside VS Code and run this. Leave it running for the whole task.', '~/codoc-study/codoc watch'],
            ['Open the written description: press Cmd+Shift+P and run "codoc: Open".', null],
            ['Open two more terminals, one for the coding agent and one for running the project.', null],
        ],
        about: [
            'The written description is a tree of features. Each one names something the codebase does and points at the code that does it.',
            'It is yours to edit. When the code changes underneath it, codoc proposes a change to the description, and you accept or reject it inline.',
            'The coding agent can read it too, so anything you write there is something the agent can act on.',
        ],
    },
    baseline: {
        title: 'The way of working without codoc',
        folder: (p) => `~/codoc-study/${p}-baseline`,
        steps: [
            ['Open the folder in VS Code.', null],
            ['Start the coding agent in a terminal.', 'claude'],
            ['Open a second terminal for running the project.', null],
        ],
        about: [
            'The written description is CLAUDE.md in the project root. It holds the same text, describing the same features.',
            'It is yours to edit, in the ordinary editor. The coding agent reads it on its own and has been told to keep it current after every change it makes.',
        ],
    },
});
