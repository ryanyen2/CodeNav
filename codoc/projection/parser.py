"""Parse a directory of `.codoc` files into a structured ParsedTree.

The parser handles both the new prose-title format and the legacy
slug+[Badge]+UUID format. Identity resolution order:

  1. Inline ``# @<uuid>`` comment (legacy format only).
  2. ``old_meta.title_path_to_uuid`` lookup using the title path.
  3. ``old_meta.slug_path_to_uuid`` lookup using the display name as slug.

Lines that cannot be resolved are reported as DiffErrors by the differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from codoc.projection.meta import TreeMeta


# New-format feature line: indent + marker + title + optional (state) suffix.
_TITLE_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[-~])\s+"
    r"(?P<title>.+?)"
    r"(?:\s+\((?P<state_suffix>strained|severed|stub|deprecated)\))?"
    r"(?:\s+\[(?P<badge>[^\]]*)\])?"      # backward compat: old [Badge] format
    r"\s*(?:#\s*@(?P<uuid>[0-9a-f\-]+))?\s*$",  # backward compat: old inline UUID
    re.IGNORECASE,
)

# Old proposal line: "? <kind>: <slug>  [proposal]  # ?<hlc>" — kept for backward compat.
_PROPOSAL_RE_LEGACY = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[?!])\s+"
    r"(?P<kind>[\w\-]+):\s+(?P<slug>\S+)"
    r"(?:\s+\[(?P<badge>[^\]]*)\])?"
    r"\s*(?:#\s*\?(?P<hlc>[0-9a-zA-Z:\-_]+))?\s*$"
)

# New-format diff hunk line: col-0 marker where position-2 char is -, ~, or space.
# This distinguishes "+ - Title" / "~ - Title" / "+     intent" from regular
# feature lines "- auth-flow" (where position-2 is a letter/digit).
_DIFF_HUNK_RE = re.compile(r"^[+\-~] [-~ ]")

# bindings/candidate-bindings lines — read-only, skip.
_BINDINGS_RE = re.compile(r"^\s*(?:bindings|candidate-bindings):")
_BINDING_ENTRY_RE = re.compile(r"^\s*\[b\d+\]\s")

# Legacy header lines.
_HEADER_RE = re.compile(r"^#\s*codoc\s+(index|subtree)")


@dataclass
class ParsedFeature:
    uuid: str
    slug: str        # display name used as slug key (title or old slug)
    title: str       # prose display title (same as slug for old-format features)
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
    source_file: str | None = None


@dataclass
class ParsedTree:
    features: list[ParsedFeature] = field(default_factory=list)
    proposals: list[ParsedProposal] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    feature_uuids: set[str] = field(default_factory=set)
    # Features whose identity couldn't be resolved (differ will convert to DiffErrors).
    feature_lines_without_uuid: list[tuple[str, int, str]] = field(default_factory=list)
    duplicate_uuids: list[tuple[str, str, int]] = field(default_factory=list)


def _strip_comment_only_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") and not s.startswith("#!")


def _parse_index_file(
    path: Path,
    parsed: ParsedTree,
    seen_proposal_hlcs: set[str],
) -> None:
    """Parse _index.codoc — only proposals are meaningful here."""
    text = path.read_text(encoding="utf-8")
    file_name = path.name
    lines = text.splitlines()
    for lineno, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or _HEADER_RE.match(stripped) or _strip_comment_only_line(line):
            continue
        p = _PROPOSAL_RE_LEGACY.match(line)
        if p and p.group("hlc"):
            hlc = p.group("hlc")
            kind = p.group("kind")
            marker = p.group("marker")
            seen_proposal_hlcs.add(hlc)
            if marker == "!":
                parsed.proposals.append(
                    ParsedProposal(hlc=hlc, kind=kind, action="reject", source_file=file_name)
                )


def _parse_one_file(
    path: Path,
    parsed: ParsedTree,
    seen_proposal_hlcs: set[str],
    old_meta: "TreeMeta | None" = None,
) -> tuple[dict[str, dict], list[str]]:
    """Parse one .codoc file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    file_name = path.name

    parent_stack: list[tuple[int, str, str | None]] = []
    cur_feature: ParsedFeature | None = None
    cur_intent_lines: list[str] = []
    present_proposals: dict[str, dict] = {}

    def _flush_feature() -> None:
        nonlocal cur_feature, cur_intent_lines
        if cur_feature is not None:
            # Preserve multi-paragraph structure: paragraphs separated by blank lines.
            paragraphs: list[str] = []
            current_para: list[str] = []
            for ln in cur_intent_lines:
                stripped = ln.strip()
                if stripped:
                    current_para.append(stripped)
                else:
                    if current_para:
                        paragraphs.append(" ".join(current_para))
                        current_para = []
            if current_para:
                paragraphs.append(" ".join(current_para))
            cur_feature.intent = "\n".join(paragraphs)
            parsed.features.append(cur_feature)
            parsed.feature_uuids.add(cur_feature.uuid)
        cur_feature = None
        cur_intent_lines = []

    for lineno, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            continue

        if _HEADER_RE.match(stripped) or _strip_comment_only_line(line):
            continue

        if _BINDINGS_RE.match(line) or _BINDING_ENTRY_RE.match(line):
            continue

        if _DIFF_HUNK_RE.match(line):
            _flush_feature()
            continue

        p = _PROPOSAL_RE_LEGACY.match(line)
        if p and p.group("hlc"):
            _flush_feature()
            hlc = p.group("hlc")
            kind = p.group("kind")
            marker = p.group("marker")
            slug = p.group("slug")
            present_proposals[hlc] = {
                "slug": slug, "kind": kind, "marker": marker,
                "file": file_name, "line": lineno,
            }
            seen_proposal_hlcs.add(hlc)
            if marker == "!":
                parsed.proposals.append(
                    ParsedProposal(hlc=hlc, kind=kind, action="reject", source_file=file_name)
                )
            continue

        m = _TITLE_LINE_RE.match(line)
        if m:
            _flush_feature()
            indent_len = len(m.group("indent"))
            marker = m.group("marker")
            title = m.group("title").strip()
            uuid = m.group("uuid")  # may be None (new format)

            while parent_stack and parent_stack[-1][0] >= indent_len:
                parent_stack.pop()
            parent_uuid = parent_stack[-1][2] if parent_stack else None

            if uuid is None and old_meta is not None:
                parent_titles = [name for _, name, _ in parent_stack]
                title_path = " > ".join(parent_titles + [title])
                uuid = old_meta.title_path_to_uuid.get(title_path)

                if uuid is None:
                    slug_path = "/".join(parent_titles + [title])
                    uuid = old_meta.slug_path_to_uuid.get(slug_path)

            if uuid is None:
                parsed.feature_lines_without_uuid.append((file_name, lineno, title))
                parent_stack.append((indent_len, title, None))
                continue

            if uuid in parsed.feature_uuids:
                parsed.duplicate_uuids.append((uuid, file_name, lineno))

            cur_feature = ParsedFeature(
                uuid=uuid,
                slug=title,
                title=title,
                intent="",
                parent_uuid=parent_uuid,
                retired=(marker == "~"),
                source_file=file_name,
                line_number=lineno,
            )
            parent_stack.append((indent_len, title, uuid))
            cur_intent_lines = []
            continue

        if cur_feature is not None:
            cur_intent_lines.append(line)

    _flush_feature()
    return present_proposals, lines


