"""Drawing primitives for the walkthrough figures.

Every colour here is read off the extension's own stylesheet
(`vscode-codoc/src/webview/doc-view.css`) and resolved against the light theme's
white page, so a figure cannot quietly invent a hue the product does not use. The
settlement grammar is the load-bearing part: human = blue ink, plan = gray by
opacity, code = a green or red ground. See the CHANNEL INKS block in that file.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

import metrics

# ── the product's palette, resolved over white ────────────────────────────────
BG = "#ffffff"
FG = "#1c1c1c"
FG_BODY = "#454545"
FG_MUTED = "#828282"
FG_FAINT = "#a9a9a9"
RULE = "#e4e4e4"
RULE_SOFT = "#f0f0f0"
HUMAN = "#5aa6e0"          # --st-human
HUMAN_CMT = "#4f8dbd"      # committed: mixed 82% toward the foreground
ADD_INK = "#6f9e78"        # --st-code-add
DEL_INK = "#c07b7b"        # --st-code-del
ADD_BG = "#e8f0e9"         # the add ground at --st-ground (16%)
DEL_BG = "#f5eaea"         # the cut ground at 16%
ADD_BG_SOFT = "#f2f7f3"    # a proposed ground, 60% of the ground
PLAN_PROP = "#a0a0a0"      # foreground at --st-plan-proposed (0.42)
PLAN_ACC = "#696969"       # foreground at --st-plan-accepted (0.66)
REF_FG = "#525252"
REF_BG = "#eaeaea"
ACC_ADD = "#66bb6a"
ACC_RET = "#d96666"
ACC_AMD = "#e6c200"
BLAME_H = "#9cc9ec"        # blame underlines: human 60%, agent 70%, loop 65%
BLAME_A = "#606060"
BLAME_L = "#a1c0a7"
SEL_BG = "#eef2f6"
CARD_BG = "#fcfcfc"

# ── the annotation layer, which is ours and not the product's ─────────────────
ANN = "#141414"
ANN_FG = "#ffffff"

DOC = "Inter, 'Inter var', 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace"

STYLES: dict[str, dict] = {
    "body": {},
    "b": {"weight": 700},
    "i": {"italic": True},
    "muted": {"fill": FG_MUTED},
    "faint": {"fill": FG_FAINT},
    "ref": {"family": "mono", "scale": 0.80, "fill": REF_FG, "chip": REF_BG, "pre": "⟢ "},
    # human channel — the ink
    "ink": {"fill": HUMAN},
    "inkb": {"fill": HUMAN, "weight": 700},
    "inkc": {"fill": HUMAN_CMT},
    "inkcb": {"fill": HUMAN_CMT, "weight": 700},
    # code channel — the ground
    "add": {"bg": ADD_BG},
    "addb": {"bg": ADD_BG, "weight": 700},
    "cut": {"bg": DEL_BG, "strike": DEL_INK},
    "prop_add": {"bg": ADD_BG_SOFT, "under_dot": ADD_INK},
    # plan channel — the opacity
    "plan": {"fill": PLAN_PROP},
    "planb": {"fill": PLAN_PROP, "weight": 700},
    "plana": {"fill": PLAN_ACC},
    # a plan's CUT: words on screen that the plan proposes to drop. Struck in the
    # plan's own faded ink, never in the code channel's red — nothing was built.
    "plancut": {"fill": PLAN_PROP, "strike": PLAN_PROP},
    "planab": {"fill": PLAN_ACC, "weight": 700},
    # history — blame per span
    "blh": {"under": BLAME_H},
    "blhb": {"under": BLAME_H, "weight": 700},
    "bla": {"under": BLAME_A},
    "blab": {"under": BLAME_A, "weight": 700},
    "bll": {"under": BLAME_L},
    # a commented span: visible at rest as a dotted rule, no wash
    "cmt": {"under_dot": "#8f8f8f"},
    # the ask overlay's highlighted quote
    "quote": {"bg": "#f2f2f2", "qrule": "#cccccc"},
    "quoteb": {"bg": "#f2f2f2", "qrule": "#cccccc", "weight": 700},
    "none": {"fill": FG_BODY},
}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Fig:
    def __init__(self, w: float, h: float, bg: str = BG):
        self.w, self.h, self.bg = w, h, bg
        self.parts: list[str] = []
        self.glyphs: set[str] = set()
        self.mono_glyphs: set[str] = set()

    # ── primitives ────────────────────────────────────────────────────────────
    def rect(self, x, y, w, h, fill="none", stroke="none", rx=0, sw=1, op=None, dash=None):
        a = f'x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"'
        if stroke != "none":
            a += f' stroke="{stroke}" stroke-width="{sw}"'
        if rx:
            a += f' rx="{rx}"'
        if op is not None:
            a += f' opacity="{op}"'
        if dash:
            a += f' stroke-dasharray="{dash}"'
        self.parts.append(f"<rect {a}/>")

    def line(self, x1, y1, x2, y2, stroke=RULE, sw=1, dash=None, cap="butt", op=None):
        a = (f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
             f'stroke="{stroke}" stroke-width="{sw}" stroke-linecap="{cap}"')
        if dash:
            a += f' stroke-dasharray="{dash}"'
        if op is not None:
            a += f' opacity="{op}"'
        self.parts.append(f"<line {a}/>")

    def circle(self, cx, cy, r, fill="none", stroke="none", sw=1, op=None):
        a = f'cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"'
        if stroke != "none":
            a += f' stroke="{stroke}" stroke-width="{sw}"'
        if op is not None:
            a += f' opacity="{op}"'
        self.parts.append(f"<circle {a}/>")

    def path(self, d, fill="none", stroke="none", sw=1, cap="round", op=None, dash=None):
        a = f'd="{d}" fill="{fill}"'
        if stroke != "none":
            a += f' stroke="{stroke}" stroke-width="{sw}" stroke-linecap="{cap}" stroke-linejoin="round"'
        if op is not None:
            a += f' opacity="{op}"'
        if dash:
            a += f' stroke-dasharray="{dash}"'
        self.parts.append(f"<path {a}/>")

    def text(self, x, y, s, size=13, family="doc", weight=400, fill=FG_BODY,
             anchor="start", op=None, italic=False, tracking=0.0):
        if not s:
            return 0.0
        (self.mono_glyphs if family == "mono" else self.glyphs).update(s)
        fam = MONO if family == "mono" else DOC
        a = (f'x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size:.2f}" '
             f'font-weight="{weight}" fill="{fill}"')
        if anchor != "start":
            a += f' text-anchor="{anchor}"'
        if italic:
            a += ' font-style="italic"'
        if tracking:
            a += f' letter-spacing="{tracking * size:.2f}"'
        if op is not None:
            a += f' opacity="{op}"'
        self.parts.append(f"<text {a}>{esc(s)}</text>")
        return metrics.width(s, size, family, weight, tracking)

    def image(self, x, y, w, h, png: Path, rx=0, clip_id=None):
        data = base64.b64encode(Path(png).read_bytes()).decode("ascii")
        extra = ""
        if rx:
            cid = clip_id or f"clip{len(self.parts)}"
            self.parts.append(
                f'<clipPath id="{cid}"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" rx="{rx}"/></clipPath>')
            extra = f' clip-path="url(#{cid})"'
        self.parts.append(
            f'<image x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"{extra} '
            f'preserveAspectRatio="xMidYMid slice" '
            f'xlink:href="data:image/png;base64,{data}"/>')

    # ── the styled-paragraph engine ───────────────────────────────────────────
    def para(self, x, y, box_w, runs, size=13.5, leading=1.62, fill=FG_BODY,
             align_last=False, opacity=None):
        """Lay out a list of (text, style) runs into `box_w`, returning the baseline
        y of the line after the last one. Every fragment is placed at an absolute x
        so a ground or an underline lands exactly under its own words."""
        line_h = size * leading
        tokens: list[tuple[str, str]] = []
        for text, style in runs:
            if style == "ref":
                tokens.append((text, style))
                continue
            buf = ""
            for ch in text:
                buf += ch
                if ch == " ":
                    tokens.append((buf, style))
                    buf = ""
            if buf:
                tokens.append((buf, style))

        def tok_w(t, style):
            st = STYLES.get(style, {})
            if style == "ref":
                s2 = size * st.get("scale", 0.8)
                return metrics.width(st.get("pre", "") + t, s2, "mono", 400) + 10
            return metrics.width(t, size, "doc", st.get("weight", 400))

        lines: list[list[tuple[str, str]]] = [[]]
        used = 0.0
        for t, style in tokens:
            w = tok_w(t, style)
            if used + w > box_w and lines[-1]:
                lines.append([])
                used = 0.0
                t = t  # keep the token; leading spaces are trimmed at draw time
                if t.strip() == "":
                    continue
                w = tok_w(t, style)
            lines[-1].append((t, style))
            used += w

        by = y
        for li, line in enumerate(lines):
            cx = x
            # trim a leading space that a wrap pushed to the head of a line
            if line and line[0][0].startswith(" ") and line[0][0].strip():
                line[0] = (line[0][0].lstrip(), line[0][1])
            for t, style in line:
                st = STYLES.get(style, {})
                if style == "ref":
                    s2 = size * st.get("scale", 0.8)
                    label = st.get("pre", "") + t
                    w = metrics.width(label, s2, "mono", 400)
                    self.rect(cx, by - s2 * 0.98, w + 10, s2 * 1.5, fill=st["chip"], rx=3)
                    self.text(cx + 5, by, label, s2, "mono", 400, st["fill"])
                    cx += w + 10
                    continue
                weight = st.get("weight", 400)
                w = metrics.width(t, size, "doc", weight)
                if st.get("bg"):
                    self.rect(cx, by - size * 0.86, w, size * 1.2, fill=st["bg"], rx=2)
                if st.get("under_dot"):
                    self.line(cx, by + size * 0.26, cx + w, by + size * 0.26,
                              st["under_dot"], 1, dash="1.6 1.6", op=0.6)
                self.text(cx, by, t, size, "doc", weight,
                          st.get("fill", fill), italic=st.get("italic", False),
                          op=opacity)
                if st.get("strike"):
                    self.line(cx, by - size * 0.28, cx + w, by - size * 0.28,
                              st["strike"], 1.1, op=0.7)
                if st.get("under"):
                    # 2px rather than the product's 1.5, so the three hues stay
                    # separable when the figure is printed at column width.
                    self.line(cx, by + size * 0.30, cx + w, by + size * 0.30,
                              st["under"], 2)
                if st.get("qrule"):
                    self.line(cx, by + size * 0.34, cx + w, by + size * 0.34,
                              st["qrule"], 1)
                cx += w
            by += line_h
        return by - line_h + (line_h if not align_last else 0)

    def para_height(self, box_w, runs, size=13.5, leading=1.62) -> float:
        probe = Fig(1, 1)
        end = probe.para(0, 0, box_w, runs, size, leading)
        return end

    # ── annotation layer ──────────────────────────────────────────────────────
    def callout(self, label, x, y, r=10.5, fill=ANN):
        self.circle(x, y, r, fill=fill)
        self.text(x, y + r * 0.36, str(label), size=r * 1.12, weight=700,
                  fill=ANN_FG, anchor="middle")

    def tag(self, label, x, y, w=None, h=19, fill=ANN):
        tw = metrics.width(label, 11, "doc", 700) + 14
        w = w or tw
        self.rect(x, y, w, h, fill=fill, rx=3)
        self.text(x + w / 2, y + h * 0.71, label, 11, "doc", 700, ANN_FG, anchor="middle")
        return w

    def leader(self, pts, stroke=ANN, sw=1.2, arrow=True, dash=None):
        d = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
        self.path(d, stroke=stroke, sw=sw, dash=dash)
        if arrow:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            import math
            a = math.atan2(y2 - y1, x2 - x1)
            for s in (2.55, -2.55):
                self.line(x2, y2, x2 + 6.5 * math.cos(a + s), y2 + 6.5 * math.sin(a + s),
                          stroke, sw, cap="round")

    # ── output ────────────────────────────────────────────────────────────────
    def svg(self, embed_fonts=True, title="") -> str:
        face = ""
        if embed_fonts:
            g = "".join(sorted(self.glyphs)) or "a"
            gm = "".join(sorted(self.mono_glyphs)) or "a"
            faces = [
                ("Inter", 400, metrics.embed("doc", 400, g)),
                ("Inter", 700, metrics.embed("doc", 700, g)),
            ]
            if self.mono_glyphs:
                faces.append(("JetBrains Mono", 400, metrics.embed("mono", 400, gm)))
            face = "\n".join(
                f"@font-face{{font-family:'{n}';font-weight:{w};font-style:normal;"
                f"src:url(data:font/woff2;base64,{b}) format('woff2');}}"
                for n, w, b in faces)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {self.w:.0f} {self.h:.0f}" width="{self.w:.0f}" height="{self.h:.0f}" '
            f'role="img" aria-label="{esc(title)}">'
            f"<style>{face}</style>"
            f'<rect width="{self.w:.0f}" height="{self.h:.0f}" fill="{self.bg}"/>'
            + "".join(self.parts) + "</svg>")

    def save(self, path: Path, title=""):
        Path(path).write_text(self.svg(embed_fonts=True, title=title), encoding="utf-8")
        return path
