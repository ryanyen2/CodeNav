# The project you will work on

Read this page. It takes about two minutes. Ask about anything on it before we
start.

## What ember is

Ember is a feed reader that writes a daily digest. It reads blog feeds into a
local database, then builds one HTML page per day listing what arrived, plus a
browsable archive and a log of new items.

Someone wrote it from scratch in plain Python and it uses no libraries. One
person runs it on their own machine to keep up with a handful of blogs.

## What it does with your feeds

```
feeds.toml           the feeds to read
fixtures/feeds/*.xml the feed files themselves (RSS and Atom)
                 →   a local sqlite database of items
                 →   _digest/2026-08-11.html   one page per day of arrivals
                 →   _digest/latest.html       a copy of the newest day
                 →   _digest/archive/index.html    every day and every feed
                 →   _digest/archive/<feed>/index.html   one feed's history
                 →   _digest/archive/search.json   the same items as data
                 →   _digest/notifications.log     a line per new item
```

Network access is switched off in this build, so a feed's url points at a local
file and everything runs offline.

Each feed is listed in `feeds.toml` like this:

```
[[feed]]
name = "saltbox-kitchen"
url = "fixtures/feeds/saltbox-kitchen.xml"
max_items = 12
```

## The commands you will use

Run these from inside the project folder. The `.venv/bin/` at the front matters,
because it runs the project's own copy of Python.

| Command | What it does |
| --- | --- |
| `.venv/bin/python -m ember refresh` | Read every feed and store what it holds |
| `.venv/bin/python -m ember digest` | Write the digest pages, the archive, and the log |
| `.venv/bin/python -m ember archive` | Write the archive pages on their own |
| `.venv/bin/python -m ember status` | Show what has been read, and when |
| `.venv/bin/python -m pytest tests/ -q` | Run the tests |

A digest run prints one line, e.g., `14 days, 3 digests written, latest 2026-08-11`.
When there is nothing to do it says `nothing to write`.

## Where things live

```
ember/       the source code, 15 files. This is what you will change.
feeds.toml   the feeds to read
fixtures/    the sample feed files
templates/   the HTML templates
tests/       the tests
_digest/     the generated pages. Ember writes them, so they are not checked in.
```

## Two words in the task

- To mute a feed is to stop seeing it, without unsubscribing. You stay
  subscribed and the items keep arriving, you just do not want them in front of
  you.
- The digest is the daily page ember writes, listing what arrived that day.

## What we are asking of you

You will get a short task card. Work however you normally would, and use the
coding agent as much or as little as you like.

Two things are worth knowing. First, you are responsible for the result being
correct, not only for the tests passing, so treat it as code you would put your
name on. Second, we will ask you afterwards to explain the code, including what
it does, why it is built the way it is, and what you would change to extend it.

The task card is short on purpose. Anything it does not specify is yours to
decide, and we will ask you about those decisions, so make them on purpose.
