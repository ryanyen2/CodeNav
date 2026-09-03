"""Build every walkthrough figure: a screenshot with a numbered annotation layer.

Coordinates are in each cropped screenshot's own pixels; `scale` sets how large the
figure is drawn, and the annotation radius stays in output pixels so the callouts
read the same size on every figure regardless of how tightly it was cropped.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from shot import Shot

H = Path(__file__).parent

FIGS = [
    dict(out="fig1-document", png="v0-teaser.png", scale=0.68, cols=2, rows=2,
         crop=(60, 60, 3240, 1540),
         title="The feature document",
         rings=[(725, 300, 605, 975), (1505, 852, 990, 30), (1505, 890, 990, 188),
                (2565, 208, 610, 34)],
         marks=[(1, 1410, 330, (1340, 330)), (2, 2570, 866, (2502, 866)),
                (3, 2570, 978, (2502, 978)), (4, 2420, 150, (2564, 204))],
         legend=[(1, "Eighteen features in five groups, named for what the simulation does."),
                 (2, "Under each title, the code that feature owns."),
                 (3, "Under that, what the feature is responsible for."),
                 (4, "Three ways to read the same document.")]),

    dict(out="fig2-ask", png="s2-ask.png", scale=0.86, cols=2, rows=3,
         title="A question answered as a path",
         rings=[(360, 40, 1400, 24), (1356, 168, 28, 22), (1380, 240, 590, 46),
                (80, 72, 28, 20), (1382, 128, 100, 20)],
         marks=[(5, 1830, 52, (1770, 52)), (6, 1286, 130, (1350, 164)),
                (7, 2040, 262, (1980, 262)), (8, 176, 58, (112, 72)),
                (9, 1570, 106, (1490, 130))],
         legend=[(5, "The question, and a stepper through the stops it drew."),
                 (6, "A numbered stop on a feature that already exists."),
                 (7, "The clause that carries part of the answer."),
                 (8, "The same numbers in the outline."),
                 (9, "The stage of the answer this stop belongs to.")]),

    dict(out="fig3-draft", png="s3-draft.png", scale=1.85, cols=2, rows=2,
         title="An edit held as a draft",
         rings=[(132, 116, 700, 76), (308, 60, 38, 20), (211, 118, 142, 20)],
         marks=[(10, 890, 150, (838, 150)), (11, 400, 40, (346, 62)),
                (12, 168, 204, (238, 142))],
         legend=[(10, "The author's own words, inked while the code does not say them yet."),
                 (11, "A draft marker: the edit is recorded and nothing has run."),
                 (12, "The phrase marked in bold rides in as the part to get right first.")]),

    dict(out="fig4-built", png="s4-built.png", scale=1.85, cols=2, rows=1,
         title="The same sentence, once the code says it",
         rings=[(132, 46, 700, 52), (194, 8, 20, 18)],
         marks=[(13, 890, 72, (838, 72)), (14, 300, 4, (222, 12))],
         legend=[(13, "The ink clears when the codebase says what the sentence claims."),
                 (14, "The marker fills: the claim reached the code.")]),

    dict(out="fig5-panic", png="s5-panic.png", scale=0.96, cols=2, rows=2,
         title="A comment, and what the agent decided",
         rings=[(208, 30, 34, 22), (448, 30, 148, 22), (52, 80, 840, 192),
                (1538, 108, 348, 172)],
         marks=[(15, 150, 12, (210, 28)), (16, 560, 6, (525, 26)),
                (17, 960, 176, (900, 176)), (18, 1490, 194, (1530, 194))],
         legend=[(15, "The feature is marked because the built result differs from what was asked."),
                 (16, "Keep the new wording, or restore your own."),
                 (17, "Green behind wording the build added, red behind wording it dropped."),
                 (18, "The comment that caused it, and the agent's reply.")]),

    dict(out="fig6-proposal", png="s6-proposal.png", scale=1.85, cols=2, rows=1,
         title="A feature proposed from code",
         rings=[(106, 62, 124, 26), (112, 96, 362, 102)],
         marks=[(19, 310, 74, (236, 74)), (20, 560, 148, (480, 148))],
         legend=[(19, "A feature nobody wrote, proposed where it would sit."),
                 (20, "Its description, on the ground CoDoc uses for what the code did.")]),

    dict(out="fig7-plan", png="s7-plan.png", scale=0.86, cols=2, rows=2,
         title="A plan, before any code",
         rings=[(1726, 12, 296, 24), (300, 292, 640, 26), (218, 402, 580, 70)],
         marks=[(21, 1650, 96, (1800, 42)), (22, 1010, 304, (950, 304)),
                (23, 900, 436, (806, 436))],
         legend=[(21, "Accept the whole plan, or take the nodes one at a time."),
                 (22, "What accepting means, said on the node rather than in a dialog."),
                 (23, "Planned wording, in the gray CoDoc uses for what nobody has built.")]),
]

for f in FIGS:
    s = Shot(H / "shots" / f["png"], scale=f["scale"], pad=10, crop=f.get("crop"),
             coord_width=0, legend_rows=f["rows"], legend_h=24)
    for r in f["rings"]:
        if r[2] and r[3]:
            s.ring(*r)
    for m in f["marks"]:
        s.mark(m[0], m[1], m[2], to=m[3])
    s.legend(f["legend"], cols=f["cols"], size=15)
    s.save(H / f"{f['out']}.svg", f["title"])
    print(f["out"], int(s.w), int(s.h))
