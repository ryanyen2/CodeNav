"""Figure 2 — asking the document a question."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot
H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s2-ask.png", scale=1.25, pad=10, coord_width=1570, legend_rows=3, legend_h=24)
s.ring(343, 112, 640, 32)          # the ask bar
s.mark(1, 1050, 128, to=(990, 128))
s.ring(356, 281, 26, 26)           # the stop chip on the heading
s.mark(2, 300, 250, to=(348, 278))
s.ring(364, 316, 240, 150)         # the margin note + its cite
s.mark(3, 660, 390, to=(612, 390))
s.mark(4, 60, 400, to=(105, 378))  # the same numbers in the outline
s.ring(240, 82, 76, 22)            # in sync
s.mark(5, 200, 50, to=(258, 78))
s.legend([
    (1, "The question, and a stepper through the stops it drew."),
    (2, "A numbered stop on the feature that carries part of the answer."),
    (3, "One line on what this stop contributes, and the line of code it points at."),
    (4, "The same numbers appear in the outline, so the path is navigable."),
    (5, "Still in sync: a walkthrough writes nothing to the document."),
], cols=2, size=15)
s.save(H / f"fig2-ask{OUT}.svg", "Asking the document a question")
print("fig2", s.w, s.h)
