"""Figure 3 — asking for a change on one sentence."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot
H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s3-comment.png", scale=0.9, pad=10, coord_width=2000, legend_rows=2, legend_h=24)
s.ring(705, 666, 1040, 104)        # the selected sentence
s.mark(1, 1810, 718, to=(1752, 718))
s.ring(578, 602, 262, 56)           # the B / Comment floating toolbar
s.mark(2, 520, 560, to=(600, 598))
s.ring(848, 630, 730, 34)           # the binding chips
s.mark(3, 1640, 646, to=(1584, 646))
s.ring(1205, 134, 540, 46)          # the three readings
s.mark(4, 1150, 96, to=(1230, 130))
s.legend([
    (1, "Select the sentence you disagree with."),
    (2, "The one thing you can do to it, which is to say what it should do instead."),
    (3, "The code this feature owns, which becomes the scope of the request."),
    (4, "Three readings of one document: what you are working on, the outline alone, and who wrote what."),
], cols=2, size=15)
s.save(H / f"fig3-comment{OUT}.svg", "Asking for a change on one sentence")
print("fig3", s.w, s.h)
