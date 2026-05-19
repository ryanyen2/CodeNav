"""Parse a directory of `.codoc` files into a structured ParsedTree.

The parser is intentionally permissive: feature lines may carry a UUID
comment (``# @<uuid>``) or omit it entirely (new default format).  When the
UUID is absent the parser attempts a slug-path lookup in ``old_meta``.
Lines that still cannot be resolved are reported as DiffErrors by the
differ, not raised here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from codoc.projection.meta import TreeMeta


# Feature line: optional indent, '- ' or '~ ' prefix, slug, optional state badge,
# optional trailing UUID comment.
_FEATURE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[-~])\s+(?P<slug>[^\s\[#]+)"
    r"(?:\s+\[(?P<badge>[^\]]*)\])?"
    r"\s*(?:#\s*@(?P<uuid>[0-9a-f\-]+))?\s*$",
    re.IGNORECASE,
)

# Feature line WITHOUT UUID (new feature, which is not allowed in v1).
_FEATURE_NO_UUID_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[-~])\s+(?P<slug>\S+).*$"
)

# Proposal line: "? <kind>: <slug>  [proposal]  # ?<hlc>"
# Or rejected proposal: "! <kind>: <slug>  [proposal]  # ?<hlc>"
_PROPOSAL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[?!])\s+"
    r"(?P<kind>[\w\-]+):\s+(?P<slug>\S+)"
    r"(?:\s+\[(?P<badge>[^\]]*)\])?"
    r"\s*(?:#\s*\?(?P<hlc>[0-9a-zA-Z:\-_]+))?\s*$"
)

# bindings: prefix line — read-only, not a delta.
_BINDINGS_RE = re.compile(r"^\s*(?:bindings|candidate-bindings):")

# New-format binding entry: "  [b1] file :: symbol" — read-only, skip.
_BINDING_ENTRY_RE = re.compile(r"^\s*\[b\d+\]\s")

# Header lines: "# codoc index @ HLC ..." or "# codoc subtree: ..."
_HEADER_RE = re.compile(r"^#\s*codoc\s+(index|subtree)")


@dataclass
class ParsedFeature:
    uuid: str
    slug: str
    intent: str
    parent_uuid: str | None
    retired: bool
    source_file: str
    line_number: int  # 0-indexed line of the feature header line


@dataclass
class ParsedProposal:
    hlc: str
    kind: str
    action: str  # "accept" | "reject" | "accept-with-edits"
    edited_slug: str | None = None
    edited_intent: str | None = None
    source_file: str | None = None  # file this proposal lived in (per old_meta) or where rejected


@dataclass
class ParsedTree:
    features: list[ParsedFeature] = field(default_factory=list)
    proposals: list[ParsedProposal] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    # uuids_seen: useful for differ
    feature_uuids: set[str] = field(default_factory=set)
    # Track features that lost their UUID (i.e. lines that match _FEATURE_NO_UUID_RE).
    # The differ converts these to DiffErrors.
    feature_lines_without_uuid: list[tuple[str, int, str]] = field(default_factory=list)
    # Track duplicate UUIDs encountered.
    duplicate_uuids: list[tuple[str, str, int]] = field(default_factory=list)


def _strip_comment_only_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") and not s.startswith("#!")


def _parse_index_file(
    path: Path,
    parsed: ParsedTree,
    seen_proposal_hlcs: set[str],
) -> None:
    """Parse _index.codoc — only proposals are meaningful here.

    The feature lines in the index are a read-only mirror of root-level slugs;
    editing them in the index has no effect (rename/retire happens in the
    subtree files). The differ ignores them.
    """
    text = path.read_text(encoding="utf-8")
    file_name = path.name
    for lineno, raw_line in enumerate(text.splitlines()):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or _HEADER_RE.match(stripped) or _strip_comment_only_line(line):
            continue
        p = _PROPOSAL_RE.match(line)
        if p and p.group("hlc"):
            hlc = p.group("hlc")
            kind = p.group("kind")
            marker = p.group("marker")
            seen_proposal_hlcs.add(hlc)
            if marker == "!":
                parsed.proposals.append(
                    ParsedProposal(
                        hlc=hlc,
                        kind=kind,
                        action="reject",
                        source_file=file_name,
                    )
                )


def _parse_one_file(
    path: Path,
    parsed: ParsedTree,
    seen_proposal_hlcs: set[str],
    old_meta: "TreeMeta | None" = None,
) -> dict[str, dict]:
    """Parse one .codoc file. Returns {hlc: {"slug":..., "intent":...}} for proposals
    that are still present in this file (used by differ to detect deletions)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    file_name = path.name

    # Indent stack: list of (indent_len, slug, uuid_or_none)
    parent_stack: list[tuple[int, str, str | None]] = []
    cur_feature: ParsedFeature | None = None
    cur_intent_lines: list[str] = []
    present_proposals: dict[str, dict] = {}  # hlc → kept fields

    def _flush_feature() -> None:
        nonlocal cur_feature, cur_intent_lines
        if cur_feature is not None:
            joined = " ".join(line.strip() for line in cur_intent_lines if line.strip())
            cur_feature.intent = joined
            parsed.features.append(cur_feature)
            parsed.feature_uuids.add(cur_feature.uuid)
        cur_feature = None
        cur_intent_lines = []

    for lineno, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        # Skip blank lines.
        if not stripped:
            # Blank lines do NOT terminate intent collection — but they are tolerated.
            continue

        # Skip header / pure comment lines.
        if _HEADER_RE.match(stripped) or _strip_comment_only_line(line):
            continue

        # Bindings echo: read-only (both old inline format and new block format).
        if _BINDINGS_RE.match(line):
            continue

        # New-format binding entry lines: "[b1] file :: symbol" — skip.
        if _BINDING_ENTRY_RE.match(line):
            continue

        # Feature line?
        m = _FEATURE_RE.match(line)
        if m:
            _flush_feature()
            indent_len = len(m.group("indent"))
            marker = m.group("marker")
            slug = m.group("slug")
            uuid = m.group("uuid")

            # Pop parent stack until we find a parent with strictly less indent.
            while parent_stack and parent_stack[-1][0] >= indent_len:
                parent_stack.pop()
            parent_uuid = parent_stack[-1][2] if parent_stack else None

            # When UUID is absent from the line, try meta lookup.
            if uuid is None and old_meta is not None:
                parent_slugs = [s for _, s, _ in parent_stack]
                slug_path = "/".join(parent_slugs + [slug])
                uuid = old_meta.slug_path_to_uuid.get(slug_path)

            if uuid is None:
                # Cannot resolve — report as unresolved (differ will handle).
                parsed.feature_lines_without_uuid.append((file_name, lineno, slug))
                parent_stack.append((indent_len, slug, None))
                continue

            if uuid in parsed.feature_uuids:
                parsed.duplicate_uuids.append((uuid, file_name, lineno))

            cur_feature = ParsedFeature(
                uuid=uuid,
                slug=slug,
                intent="",
                parent_uuid=parent_uuid,
                retired=(marker == "~"),
                source_file=file_name,
                line_number=lineno,
            )
            parent_stack.append((indent_len, slug, uuid))
            cur_intent_lines = []
            continue

        # Proposal line?
        p = _PROPOSAL_RE.match(line)
        if p and p.group("hlc"):
            _flush_feature()
            hlc = p.group("hlc")
            kind = p.group("kind")
            marker = p.group("marker")
            slug = p.group("slug")

            # Track seen so the differ knows it was NOT deleted.
            present_proposals[hlc] = {
                "slug": slug,
                "kind": kind,
                "marker": marker,
                "file": file_name,
                "line": lineno,
            }
            seen_proposal_hlcs.add(hlc)

            if marker == "!":
                # Explicit reject directive in the buffer.
                parsed.proposals.append(
                    ParsedProposal(
                        hlc=hlc,
                        kind=kind,
                        action="reject",
                        source_file=file_name,
                    )
                )
            # ? marker: still pending — no action emitted now (we'll detect
            # deletion vs presence in differ).
            continue

        # Otherwise, treat as intent prose for the current feature.
        if cur_feature is not None:
            cur_intent_lines.append(line)

    # End of file: flush trailing feature.
    _flush_feature()
    return present_proposals