def parse_tree_dir(codoc_dir: str, old_meta: TreeMeta | None = None) -> ParsedTree:
    """Read _index.codoc (the single hierarchical document) and parse into a ParsedTree.

    In the new single-document layout, _index.codoc contains the full nested outline
    with description blocks.  Legacy per-root <slug>.codoc files are also read if
    present (backward compat for existing stores not yet re-bootstrapped).
    """
    parsed = ParsedTree()
    tree_dir = Path(codoc_dir) / "tree"
    if not tree_dir.is_dir():
        return parsed

    seen_proposal_hlcs: set[str] = set()
    file_lines_cache: dict[str, list[str]] = {}

    index_path = tree_dir / "_index.codoc"
    if index_path.exists():
        try:
            present_proposals, file_lines = _parse_one_file(
                index_path, parsed, seen_proposal_hlcs, old_meta=old_meta
            )
            file_lines_cache[index_path.name] = file_lines
        except OSError as exc:
            parsed.parse_errors.append(f"could not read _index.codoc: {exc}")

    # Also parse any legacy per-root .codoc files that are not _index.codoc.
    for path in sorted(tree_dir.glob("*.codoc")):
        if path.name == "_index.codoc":
            continue
        try:
            present_proposals, file_lines = _parse_one_file(
                path, parsed, seen_proposal_hlcs, old_meta=old_meta
            )
            file_lines_cache[path.name] = file_lines
        except OSError as exc:
            parsed.parse_errors.append(f"could not read {path.name}: {exc}")

    if old_meta is not None:
        for hlc, loc in old_meta.uuid_to_location.items():
            if loc.get("kind") != "proposal":
                continue
            if hlc in seen_proposal_hlcs:
                continue

            file_name = loc.get("file", "")
            start_line = loc.get("line", -1)
            if _is_new_format_proposal_present(
                hlc, file_name, start_line,
                old_meta, file_lines_cache
            ):
                continue

            parsed.proposals.append(
                ParsedProposal(
                    hlc=hlc,
                    kind="",
                    action="accept",
                    source_file=file_name,
                )
            )

    return parsed


def _is_new_format_proposal_present(
    hlc: str,
    file_name: str,
    start_line: int,
    old_meta: TreeMeta,
    file_lines_cache: dict[str, list[str]],
) -> bool:
    """Return True if the col-0 diff hunk for *hlc* is still in the file."""
    for key, stored_hlc in old_meta.line_range_to_hlc.items():
        if stored_hlc != hlc:
            continue
        try:
            file_part, range_part = key.rsplit(":", 1)
            range_start = int(range_part.split("-")[0])
        except (ValueError, IndexError):
            continue
        if file_part != file_name:
            continue
        lines = file_lines_cache.get(file_name, [])
        if range_start < len(lines):
            line = lines[range_start]
            return bool(line and line[0] in "+-~")
        return False

    return False
