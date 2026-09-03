# Walkthrough figures

Seven figures for `SCENARIO.md`. Each is a screenshot of the CoDoc extension with a
numbered annotation layer. **Do not redraw the interface.** An earlier version of these
figures recreated the editor in SVG from `vscode-codoc/src/webview/doc-view.css` and
showed controls the product does not have.

```
python3 build_figs.py          # every figure, from shots/ into figN-*.svg
./topdf.sh fig*.svg            # a PDF beside each SVG, for \includegraphics
```

Run them with the CodeNav venv python; they need `fonttools` and `brotli`
(`uv pip install --python .venv/bin/python fonttools brotli`) because the legend text
is placed at an absolute x and that needs real glyph advances from Inter.

- `shots/` — the screenshots, at 2x.
- `shot.py` — the annotation layer, and SVG-side cropping. Pass `crop=(x, y, w, h)` and
  every annotation coordinate stays in the screenshot's own pixels, which is the only
  space that can be measured off the screenshot. Do not crop with `sips`: its
  `--cropOffset` is relative to the centre, not the top left, and it silently clamps.
- `build_figs.py` — one table entry per figure: the crop, the outlines, the numbered
  marks, and the legend. Callout numbers run continuously across the whole set.
- `tools/winlist.swift` — lists on-screen windows with their CoreGraphics ids, so a
  capture can target one window without Accessibility permission.
- `tools/grab.swift`, `tools/sheet.swift` — pull exact frames out of a video and tile
  them into a contact sheet. Left from working with the recorded session.

`PROVENANCE.md` says how each captured state was reached.