def parse_tree_dir(codoc_dir: str, old_meta: TreeMeta | None = None) -> ParsedTree:
    """Read all *.codoc files in .codoc/tree/ and unify into a ParsedTree.

    When *old_meta* is supplied, proposals that were in the previous render
    but are missing from the current files are emitted as ``accept`` actions
    (or ``accept-with-edits`` if the slug/intent on a sibling line indicates
    user edits — Phase 1.5 keeps this minimal: any deletion = accept).
    """
    parsed = ParsedTree()
    tree_dir = Path(codoc_dir) / "tree"
    if not tree_dir.is_dir():
        return parsed

    seen_proposal_hlcs: set[str] = set()

    # Parse every subtree .codoc file. _index.codoc is read-only listing;
    # editing it has no effect on features (only top-level proposals live there
    # and the proposal parser handles them).
    for path in sorted(tree_dir.glob("*.codoc")):
        try:
            if path.name == "_index.codoc":
                _parse_index_file(path, parsed, seen_proposal_hlcs)
            else:
                _parse_one_file(path, parsed, seen_proposal_hlcs, old_meta=old_meta)
        except OSError as exc:
            parsed.parse_errors.append(f"could not read {path.name}: {exc}")

    # Detect deletions of proposal lines (= accept).
    if old_meta is not None:
        for hlc, loc in old_meta.uuid_to_location.items():
            if loc.get("kind") != "proposal":
                continue
            if hlc not in seen_proposal_hlcs:
                # Deleted from buffer → accept.
                parsed.proposals.append(
                    ParsedProposal(
                        hlc=hlc,
                        kind="",  # unknown at this point; differ will look up
                        action="accept",
                        source_file=loc.get("file"),
                    )
                )

    return parsed
