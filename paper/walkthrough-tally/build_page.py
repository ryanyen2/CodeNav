"""Build the walkthrough page: the six annotated screenshots plus the paper text."""
from pathlib import Path
import re
import sys

HERE = Path(__file__).parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "walkthrough.html")


def fig(name):
    svg = (HERE / f"{name}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<style>.*?</style>", "", svg, flags=re.S)
    return svg.replace('width="', 'data-w="', 1).replace('height="', 'data-h="', 1)


def figure(name, num, caption):
    return (f'<figure class="plate"><div class="plate-art">{fig(name)}</div>'
            f'<figcaption><span class="fignum">Figure {num}</span>{caption}</figcaption></figure>')


def roles(dev, agent, codoc):
    return (f'<div class="roles">'
            f'<div><span class="rl">the developer</span><p>{dev}</p></div>'
            f'<div><span class="rl">the agent</span><p>{agent}</p></div>'
            f'<div><span class="rl">CoDoc</span><p>{codoc}</p></div></div>')


STEPS = [
    dict(n=1, id="ask", title="Asking the document a question",
         fig=("fig2-ask", 2,
              "The answer is a numbered path over features that already exist. Each stop "
              "highlights the clause that carries part of the answer and hangs one line "
              "beside it. The overlay writes nothing, which is why the toolbar still reads "
              "in sync."),
         body="""
<p>The developer wants to know how a payment ends up in a category, and asks in the
document rather than in a chat window.</p>
<pre class="cmd">/codoc:ask how the payment is put into the category</pre>
<p>The agent reads the tree and the code, then draws a numbered path over two
features that already exist. Each stop highlights the clause in that feature's own
prose that answers the question, and hangs one line beside it saying what the stop
contributes. The same numbers appear in the outline, so the path is navigable.</p>
<p>We made the answer a path through the document rather than a paragraph in a chat
pane, because a reader who finishes a chat answer still has to find the place in the
system it was about.</p>""",
         rl=roles("Asks about behaviour they can see and cannot explain.",
                  "Reads the tree and the code, then answers by drawing a path over features that exist.",
                  "Renders the path in place and writes nothing, so it is safe to ask mid-edit.")),

    dict(n=2, id="request", title="Asking for a change",
         fig=("fig3-comment", 3,
              "A request can also be attached to the sentence it is about. Selecting a "
              "sentence offers one action, and the code listed under that feature's title "
              "becomes the scope of the request."),
         body="""
<p>The developer wants two things: the merchant rules in a file they can edit, and a
summary at a finer grain than a whole month.</p>
<pre class="cmd">/codoc:plan i want to manage the categories myself without touching the source, and i want to see spending at a finer grain than a whole month</pre>
<p>A request can also be attached to the exact sentence it is about. Selecting a
sentence offers one action, which is to say what it should do instead. Both routes
end in the same place, which is a request that names a feature rather than a
file.</p>""",
         rl=roles("Says what should change, in one sentence, at the level of features.",
                  "Receives a request whose scope is the code that feature owns.",
                  "Turns a selection or a prompt into a request that names a feature.")),

    dict(n=3, id="working", title="The agent works where the developer can see it",
         fig=("fig4-working", 4,
              "While the agent reads the codebase, the document says which feature it is "
              "inside and which file it is reading. The outline carries the same marks, and "
              "the file tree shows what it has touched."),
         body="""
<p>While the agent reads the codebase, the document says which feature it is inside
and which file it is reading. The outline carries the same marks, and the file tree
shows what it has touched so far. In the terminal it states the shape it has in mind
before writing any of it.</p>
<p>We put the activity on the features rather than in a progress log, because the
question a reader has during a long run is which part of the system is being
changed, and a list of file paths does not answer it.</p>""",
         rl=roles("Watches which part of the system is being changed, not which files.",
                  "Reads the codebase and reports where it is as it goes.",
                  "Marks the feature the agent is inside, in the prose and in the outline.")),

    dict(n=4, id="plan", title="The plan arrives in the document, before any code",
         fig=("fig5-plan", 5,
              "A planned feature is drawn where it would sit, in the gray CoDoc uses for "
              "wording nobody has built, with its verdict on the node itself."),
         body="""
<p>The agent answers with proposals in the document. A planned feature is drawn
where it would sit, in the gray CoDoc uses for wording nobody has built, and its
verdict sits on the node itself rather than in a dialog. The row under the title
says what accepting means: the agent writes this, and the code will change.</p>
<p>Reviewing at the level of intent is cheaper than reviewing a diff, and it happens
before the diff exists. The developer is reading four sentences about what the
program will do, not four hundred lines about how.</p>""",
         rl=roles("Approves a design before any code exists, node by node.",
                  "Proposes the change as document nodes and writes nothing until a verdict lands.",
                  "Draws each proposal in the position it would occupy, with its verdict attached.")),

    dict(n=5, id="back", title="The code comes back into the document",
         fig=("fig6-loopa", 6,
              "CoDoc rewrites the descriptions the code has outgrown, struck where the old "
              "sentence stood and replaced in place. One change stays a proposal, because it "
              "would alter the shape of the document."),
         body="""
<p>The agent implements the accepted nodes, runs the tests, and stops. CoDoc then
reads the repository and finds descriptions the code has outgrown. It rewrites three
of them, struck where the old sentence stood and replaced in place, so the developer
can compare the claim with what the code now does. Keep them, or restore your own
wording on any one of them.</p>
<p>One change is still a proposal rather than a rewrite, because it would alter the
shape of the document rather than the wording of a feature. A change to the shape is
always a proposal a person approves, because that judgment is approximate.</p>""",
         rl=roles("Sees which of their own sentences the code outgrew, and rules on each.",
                  "Implements what was accepted, then stops.",
                  "Rewrites the wording the code outgrew, and proposes anything structural.")),
]

