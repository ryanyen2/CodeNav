"""Annotate a real screenshot.

A figure here is the screenshot itself plus a numbered overlay: circles on the UI,
optional rings around the control being pointed at, and a legend underneath. Nothing
about the interface is redrawn, so a figure cannot show a control the product does
not have. Coordinates are in the source image's own pixels.
"""
from __future__ import annotations

import base64
from pathlib import Path

import metrics
from draw import Fig, esc

INK = "#111318"
INK_FG = "#ffffff"
RING = "#e8453c"          # the one saturated hue, used only by the annotation layer
LEG = "#3a3f47"
LEG_MUTED = "#6b727d"
PAGE = "#ffffff"


class Shot(Fig):
    """A screenshot with an annotation layer over it."""

    def __init__(self, png: Path, scale: float = 1.0, pad: int = 0, legend_rows: int = 0,
                 legend_h: float = 22.0, bg: str = PAGE, coord_width: float = 0.0):
        """`coord_width` is the width the annotation coordinates were authored
        against. Feeding a resized copy of the same screenshot then keeps every
        callout in place, which is how the web-sized figures are built."""
        import subprocess
        out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(png)],
                             capture_output=True, text=True).stdout
        w = h = 0
        for line in out.splitlines():
            if "pixelWidth" in line:
                w = int(line.split(":")[1])
            if "pixelHeight" in line:
                h = int(line.split(":")[1])
        self.cs = (w / coord_width) if coord_width else 1.0
        self.iw, self.ih = w * scale, h * scale
        self.scale = scale * self.cs
        self.pad = pad
        legend = legend_rows * legend_h + (14 if legend_rows else 0)
        super().__init__(self.iw + pad * 2, self.ih + pad * 2 + legend, bg=bg)
        data = base64.b64encode(Path(png).read_bytes()).decode("ascii")
        self.parts.append(
            f'<image x="{pad}" y="{pad}" width="{self.iw:.1f}" height="{self.ih:.1f}" '
            f'preserveAspectRatio="none" xlink:href="data:image/png;base64,{data}"/>')
        self.rect(pad, pad, self.iw, self.ih, fill="none", stroke="#c9ccd2", sw=1)
        self.legend_y = pad * 2 + self.ih + 20
        self.legend_h = legend_h

    def px(self, x, y):
        return self.pad + x * self.scale, self.pad + y * self.scale

    def ring(self, x, y, w, h, r=4, sw=2.4, color=RING):
        px, py = self.px(x, y)
        self.rect(px, py, w * self.scale, h * self.scale, fill="none", stroke=color,
                  sw=sw, rx=r)

    def mark(self, n, x, y, r=17, to=None, color=INK):
        """A numbered circle at (x, y), optionally with a leader to a point."""
        px, py = self.px(x, y)
        if to:
            tx, ty = self.px(*to)
            self.leader([(px, py), (tx, ty)], stroke=color, sw=2.0)
        self.circle(px, py, r, fill=color)
        self.text(px, py + r * 0.37, str(n), size=r * 1.15, weight=700, fill=INK_FG,
                  anchor="middle")

    def legend(self, items, cols=2, size=15.5, gap=30):
        """Numbered legend under the image. Items are (n, text)."""
        colw = (self.w - self.pad * 2 - gap * (cols - 1)) / cols
        for i, (n, text) in enumerate(items):
            c, r = i % cols, i // cols
            x = self.pad + c * (colw + gap)
            y = self.legend_y + r * self.legend_h
            self.circle(x + 9, y - 5, 9, fill=INK)
            self.text(x + 9, y - 0.6, str(n), size=11.5, weight=700, fill=INK_FG,
                      anchor="middle")
            self.para(x + 24, y, colw - 24, [(text, "body")], size, 1.45, fill=LEG)
