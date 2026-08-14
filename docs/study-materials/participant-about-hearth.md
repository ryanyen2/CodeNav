# The project you will work on

Read this page. It takes about two minutes. Ask about anything on it before we
start.

## What hearth is

Hearth is a small static site generator. You give it a folder of markdown files
and it gives you back a folder of finished HTML that you could upload anywhere.

It does the same job as Jekyll, Hugo, or Eleventy, but someone wrote it from
scratch in plain Python and it uses no libraries. One person's blog runs on it.

## What it does with your files

```
content/posts/*.md   →   _site/posts/<name>/index.html   one page per post
content/*.md         →   _site/<name>/index.html         about, colophon, and so on
                     →   _site/index.html                the home page, newest first
                     →   _site/tags/<tag>/index.html     one page per tag
                     →   _site/archive/index.html        everything by year
                     →   _site/feed.xml                  the RSS feed
                     →   _site/sitemap.xml               the sitemap
```

Every markdown file starts with a small block of settings between two lines of
three dashes. Blogging tools call that block the frontmatter.

```
---
title: A Winter Stock
date: 2026-01-14
tags: [cooking, winter]
---

The stockpot came out on the first cold morning…
```

## The commands you will use

Run these from inside the project folder. The `.venv/bin/` at the front matters,
because it runs the project's own copy of Python.

| Command | What it does |
| --- | --- |
| `.venv/bin/hearth build` | Build the site into `_site/` |
| `.venv/bin/hearth serve` | Build the site, serve it at http://localhost:8000, and rebuild whenever you edit a file |
| `.venv/bin/hearth clean` | Delete `_site/` and start again |
| `.venv/bin/python -m pytest tests/ -q` | Run the tests |

A build prints one line, e.g., `12 pages, 3 rebuilt, aggregates rebuilt, took 0.04s`.

## Where things live

```
hearth/       the source code, 13 files. This is what you will change.
content/      the sample site's markdown, 10 posts and 2 pages
templates/    the HTML templates
tests/        the tests
_site/        the built site. Hearth generates it, so it is not checked in.
```

## Two words in the task

- A draft is a post you have started writing but are not ready to publish. It
  sits alongside your finished posts, and the tool knows to leave it out of what
  gets published.
- The dev server is the local preview you get from `hearth serve`. It is for the
  author looking at their own site before publishing, and nobody else sees it.

## What we are asking of you

You will get a short task card. Work however you normally would, and use the
coding agent as much or as little as you like.

Two things are worth knowing. First, you are responsible for the result being
correct, not only for the tests passing, so treat it as code you would put your
name on. Second, we will ask you afterwards to explain the code, including what
it does, why it is built the way it is, and what you would change to extend it.

The task card is short on purpose. Anything it does not specify is yours to
decide, and we will ask you about those decisions, so make them on purpose.
