"""Figure 6 — the code changes, and the document says what it now claims."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot
H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s6-loopa.png", scale=1.0, pad=10, coord_width=2000, legend_rows=3, legend_h=24)
s.ring(766, 80, 252, 34)            # codoc rewrote 3 descriptions
s.mark(1, 720, 46, to=(790, 76))
s.ring(1018, 80, 118, 34)           # Keep all (3)
s.mark(2, 1090, 44, to=(1075, 76))
s.ring(455, 366, 725, 74)           # the struck sentence
s.mark(3, 1230, 400, to=(1184, 400))
s.ring(455, 424, 725, 140)          # the sentence codoc wrote instead
s.mark(4, 1230, 500, to=(1184, 500))
s.ring(1150, 80, 268, 34)           # Accept all (1) / Reject all (1)
s.mark(5, 1288, 158, to=(1288, 118))
s.legend([
    (1, "The code moved first, so CoDoc rewrote the descriptions that had stopped being true."),
    (2, "Keep all of them, or restore your own wording on any one."),
    (3, "The sentence the codebase no longer supports, struck where it stood."),
    (4, "The sentence CoDoc wrote in its place, on the ground it uses for what the code did."),
    (5, "One change still needs a verdict, because it would alter the shape of the document."),
], cols=2, size=15)
s.save(H / f"fig6-loopa{OUT}.svg", "Code coming back into the document")
print("fig6", s.w, s.h)
