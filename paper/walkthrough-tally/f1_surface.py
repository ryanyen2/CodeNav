"""Figure 1 — the surface: the feature document, the code, and the agent's terminal."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot

H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s1-surface.png", pad=10, coord_width=2000, legend_rows=3, legend_h=24)

s.ring(1400, 52, 130, 34)                  # Codoc Tree
s.mark(1, 1290, 30, to=(1398, 58))
s.mark(2, 250, 300, to=(330, 330))         # the outline
s.ring(795, 426, 500, 32)                  # binding chips
s.mark(3, 1350, 442, to=(1300, 442))
s.ring(1615, 448, 300, 26)                 # codoc: label in the source
s.mark(4, 1600, 405, to=(1660, 445))
s.ring(320, 1015, 400, 105)                # the /codoc commands
s.mark(5, 285, 1000, to=(325, 1022))

s.legend([
    (1, "The feature document is an ordinary editor tab, open beside the source."),
    (2, "Twenty six features, grouped by what the program does with a statement."),
    (3, "Under each title, the code that feature owns."),
    (4, "The same link seen from the code side, above the function it names."),
    (5, "What the agent can do with the document, from its own prompt."),
], cols=2, size=15)
s.save(H / f"fig1-surface{OUT}.svg", "The CoDoc surface")
print("fig1 ok", s.w, s.h)
