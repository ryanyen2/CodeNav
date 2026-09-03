"""Figure 4 — the agent at work, in the open."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot
H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s4-working.png", scale=1.0, pad=10, coord_width=2000, legend_rows=2, legend_h=24)
s.ring(738, 428, 290, 28)           # Claude - reading categories.py
s.mark(1, 1112, 414, to=(1036, 438))
s.ring(430, 410, 240, 108)          # dots on the outline rows
s.mark(2, 385, 380, to=(430, 415))
s.ring(30, 645, 250, 275)           # dots on the changed files
s.mark(3, 305, 700, to=(285, 700))
s.ring(330, 1012, 1280, 88)         # the terminal
s.mark(4, 1660, 1064, to=(1614, 1064))
s.legend([
    (1, "Which feature the agent is inside, and which file it is reading."),
    (2, "The same, in the outline, so the reader can watch it move."),
    (3, "The files it has touched so far."),
    (4, "What it said it would do, before it wrote any of it."),
], cols=2, size=15)
s.save(H / f"fig4-working{OUT}.svg", "The agent at work")
print("fig4", s.w, s.h)