SHOTS = [
    ("0:00 to 0:18", "Reading the document. Twenty six features, plain prose, nothing marked.", "1", True),
    ("0:20 to 0:30", "Typing <code>/codoc:ask how the payment is put into the category</code>.", "", True),
    ("0:30", "The agent reads the tree and the code.", "", True),
    ("0:39 to 0:46", "The walkthrough is drawn: two numbered stops, notes in the margin.", "2", True),
    ("1:03", "Typing the change request as <code>/codoc:plan …</code>.", "3", True),
    ("1:06 to 1:12", "The agent reads the codebase; the document shows which feature it is in.", "4", False),
    ("1:14", "Four proposals in the toolbar, with Accept all and Accept and build.", "5", True),
    ("1:20 to 1:55", "The agent implements and runs the tests.", "", True),
    ("1:58 to 2:01", "The build lands; CoDoc rewrites the descriptions the code outgrew.", "6", True),
    ("2:04 to 2:41", "The tree is translated into Traditional Chinese.", "", True),
]

PROV = [
    ("The recording", "<code>video/codoc-record-trimmed.mp4</code>, 2 minutes 41 seconds, recorded 2026-08-20 on the <code>tally</code> workspace."),
    ("The feature tree", "All 26 titles and descriptions are <code>.codoc/tree.codoc</code> in that workspace as it stood at the end of the session."),
    ("Figures 1, 3, 4, 5, 6", "Screenshots of the session, supplied at 2000 pixels wide."),
    ("Figure 2", "Frame 39.5 s of the recording, cropped to the editor and the terminal."),
    ("Quoted text", "Every command and every line of agent output is read off the frame, not reconstructed."),
    ("The annotation layer", "Numbered circles, leader lines, and outlines around the control being pointed at, in one colour the product does not use. Nothing else was added and nothing was redrawn."),
]

steps_html = "".join(
    f'<section class="step" id="step-{st["id"]}">'
    f'<div class="step-head"><span class="stepno">{st["n"]}</span><h2>{st["title"]}</h2></div>'
    f'<div class="prose">{st["body"]}</div>{st["rl"]}{figure(*st["fig"])}</section>'
    for st in STEPS)

shots_html = "\n".join(
    f'<tr><td class="t">{t}</td><td>{w}</td><td class="fg">{f}</td>'
    f'<td class="chk">{"read off the frame" if v else "range"}</td></tr>'
    for t, w, f, v in SHOTS)

prov_html = "\n".join(
    f'<div class="prov"><span class="pk">{k}</span><p>{v}</p></div>' for k, v in PROV)

