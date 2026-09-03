# Walkthrough figures

Six figures for the paper walkthrough (`WALKTHROUGH.md`). Each one is a screenshot of
the recorded `tally` session with an annotation layer drawn over it. **Do not redraw
the interface.** An earlier version of these figures recreated the editor in SVG from
`vscode-codoc/src/webview/doc-view.css` and showed controls the product does not
have. If a surface is not in the recording, capture it before drawing it.

```
python3 f1_surface.py … f6_loopa.py     # one script per figure, writes figN-*.svg
python3 build_page.py out.html          # the page version, every SVG inlined
```

Run them with the CodeNav venv python. They need `fonttools` and `brotli`
(`uv pip install --python .venv/bin/python fonttools brotli`), because the legend
text is placed at an absolute x and that needs real glyph advances from Inter.

- `shots/` — the screenshots. Five were supplied at 2000 px wide; `s2-ask.png` is
  frame 39.5 s of `video/codoc-record-trimmed.mp4`, cropped to the editor and the
  terminal.
- `shot.py` — the annotation layer: numbered circles, leader lines, outlines around
  the control being pointed at, and a numbered legend. Coordinates are in the source
  image's own pixels; `coord_width` rescales them if you swap in a resized copy.
- `draw.py`, `metrics.py` — the text engine, shared with the rest of the figure work.

## Getting frames out of the recording

There is no ffmpeg on this machine and the Chrome extension is not connected, so
`tools/grab.swift` pulls exact frames through AVFoundation and `tools/sheet.swift`
tiles them into a labelled contact sheet, which is how the session was scanned.

```
swiftc -O tools/grab.swift -o /tmp/grab
swiftc -O tools/sheet.swift -o /tmp/sheet
/tmp/grab <video> <outdir> 39.5 63 74 118        # seconds
/tmp/sheet sheet.png 4 460 <frames...>            # cols, cell width
```

Rasterise an SVG to check it with headless Chrome:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --disable-gpu --hide-scrollbars --window-size=2040,1330 --virtual-time-budget=3000 \
  --screenshot=out.png "file://$PWD/fig1-surface.svg"
```

## For LaTeX

`./topdf.sh fig*.svg` writes a PDF beside each SVG, sized to the figure with no page
margin, via Chrome's printer. Use the PDFs in `\includegraphics`.

## A parallel set exists

`docs/paper/figures/walkthrough-{document,sync,plan,handoff}-annotated.pdf` is a
second set of annotated screenshots from the same session, wired into
`docs/paper/system-section.tex`. It has four figures and purple callouts numbered
continuously across them. This set has six, in red, and adds `/codoc:ask` and the
description rewrite. Pick one before the paper goes out; two annotation styles in one
paper will read as an error.
