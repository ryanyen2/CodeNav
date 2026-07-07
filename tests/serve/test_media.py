"""codoc/serve/media.py — serving a local image/pdf attachment to hub browser
clients, and the ref→URL rewrite shared with the payload."""
from __future__ import annotations

import base64

from codoc.serve.media import media_url_for, resolve_media_file, save_media_attachment


def test_resolve_media_file_serves_an_existing_attachment(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)
    target = codoc_dir / "media" / "mock.png"
    target.write_bytes(b"\x89PNG\r\n")
    resolved = resolve_media_file(str(codoc_dir), "mock.png")
    assert resolved == target


def test_resolve_media_file_rejects_traversal_segments(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(b"nope")
    assert resolve_media_file(str(codoc_dir), "..") is None
    assert resolve_media_file(str(codoc_dir), "../secret.png") is None
    assert resolve_media_file(str(codoc_dir), "sub/dir.png") is None
    assert resolve_media_file(str(codoc_dir), "..\\secret.png") is None


def test_resolve_media_file_rejects_disallowed_extension(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)
    (codoc_dir / "media" / "script.sh").write_text("echo hi")
    assert resolve_media_file(str(codoc_dir), "script.sh") is None


def test_resolve_media_file_missing_file(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    (codoc_dir / "media").mkdir(parents=True)
    assert resolve_media_file(str(codoc_dir), "absent.png") is None


def test_media_url_for_local_ref():
    assert media_url_for(".codoc/media/mock.png") == "/api/media/mock.png"


def test_media_url_for_external_url_passthrough():
    assert media_url_for("https://cdn.example/mock.png") == "https://cdn.example/mock.png"


def test_media_url_for_unresolvable_ref():
    assert media_url_for("bare-filename.png") is None
    assert media_url_for("") is None


# ── save_media_attachment — a remote-submitted block's file bytes ──────────

def test_save_media_attachment_writes_and_returns_ref(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    data = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    ref = save_media_attachment(str(codoc_dir), "blk-1", data, "image/png")
    assert ref == ".codoc/media/blk-1.png"
    assert (codoc_dir / "media" / "blk-1.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_save_media_attachment_sanitizes_key(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    data = base64.b64encode(b"data").decode()
    ref = save_media_attachment(str(codoc_dir), "../../etc/passwd", data, "application/pdf")
    assert ref == ".codoc/media/etcpasswd.pdf"
    # never escaped the media directory
    assert (codoc_dir / "media" / "etcpasswd.pdf").exists()
    assert not (tmp_path / "etc").exists()


def test_save_media_attachment_rejects_disallowed_mime(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    data = base64.b64encode(b"#!/bin/sh\nrm -rf /").decode()
    assert save_media_attachment(str(codoc_dir), "blk-1", data, "application/x-sh") is None


def test_save_media_attachment_rejects_bad_base64(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    assert save_media_attachment(str(codoc_dir), "blk-1", "not-base64!!!", "image/png") is None


def test_save_media_attachment_rejects_oversized_payload(tmp_path, monkeypatch):
    import codoc.serve.media as media_mod

    monkeypatch.setattr(media_mod, "_MAX_ATTACHMENT_BYTES", 10)
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    data = base64.b64encode(b"x" * 100).decode()
    assert save_media_attachment(str(codoc_dir), "blk-1", data, "image/png") is None
