"""Figure 5 — the agent plans the change in the document before writing code."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import os
from shot import Shot
H = Path(__file__).parent
SHOTS = H / os.environ.get("CODOC_SHOTS", "shots")
OUT = os.environ.get("CODOC_FIG_SUFFIX", "")
s = Shot(SHOTS / "s5-plan.png", scale=1.0, pad=10, coord_width=2000, legend_rows=3, legend_h=24)
s.ring(578, 96, 150, 36)            # 3 proposals
s.mark(1, 530, 60, to=(600, 92))
s.ring(1198, 96, 272, 36)           # Accept all (3, 3 to build)
s.mark(2, 1350, 50, to=(1350, 92))
s.ring(728, 592, 720, 48)           # the planned node title
s.mark(3, 1520, 612, to=(1454, 612))
s.ring(728, 636, 720, 34)           # from a plan / Accept & build
s.mark(4, 1520, 700, to=(1454, 664))
s.ring(378, 420, 260, 76)           # the planned rows in the outline
s.ring(415, 618, 230, 32)
s.mark(5, 340, 390, to=(390, 424))
s.legend([
    (1, "Three proposals, and no code written for any of them."),
    (2, "Accept the whole plan, or take the nodes one at a time."),
    (3, "A planned feature is drawn where it would sit, in the gray CoDoc uses for wording nobody has built."),
    (4, "What accepting means, said on the node rather than in a dialog."),
    (5, "The outline shows the planned nodes in position too."),
], cols=2, size=15)
s.save(H / f"fig5-plan{OUT}.svg", "Planning in the document")
print("fig5", s.w, s.h)