HTML = f"""<title>Co-authoring a spending summary</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&display=swap">
<style>
:root {{
  --ground:#f7f8fa; --surface:#ffffff; --plate:#ffffff;
  --ink:#14171d; --body:#33383f; --muted:#666d78; --faint:#98a0ab;
  --rule:#e2e5ea; --rule-soft:#eef0f4;
  --accent:#2f6f9e; --accent-soft:#e8f0f6; --mark:#c8362c;
  --shadow:0 1px 2px rgba(20,23,29,.05), 0 8px 24px -18px rgba(20,23,29,.35);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0f1116; --surface:#161920; --plate:#f4f5f7;
    --ink:#e8eaee; --body:#c3c8d0; --muted:#939aa5; --faint:#6b727d;
    --rule:#252932; --rule-soft:#1e222a;
    --accent:#79b3dd; --accent-soft:#1a2530; --mark:#e8756a;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -20px rgba(0,0,0,.8);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0f1116; --surface:#161920; --plate:#f4f5f7;
  --ink:#e8eaee; --body:#c3c8d0; --muted:#939aa5; --faint:#6b727d;
  --rule:#252932; --rule-soft:#1e222a;
  --accent:#79b3dd; --accent-soft:#1a2530; --mark:#e8756a;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -20px rgba(0,0,0,.8);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ground); color:var(--body);
  font:400 16.5px/1.68 Inter, "Helvetica Neue", Arial, sans-serif; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1264px; margin:0 auto; padding:0 32px 120px; }}
.col, .prose {{ max-width:66ch; }}
h1,h2,h3 {{ font-family:Newsreader, Georgia, "Times New Roman", serif; color:var(--ink);
  font-weight:500; text-wrap:balance; margin:0; letter-spacing:-.005em; }}
h1 {{ font-size:clamp(36px,5vw,58px); line-height:1.08; }}
h2 {{ font-size:29px; line-height:1.22; }}
h3 {{ font-size:20px; line-height:1.3; }}
p {{ margin:0 0 1.05em; }}
code {{ font:400 .875em/1.4 "JetBrains Mono", ui-monospace, Menlo, monospace;
  background:var(--rule-soft); padding:.12em .38em; border-radius:3px; color:var(--ink); }}
strong {{ font-weight:600; color:var(--ink); }}
header.mast {{ padding:76px 0 40px; border-bottom:1px solid var(--rule); }}
.eyebrow {{ font:500 11.5px/1 "JetBrains Mono", monospace; letter-spacing:.12em;
  text-transform:uppercase; color:var(--faint); margin-bottom:22px; }}
.lede {{ font-size:20px; line-height:1.55; color:var(--muted); margin-top:22px; max-width:62ch;
  font-family:Newsreader, Georgia, serif; }}
.meta {{ display:flex; flex-wrap:wrap; gap:8px 28px; margin-top:30px;
  font:400 13px/1.5 "JetBrains Mono", monospace; color:var(--faint); }}
.meta b {{ color:var(--muted); font-weight:500; }}
nav.toc {{ position:sticky; top:0; z-index:20; background:var(--ground);
  border-bottom:1px solid var(--rule); margin-bottom:56px; }}
nav.toc ol {{ list-style:none; display:flex; gap:22px; margin:0; padding:13px 0;
  overflow-x:auto; font:500 12px/1 "JetBrains Mono", monospace; }}
nav.toc a {{ color:var(--muted); text-decoration:none; white-space:nowrap; }}
nav.toc a:hover {{ color:var(--accent); }}
nav.toc li::before {{ content:attr(data-n); color:var(--faint); margin-right:7px; }}
section {{ margin:0 0 84px; }}
.step-head {{ display:flex; align-items:baseline; gap:18px; margin-bottom:26px; }}
.stepno {{ font:500 13px/1 "JetBrains Mono", monospace; color:var(--accent);
  border:1px solid var(--rule); border-radius:999px; padding:7px 12px; flex:0 0 auto; }}
pre.cmd {{ margin:1.3em 0; padding:14px 18px; background:var(--surface);
  border:1px solid var(--rule); border-radius:6px; white-space:pre-wrap; overflow-wrap:anywhere;
  font:400 13.5px/1.6 "JetBrains Mono", monospace; color:var(--ink); }}
.roles {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1px; margin:34px 0 0;
  background:var(--rule); border:1px solid var(--rule); border-radius:7px; overflow:hidden; }}
.roles > div {{ background:var(--surface); padding:18px 20px; }}
.rl {{ display:block; font:500 11px/1 "JetBrains Mono", monospace; letter-spacing:.1em;
  text-transform:uppercase; color:var(--faint); margin-bottom:10px; }}
.roles p {{ margin:0; font-size:14.5px; line-height:1.55; color:var(--body); }}
figure.plate {{ margin:38px 0 0; }}
.plate-art {{ background:var(--plate); border:1px solid var(--rule); border-radius:8px;
  padding:10px; overflow-x:auto; box-shadow:var(--shadow); }}
.plate-art svg {{ display:block; width:100%; height:auto; min-width:900px; }}
figcaption {{ margin-top:14px; font-size:14.5px; line-height:1.6; color:var(--muted); max-width:84ch; }}
.fignum {{ font:500 11.5px/1 "JetBrains Mono", monospace; letter-spacing:.09em;
  text-transform:uppercase; color:var(--accent); margin-right:11px; }}
.tablewrap {{ overflow-x:auto; border:1px solid var(--rule); border-radius:8px; background:var(--surface); }}
table {{ border-collapse:collapse; width:100%; min-width:760px; font-size:14.5px; }}
th, td {{ text-align:left; padding:13px 16px; border-bottom:1px solid var(--rule-soft); vertical-align:top; }}
th {{ font:500 11px/1.4 "JetBrains Mono", monospace; letter-spacing:.09em;
  text-transform:uppercase; color:var(--faint); background:var(--ground); }}
tr:last-child td {{ border-bottom:none; }}
td.t {{ font:400 13px/1.5 "JetBrains Mono", monospace; color:var(--ink);
  font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.fg {{ font:500 13px/1.5 "JetBrains Mono", monospace; color:var(--accent); }}
td.chk {{ font:400 12px/1.5 "JetBrains Mono", monospace; color:var(--faint); white-space:nowrap; }}
.prov {{ display:grid; grid-template-columns:minmax(170px,215px) 1fr; gap:24px;
  padding:15px 0; border-bottom:1px solid var(--rule-soft); }}
.prov:last-child {{ border-bottom:none; }}
.pk {{ font:500 12.5px/1.55 "JetBrains Mono", monospace; color:var(--ink); }}
.prov p {{ margin:0; font-size:14.5px; color:var(--muted); }}
.note {{ background:var(--accent-soft); border:1px solid var(--rule); border-radius:7px;
  padding:20px 24px; max-width:78ch; }}
.note p:last-child {{ margin-bottom:0; }}
.note h3 {{ margin-bottom:10px; }}
@media (max-width:860px) {{
  .wrap {{ padding:0 20px 80px; }}
  .roles {{ grid-template-columns:1fr; }}
  .prov {{ grid-template-columns:1fr; gap:6px; }}
}}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none !important; transition:none !important; }} }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:3px; border-radius:3px; }}
</style>

<div class="wrap">
<header class="mast">
  <div class="eyebrow">CoDoc &middot; example usage scenario</div>
  <h1>Co-authoring a spending summary</h1>
  <p class="lede">A developer and a coding agent change the same program through one
  document. Every figure below is a screenshot of the session with an annotation layer
  on top. Nothing about the interface is redrawn.</p>
  <div class="meta">
    <span><b>Program</b> tally, 11 Python files</span>
    <span><b>Tree</b> 26 features in 4 groups</span>
    <span><b>Session</b> 2026-08-20, 2 min 41 s</span>
  </div>
</header>

<nav class="toc"><ol>
  <li data-n="00"><a href="#program">The program</a></li>
  <li data-n="01"><a href="#step-ask">Asking</a></li>
  <li data-n="02"><a href="#step-request">Requesting</a></li>
  <li data-n="03"><a href="#step-working">Working</a></li>
  <li data-n="04"><a href="#step-plan">Planning</a></li>
  <li data-n="05"><a href="#step-back">Coming back</a></li>
  <li data-n="&#43;"><a href="#built">What we built</a></li>
  <li data-n="&#43;"><a href="#shots">Shot list</a></li>
  <li data-n="&#43;"><a href="#prov">Sources</a></li>
</ol></nav>

<section id="program">
  <div class="prose">
    <h2>The program</h2>
    <p><code>tally</code> reads bank statement CSV files and prints a spending
    summary. It is eleven Python files, and no single one of them is "how a payment
    gets a category". The reading is in <code>rows.py</code>, the sign convention in
    <code>money.py</code>, the matching in <code>categories.py</code>, the totals in
    <code>summary.py</code>, and the rules those parts have to obey in
    <code>tests/</code>. Reading the files tells you what the program computes. It
    does not tell you what the program is for.</p>
    <p>CoDoc opens a feature document beside the source. Twenty six features, grouped
    by what the program does with a statement: reading it, applying policies to it,
    producing a summary, and checking the result. Each feature is a title, the code it
    owns, and a paragraph saying what that part is responsible for.</p>
  </div>
  {figure("fig1-surface", 1, "The feature document open beside the source. The link between a feature and its code is visible from both sides.")}
</section>

<div class="prose" style="margin-bottom:46px">
  <div class="note">
    <h3>Who the subjects are</h3>
    <p>"We" means a design decision the authors made. "CoDoc" means what the running
    system does. "The developer" and "the agent" are the two parties who write the
    document. Nothing below says "we" for something that happens at run time.</p>
  </div>
</div>

{steps_html}

<section id="built">
  <div class="prose">
    <h2>What we built, and what it makes possible</h2>
    <p>We built four things, and each is a mechanism rather than a workflow. First, a
    document whose unit is a feature rather than a file, with links into the code that
    CoDoc keeps current on its own. Second, a request that names a feature, so its
    scope is the code that feature owns. Third, a proposal drawn into the document at
    the position it would take, so a change is reviewed where it will live. Fourth, a
    record of who wrote which wording and what each change was answering.</p>
    <p>What people can do with the four mechanisms is what the walkthrough shows. The
    developer can ask a question and get a reading path through the document instead
    of a paragraph to re-situate. They can ask for a change in one sentence and watch
    which feature the agent is inside while it works. They can approve a design before
    a diff exists. They can see which of their own sentences the code has outgrown,
    and decide one at a time whether to keep the new wording.</p>
  </div>
  <div class="tablewrap" style="margin-top:26px">
  <table>
    <tr><th>CoDoc does</th><th>The developer does</th><th>The agent does</th></tr>
    <tr><td>Keeps the record of which code implements which feature current across edits, renames, and moves</td>
        <td>Writes what each feature is for, and decides what to ask for</td>
        <td>Answers a question by drawing a path over features that already exist</td></tr>
    <tr><td>Draws every proposal in the position it would occupy</td>
        <td>Accepts, rejects, or edits every proposal</td>
        <td>Proposes a plan as document nodes before writing code</td></tr>
    <tr><td>Rewrites a description the code has outgrown, and offers to restore the old one</td>
        <td>Keeps the new wording or restores their own, one at a time</td>
        <td>Reports which feature it is inside while it works</td></tr>
  </table>
  </div>
</section>

<section id="shots">
  <div class="prose">
    <h2>Shot list</h2>
    <p>Timecodes are from the recording. The rows marked "read off the frame" were
    checked against the frame at that second; the others are the ranges around
    them.</p>
  </div>
  <div class="tablewrap" style="margin-top:26px">
  <table>
    <tr><th>Time</th><th>What happens</th><th>Fig.</th><th></th></tr>
    {shots_html}
  </table>
  </div>
  <div class="prose" style="margin-top:26px">
    <p>The translation at the end is a separate feature and is not part of the
    walkthrough above. It is in the list because it is in the recording.</p>
  </div>
</section>

<section id="prov">
  <div class="prose"><h2>Sources</h2></div>
  <div style="margin-top:22px; max-width:96ch">{prov_html}</div>
  <div class="prose" style="margin-top:34px">
    <div class="note">
      <h3>What the walkthrough does not show</h3>
      <p>The recording has no moment where a person edits a description and hands it
      off, so the walkthrough describes requests as they were actually made, which was
      by prompt and by comment. The authorship and history surfaces are not in the
      recording either. If either is wanted as a figure, it needs a new capture rather
      than a drawing.</p>
    </div>
  </div>
</section>
</div>
"""
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT} ({len(HTML)/1024/1024:.1f} MB)")
