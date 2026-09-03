#!/usr/bin/env bash
# SVG -> PDF for LaTeX. Chrome prints the page, so the page has to be exactly the
# figure: no margins, and a paper size that matches the viewBox.
set -euo pipefail
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for svg in "$@"; do
  base="${svg%.svg}"
  w=$(sed -n 's/.*viewBox="0 0 \([0-9.]*\) \([0-9.]*\)".*/\1/p' "$svg" | head -1)
  h=$(sed -n 's/.*viewBox="0 0 \([0-9.]*\) \([0-9.]*\)".*/\2/p' "$svg" | head -1)
  cat > /tmp/wrap.html <<HTML
<style>@page{size:${w}px ${h}px;margin:0}html,body{margin:0;padding:0}
img{display:block;width:${w}px;height:${h}px}</style>
<img src="file://$PWD/$svg">
HTML
  "$CHROME" --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
    --print-to-pdf="$base.pdf" --virtual-time-budget=4000 file:///tmp/wrap.html >/dev/null 2>&1
  echo "$base.pdf"
done
