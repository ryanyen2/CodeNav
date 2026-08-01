"""url/pdf lift (real content extraction) + latex consult.

The url plugin's `lift` is the network-facing path — these tests stub
`fetch_guard.safe_get` directly rather than hitting the real network (safe_get's
own SSRF behavior is covered by tests/blocks/test_fetch_guard.py). The pdf
plugin's `lift` reads a local file only, so it's tested against a hand-built
minimal PDF fixture (no reportlab/fpdf dependency needed).
"""
from __future__ import annotations

import io
import json
import time

import pytest

from codoc.blocks.base import LiftContext
from codoc.blocks.reference import LatexPlugin, PdfPlugin, UrlPlugin
from codoc.model.block import Block
from codoc.model.feature import Feature


def _feature(fid="f-1"):
    return Feature(id=fid, title="A feature", description="")


def _minimal_pdf(text: str) -> bytes:
    """A hand-built, minimally-valid single-page PDF with one text-showing
    operator — enough for pypdf to extract `text` back out, no extra deps."""
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>endobj",
        b"4 0 obj<< /Length %d >>stream\n" % len(content) + content + b"\nendstream endobj",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for obj in objects:
        offsets.append(out.tell())
        out.write(obj + b"\n")
    xref_offset = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    out.write(f"startxref\n{xref_offset}\n%%EOF".encode())
    return out.getvalue()


# ── UrlPlugin.lift ──────────────────────────────────────────────────────────

def test_url_lift_fetches_and_extracts_on_a_bare_url(monkeypatch):
    pytest.importorskip("trafilatura")
    import codoc.blocks.reference as ref

    html = (b"<html><head><title>Spec Doc</title></head><body>"
            b"<article><p>The important spec content lives here in detail.</p>"
            b"</article></body></html>")
    monkeypatch.setattr(ref, "safe_get", lambda url, **kw: html)

    block = Block(feature_id="f-1", kind="url", content="https://docs.example/spec")
    result = UrlPlugin().lift(LiftContext(feature=_feature(), bindings=[], block=block))
    assert result.changed
    envelope = json.loads(result.content)
    assert envelope["url"] == "https://docs.example/spec"
    assert envelope["status"] == "ok"
    assert "spec content" in envelope["excerpt"]
    assert envelope["title"] == "Spec Doc"


def test_url_lift_is_a_noop_once_cached(monkeypatch):
    import codoc.blocks.reference as ref

    monkeypatch.setattr(ref, "safe_get", lambda url, **kw: (_ for _ in ()).throw(
        AssertionError("must not re-fetch a cached url")))
    envelope = json.dumps({"url": "https://docs.example/spec", "status": "ok",
                            "excerpt": "already fetched", "fetched_at": time.time()})
    block = Block(feature_id="f-1", kind="url", content=envelope)
    result = UrlPlugin().lift(LiftContext(feature=_feature(), bindings=[], block=block))
    assert result.changed is False


def test_url_lift_caches_failure_with_cooldown(monkeypatch):
    import codoc.blocks.reference as ref

    calls = []
    monkeypatch.setattr(ref, "safe_get", lambda url, **kw: calls.append(url) or None)
    block = Block(feature_id="f-1", kind="url", content="https://blocked.example/x")
    plugin = UrlPlugin()
    r1 = plugin.lift(LiftContext(feature=_feature(), bindings=[], block=block))
    assert r1.changed and json.loads(r1.content)["status"] == "blocked_or_unreachable"
    assert len(calls) == 1

    # A second lift pass immediately after must NOT re-fetch (cooldown).
    block2 = Block(feature_id="f-1", kind="url", content=r1.content)
    r2 = plugin.lift(LiftContext(feature=_feature(), bindings=[], block=block2))
    assert r2.changed is False
    assert len(calls) == 1


def test_url_lift_ignores_non_url_content():
    result = UrlPlugin().lift(LiftContext(
        feature=_feature(), bindings=[],
        block=Block(feature_id="f-1", kind="url", content="not a url")))
    assert result.changed is False


def test_url_consult_prefers_cached_excerpt():
    envelope = json.dumps({"url": "https://docs.example/spec", "title": "Spec Doc",
                            "excerpt": "the important bit", "status": "ok"})
    text = UrlPlugin().consult(Block(feature_id="f-1", kind="url", content=envelope))
    assert "Spec Doc" in text and "the important bit" in text


# ── PdfPlugin.lift ──────────────────────────────────────────────────────────

def test_pdf_lift_extracts_local_attachment(tmp_path):
    pytest.importorskip("pypdf")
    codoc_dir = tmp_path / ".codoc"
    media_dir = codoc_dir / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "blk-1.pdf").write_bytes(_minimal_pdf("Design notes for the feature"))

    block = Block(feature_id="f-1", kind="pdf", content=".codoc/media/blk-1.pdf")
    result = PdfPlugin().lift(LiftContext(
        feature=_feature(), bindings=[], block=block, codoc_dir=str(codoc_dir)))
    assert result.changed
    envelope = json.loads(result.content)
    assert envelope["status"] == "ok"
    assert "Design notes" in envelope["excerpt"]
    assert envelope["pages"] == 1


def test_pdf_lift_rejects_path_escaping_media_dir(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(_minimal_pdf("should never be read"))

    block = Block(feature_id="f-1", kind="pdf", content="../secret.pdf")
    result = PdfPlugin().lift(LiftContext(
        feature=_feature(), bindings=[], block=block, codoc_dir=str(codoc_dir)))
    # The traversal guard blocks the read — cached as an error (not silently
    # retried every pass), and critically the secret content never appears.
    envelope = json.loads(result.content)
    assert envelope["status"] == "error"
    assert "should never be read" not in result.content


def test_pdf_lift_does_not_retry_a_cached_error_every_pass(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)  # attachment intentionally absent

    envelope = json.dumps({"ref": ".codoc/media/missing.pdf", "status": "error",
                            "attempted_at": time.time()})
    block = Block(feature_id="f-1", kind="pdf", content=envelope)
    result = PdfPlugin().lift(LiftContext(
        feature=_feature(), bindings=[], block=block, codoc_dir=str(codoc_dir)))
    assert result.changed is False


def test_pdf_lift_noop_without_codoc_dir():
    block = Block(feature_id="f-1", kind="pdf", content=".codoc/media/blk-1.pdf")
    result = PdfPlugin().lift(LiftContext(feature=_feature(), bindings=[], block=block))
    assert result.changed is False


# ── LatexPlugin ─────────────────────────────────────────────────────────────

def test_latex_consult_carries_the_formula():
    block = Block(feature_id="f-1", kind="latex", content=r"E = mc^2")
    assert "E = mc^2" in LatexPlugin().consult(block)


def test_latex_consult_empty_is_empty():
    block = Block(feature_id="f-1", kind="latex", content="")
    assert LatexPlugin().consult(block) == ""
