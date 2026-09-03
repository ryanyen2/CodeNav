"""Build the scenario page: seven annotated screenshots and the scenario text."""
from pathlib import Path
import re
import sys

HERE = Path(__file__).parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "scenario.html")

CIRC = {c: str(i + 1) for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
CIRC.update({"㉑": "21", "㉒": "22", "㉓": "23"})


def marks(t):
    for ch, n in CIRC.items():
        t = t.replace(ch, f'<span class="n">{n}</span>')
    return t


def fig(name):
    svg = (HERE / f"{name}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<style>.*?</style>", "", svg, flags=re.S)
    return svg.replace('width="', 'data-w="', 1).replace('height="', 'data-h="', 1)


def figure(name, num, cap):
    return (f'<figure><div class="art">{fig(name)}</div>'
            f'<figcaption><b>Figure {num}.</b> {marks(cap)}</figcaption></figure>')


P = [
    (None, """Nadia is a developer who maintains a flocking simulation that runs on a
display in a museum lobby. They have recently installed CoDoc as an extension in their
editor. The simulation is about 500 lines of TypeScript across 13 files, and it draws a
flock of birds that steer by three local rules, a hawk that hunts them, a wind, and a
cycle from day to night. A coding agent has been changing the code alongside Nadia for
several weeks, and Nadia has not read most of what it wrote."""),

    ("First Steps:", """Nadia opens the repository. CoDoc opens a feature document
beside the source. It lists eighteen features in five groups ①, named for what the
simulation does rather than for the files the code sits in. Under each title CoDoc
lists the code that feature owns ②, and under that is a paragraph saying what the
feature is responsible for ③. "Edge behaviour" owns code in <code>world.ts</code> and
in <code>render/trails.ts</code>; neither file is named for the rule, and neither holds
all of it. Nadia can read the document three ways ④: as prose, as the outline alone, or
marked with who wrote each part."""),
    ("FIG", ("fig1-document", 1,
             "The feature document, open beside the source. ① eighteen features in five "
             "groups, ② the code each feature owns, ③ what it is responsible for, ④ three "
             "ways to read the same document.")),

    ("From a Question to a Path:", """Visitors have told Nadia that the flock jumps
across the display, and Nadia wants to know whether that is a fault or a decision.
Rather than open the physics code, Nadia asks the document."""),
    ("CMD", "/codoc:ask what happens when a boid reaches the edge of the world, and why does its trail break there?"),
    (None, """The agent reads the outline and the code, and answers by numbering four
features that already exist. A bar carries the question and a stepper through the stops
⑤, each stop sits on a feature ⑥, and on each one the clause that carries part of the
answer is picked out ⑦. The same numbers appear in the outline ⑧, and the stops are
grouped by the stage of the answer they belong to ⑨. The world wraps: a boid that
leaves one edge re-enters from the opposite one, and its trail is broken at the
boundary so that no false line is drawn back across the sky. The jump is a decision.
Nothing has been written to the document, and the toolbar still reads "in sync"."""),
    ("FIG", ("fig2-ask", 2,
             "The answer, drawn as a path over features that already exist. ⑤ the question "
             "and a stepper, ⑥ a numbered stop, ⑦ the clause that carries part of the "
             "answer, ⑧ the same numbers in the outline, ⑨ the stage this stop belongs to.")),

    ("From a Sentence to a Draft:", """On a display this size a teleport reads as a
fault, so Nadia decides the flock should turn instead. Nadia rewrites the description
of "Edge behaviour" to say what the world should do, and marks the phrase that matters
most in bold. The rewritten words are inked while the code does not say them yet ⑩, a
marker appears on the feature ⑪, and the bolded phrase is the part the agent will be
told to get right first ⑫. No code has been written, because an edit to the document
counts as documentation until Nadia hands it off. Had Nadia only been correcting a
stale sentence, the edit would have ended here."""),
    ("FIG", ("fig3-draft", 3,
             "An edit held as a draft. ⑩ the author's own words, inked while the code does "
             "not say them, ⑪ the draft marker, ⑫ the phrase marked in bold.")),

    ("From a Draft to a Diff:", """Nadia hands the draft off. CoDoc assembles one
request out of the new description, the bolded phrase, and the code that "Edge
behaviour" owns, and puts it in front of the coding session Nadia already has open. The
agent changes that code, and on the display the flock banks away from the walls instead
of crossing the frame. In the document the ink clears ⑬ and the marker fills ⑭, which
is CoDoc saying that the codebase now claims what the sentence claims."""),
    ("FIG", ("fig4-built", 4,
             "The same sentence once the code says it. ⑬ the ink clears, ⑭ the marker "
             "fills.")),

    ("When the Agent Has to Decide:", """The hawk's dive reads weakly on the display,
so Nadia turns to "Panic". Nadia selects the sentence that says what a frightened bird
does and comments on it: "Panicked boids should break formation and dive." The request
is underspecified on purpose, because Nadia has not said which of the three flocking
rules a frightened bird drops, or which way a dive points. The agent decides both,
changes three files, and writes what it decided back into the description. The feature
is marked because what was built differs from what was asked for ⑮, Nadia can keep the
new wording or restore their own ⑯, wording the build added sits on a green ground and
wording it dropped is struck where it stood ⑰, and the comment that caused it carries
the agent's reply ⑱. Reading it, Nadia sees that the agent threw away all three
flocking rules during a panic and read "dive" as straight down, and rewrites those two
clauses in place."""),
    ("FIG", ("fig5-panic", 5,
             "A comment, and what the agent decided. ⑮ the feature is marked because the "
             "built result differs from what was asked, ⑯ keep the new wording or restore "
             "your own, ⑰ green for what the build added and red for what it dropped, ⑱ the "
             "comment that caused it and the agent's reply.")),

    ("From Code back to the Document:", """A teammate pushes a change that adds a fixed
pillar to the world and a force that steers birds around it. Nothing in the document
asked for it. CoDoc reads the repository, finds code that no feature owns, and proposes
a feature for it, drawn at the position it would occupy if accepted ⑲, with its
description on the ground CoDoc uses for what the code did ⑳. Nadia accepts."""),
    ("FIG", ("fig6-proposal", 6,
             "A feature proposed from code. ⑲ a feature nobody wrote, drawn where it would "
             "sit, ⑳ its description, on the ground CoDoc uses for what the code did.")),

    ("From a Task to a Plan:", """Nadia wants the flock to settle at night rather than
fly at full speed until morning, and does not yet know how it should be organised.
Nadia asks for a plan instead of a change."""),
    ("CMD", "/codoc:plan at night the flock should settle and roost instead of flying at full speed until morning"),
    (None, """The agent answers with proposals in the document and writes no code. Nadia
can accept the whole plan or take the nodes one at a time ㉑, what accepting means is
said on the node rather than in a dialog ㉒, and the planned wording is drawn in the
gray CoDoc uses for what nobody has built ㉓. The agent proposes a new feature called
"Roosting", and amends two features the change would make untrue, one of which Nadia
never mentioned. Nadia accepts the plan, and only then does the agent write code."""),
    ("FIG", ("fig7-plan", 7,
             "A plan, before any code. ㉑ accept the whole plan or take the nodes one at a "
             "time, ㉒ what accepting means, said on the node, ㉓ planned wording in the gray "
             "CoDoc uses for what nobody has built.")),
]

FIGTABLE = [
    ("1", "The feature document", "① ② ③ ④"),
    ("2", "A question answered as a path", "⑤ ⑥ ⑦ ⑧ ⑨"),
    ("3", "An edit held as a draft", "⑩ ⑪ ⑫"),
    ("4", "The same sentence, once the code says it", "⑬ ⑭"),
    ("5", "A comment, and what the agent decided", "⑮ ⑯ ⑰ ⑱"),
    ("6", "A feature proposed from code", "⑲ ⑳"),
    ("7", "A plan, before any code", "㉑ ㉒ ㉓"),
]

PROV = [
    ("1", "<code>rehearse.sh reset-hard</code>, then open <code>.codoc/tree.codoc</code>."),
    ("2", "A live <code>claude -p \"/codoc:ask …\"</code> run. The walkthrough it drew is <code>.codoc/ask.json</code>."),
    ("3", "<code>demo_edit.py describe \"Edge behaviour\" …</code>, drained by the daemon."),
    ("4", "<code>codoc sync</code> then <code>codoc realize</code>, with the recorded agent take on PATH."),
    ("5", "<code>demo_edit.py comment \"Panic\" … --scope both</code>, then sync and realize."),
    ("6", "<code>woz.py surprise</code> then <code>codoc sync</code>, which raised <code>add_node \"Obstacle avoidance\"</code>."),
    ("7", "A live <code>claude -p \"/codoc:plan …\"</code> run, captured once the three proposals landed."),
]

body = []
for head, text in P:
    if head == "FIG":
        body.append(figure(*text))
    elif head == "CMD":
        body.append(f"<pre class=cmd>{text}</pre>")
    elif head is None:
        body.append(f"<p>{marks(' '.join(text.split()))}</p>")
    else:
        body.append(f"<p><b>{head}</b> {marks(' '.join(text.split()))}</p>")

rows = "\n".join(f"<tr><td class=fn>{n}</td><td>{w}</td><td class=st>{marks(s)}</td></tr>"
                 for n, w, s in FIGTABLE)
prov = "\n".join(f"<tr><td class=fn>{n}</td><td>{h}</td></tr>" for n, h in PROV)

HTML = f"""<title>Nadia and the flocking simulation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap">
<style>
:root {{
  --ground:#ffffff; --plate:#ffffff; --ink:#15171c; --body:#26292f; --muted:#5f656f;
  --faint:#949aa4; --rule:#e4e6ea; --soft:#f2f3f5; --link:#2b6693;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#101216; --plate:#f5f6f8; --ink:#e9ebef; --body:#c8cdd4; --muted:#949aa4;
    --faint:#6b717a; --rule:#242830; --soft:#1b1f26; --link:#7fb2da;
  }}
}}
:root[data-theme="dark"] {{
  --ground:#101216; --plate:#f5f6f8; --ink:#e9ebef; --body:#c8cdd4; --muted:#949aa4;
  --faint:#6b717a; --rule:#242830; --soft:#1b1f26; --link:#7fb2da;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ground); color:var(--body);
  font:400 17px/1.62 Inter,"Helvetica Neue",Arial,sans-serif; -webkit-font-smoothing:antialiased; }}
.page {{ max-width:1180px; margin:0 auto; padding:64px 32px 110px; }}
h1 {{ font:500 34px/1.2 Newsreader,Georgia,serif; color:var(--ink); margin:0 0 6px;
  letter-spacing:-.01em; }}
.sub {{ font:400 14px/1.5 "JetBrains Mono",monospace; color:var(--faint); margin:0 0 40px; }}
h2 {{ font:500 22px/1.3 Newsreader,Georgia,serif; color:var(--ink); margin:56px 0 14px; }}
p {{ margin:0 0 1.15em; max-width:68ch; }}
p b {{ color:var(--ink); font-weight:600; }}
code {{ font:400 .87em/1.4 "JetBrains Mono",ui-monospace,Menlo,monospace;
  background:var(--soft); padding:.1em .36em; border-radius:3px; color:var(--ink); }}
.n {{ display:inline-block; min-width:1.32em; height:1.32em; line-height:1.32em;
  text-align:center; border-radius:50%; background:var(--ink); color:var(--ground);
  font:700 11px/1.32em Inter,sans-serif; vertical-align:.08em; margin:0 .12em;
  font-variant-numeric:tabular-nums; padding:0 .25em; }}
pre.cmd {{ margin:1.2em 0; padding:13px 17px; background:var(--soft);
  border:1px solid var(--rule); border-radius:5px; white-space:pre-wrap;
  overflow-wrap:anywhere; max-width:68ch;
  font:400 13.5px/1.6 "JetBrains Mono",monospace; color:var(--ink); }}
figure {{ margin:34px 0 40px; }}
.art {{ background:var(--plate); border:1px solid var(--rule); border-radius:5px;
  padding:8px; overflow-x:auto; }}
.art svg {{ display:block; width:100%; height:auto; min-width:820px; }}
figcaption {{ margin-top:11px; font-size:14.5px; line-height:1.55; color:var(--muted);
  max-width:88ch; }}
figcaption b {{ color:var(--ink); font-weight:600; }}
figcaption .n, td.st .n {{ transform:scale(.88); }}
table {{ border-collapse:collapse; width:100%; max-width:860px; font-size:14.5px;
  margin-top:8px; }}
th,td {{ text-align:left; padding:9px 14px 9px 0; border-bottom:1px solid var(--rule);
  vertical-align:top; }}
th {{ font:500 11px/1.4 "JetBrains Mono",monospace; letter-spacing:.08em;
  text-transform:uppercase; color:var(--faint); }}
td.fn {{ font:500 13px/1.6 "JetBrains Mono",monospace; color:var(--ink); width:3em; }}
.note {{ border-left:2px solid var(--rule); padding-left:18px; color:var(--muted);
  font-size:15.5px; max-width:68ch; }}
@media (max-width:760px) {{ .page {{ padding:40px 18px 80px; }} }}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none!important; transition:none!important; }} }}
:focus-visible {{ outline:2px solid var(--link); outline-offset:3px; }}
</style>
<div class="page">
<h1>Example usage scenario for CoDoc</h1>
<p class="sub">boids-demo &middot; 18 features &middot; CoDoc extension, 2026-09-03</p>
{"".join(body)}

<h2>Figures</h2>
<table><tr><th>#</th><th>Shows</th><th>Steps</th></tr>{rows}</table>
<p class="note" style="margin-top:24px">Every figure is a screenshot of the CoDoc
extension running against the <code>boids-demo</code> repository, with a numbered
annotation layer drawn over it in one colour the product does not use. Nothing about
the interface is redrawn.</p>

<h2>How each state was reached</h2>
<table><tr><th>Fig.</th><th>Command</th></tr>{prov}</table>
<p class="note" style="margin-top:24px">The states were produced by driving the
workspace from the shell and letting the extension render what it found. Figures 2, 6
and 7 call a model, so their wording differs between runs; the titles and descriptions
in them are what those runs actually produced, read back out of the store rather than
transcribed.</p>
</div>
"""
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT} ({len(HTML)/1024/1024:.1f} MB)")
