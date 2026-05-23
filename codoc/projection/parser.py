"""Parse a directory of `.codoc` files into a structured ParsedTree.

Identity resolution order:
  1. Inline ``# @<uuid>`` comment (backward compat).
  2. ``old_meta.title_path_to_uuid`` lookup using the title path.
  3. ``old_meta.slug_path_to_uuid`` lookup using the display name as slug.
  4. Structural sibling-position + edit-distance alignment.

Lines that cannot be resolved are reported as DiffErrors by the differ.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from codoc.projection.meta import TreeMeta
from codoc.projection import tree_align


# New-format feature line: indent + marker + title + optional (state) suffix.
# Markers: - live, ~ retired, * placeholder (authored stub awaiting feedforward)
_TITLE_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[-~*])\s+"
    r"(?P<title>.+?)"
    r"(?:\s+\((?P<state_suffix>strained|severed|stub|deprecated)\))?"
    r"(?:\s+\[(?P<badge>[^\]]*)\])?"      # backward compat: old [Badge] format
    r"\s*(?:#\s*@(?P<uuid>[0-9a-f\-]+))?\s*$",  # backward compat: old inline UUID
    re.IGNORECASE,
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

# Legacy reject marker: "! kind: ... # ?hlc" written by user to reject a proposal.
_REJECT_MARKER_RE = re.compile(
    r"^!\s+\S+.*#\s*\?(?P<hlc>[0-9a-zA-Z:\-_]+)\s*$"
)

# Structured field lines (new format)
_FIELD_RE = re.compile(r"^(?P<indent>\s*)(?P<key>purpose|rationale|scenario|needs|binds)\s*:\s*(?P<value>.*)$", re.IGNORECASE)
# Edge/binding lines: "    -> feature://slug" or "    -> code://file::sym"
_EDGE_RE = re.compile(r"^\s*->\s*(?P<ref>\S+)")
# Scenario continuation: "    given ..." / "    when  ..." / "    then  ..."
_SCENARIO_LINE_RE = re.compile(r"^\s*(given|when\s+|then\s+)\s*.+", re.IGNORECASE)


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
    # New structured fields
    purpose: str = ""
    rationale: str = ""
    scenario: str = ""
    needs: list = field(default_factory=list)  # list of slug strings
    is_placeholder: bool = False  # True when authored with * marker


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
    # Features whose identity couldn't be resolved (differ will convert to IntroduceOps).
    # Each tuple: (file, lineno, title, parent_uuid_or_none, intent_str)
    feature_lines_without_uuid: list[tuple[str, int, str, str | None, str]] = field(default_factory=list)
    duplicate_uuids: list[tuple[str, str, int]] = field(default_factory=list)


def _strip_comment_only_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") and not s.startswith("#!")


def _parse_one_file(
    path: Path,
    parsed: ParsedTree,
    seen_proposal_hlcs: set[str],
    old_meta: "TreeMeta | None" = None,
    sibling_index: "dict | None" = None,
) -> tuple[dict[str, dict], list[str]]:
    """Parse one .codoc file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    file_name = path.name

    parent_stack: list[tuple[int, str, str | None]] = []
    cur_feature: ParsedFeature | None = None
    cur_intent_lines: list[str] = []
    cur_scenario_lines: list[str] = []
    cur_needs: list[str] = []
    cur_in_scenario: bool = False
    cur_in_needs: bool = False
    cur_in_binds: bool = False
    present_proposals: dict[str, dict] = {}
    sibling_counter: defaultdict[str | None, int] = defaultdict(int)
    # Pre-built sibling index — shared across the parse (passed from parse_tree_dir).
    _sibling_index = sibling_index

    # Non-empty sentinel distinct from any real UUID so `if uuid:` guards work correctly.
    _SENTINEL_UUID = "__unresolved__"

    def _flush_feature() -> None:
        nonlocal cur_feature, cur_intent_lines, cur_scenario_lines, cur_needs
        nonlocal cur_in_scenario, cur_in_needs, cur_in_binds
        if cur_feature is not None:
            # Preserve multi-paragraph structure for legacy intent
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
            intent_text = "\n".join(paragraphs)
            cur_feature.intent = intent_text
            # Apply structured fields
            if cur_scenario_lines:
                cur_feature.scenario = "\n".join(cur_scenario_lines)
            if cur_needs:
                cur_feature.needs = list(cur_needs)

            if cur_feature.uuid == _SENTINEL_UUID:
                parsed.feature_lines_without_uuid.append(
                    (cur_feature.source_file, cur_feature.line_number, cur_feature.title,
                     cur_feature.parent_uuid, intent_text)
                )
            else:
                parsed.features.append(cur_feature)
                parsed.feature_uuids.add(cur_feature.uuid)
        cur_feature = None
        cur_intent_lines = []
        cur_scenario_lines = []
        cur_needs = []
        cur_in_scenario = False
        cur_in_needs = False
        cur_in_binds = False

    for lineno, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            # Inside a feature block, blank lines are paragraph separators.
            if cur_feature is not None:
                cur_in_scenario = False
                cur_in_needs = False
                cur_in_binds = False
                cur_intent_lines.append("")
            continue

        if _HEADER_RE.match(stripped) or _strip_comment_only_line(line):
            continue

        if _BINDINGS_RE.match(line) or _BINDING_ENTRY_RE.match(line):
            continue

        if _DIFF_HUNK_RE.match(line):
            _flush_feature()
            continue

        rm = _REJECT_MARKER_RE.match(line)
        if rm and rm.group("hlc"):
            _flush_feature()
            hlc = rm.group("hlc")
            if hlc not in seen_proposal_hlcs:
                seen_proposal_hlcs.add(hlc)
                parsed.proposals.append(ParsedProposal(hlc=hlc, kind="", action="reject"))
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
                # Pass 1 (backward compat): title-path and slug-path exact lookups.
                parent_titles = [name for _, name, _ in parent_stack]
                title_path = " > ".join(parent_titles + [title])
                uuid = old_meta.title_path_to_uuid.get(title_path)

                if uuid is None:
                    slug_path = "/".join(parent_titles + [title])
                    uuid = old_meta.slug_path_to_uuid.get(slug_path)

                # Pass 2/3 (structural): use sibling position + edit distance.
                if uuid is None:
                    sibling_index_new = sibling_counter[parent_uuid]
                    uuid = tree_align.resolve_uuid_structural(
                        title, parent_uuid, sibling_index_new, old_meta,
                        prebuilt_sibling_index=_sibling_index,
                    )

            # Increment sibling counter for this parent (after resolving uuid).
            sibling_counter[parent_uuid] += 1

            if uuid is None:
                cur_feature = ParsedFeature(
                    uuid=_SENTINEL_UUID,
                    slug=title,
                    title=title,
                    intent="",
                    parent_uuid=parent_uuid,
                    retired=(marker == "~"),
                    is_placeholder=(marker == "*"),
                    source_file=file_name,
                    line_number=lineno,
                )
                parent_stack.append((indent_len, title, None))
                cur_intent_lines = []
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
                is_placeholder=(marker == "*"),
                source_file=file_name,
                line_number=lineno,
            )
            parent_stack.append((indent_len, title, uuid))
            cur_intent_lines = []
            continue

        if cur_feature is not None:
            # Check for structured field lines FIRST
            fm = _FIELD_RE.match(line)
            if fm:
                key = fm.group("key").lower()
                value = fm.group("value").strip()
                cur_in_scenario = False
                cur_in_needs = False
                cur_in_binds = False
                if key == "purpose":
                    cur_feature.purpose = value
                elif key == "rationale":
                    cur_feature.rationale = value
                elif key == "scenario":
                    cur_in_scenario = True
                elif key == "needs":
                    if value:
                        # CSV form: "needs: slug-a, slug-b"
                        for slug in (s.strip() for s in value.split(",")):
                            if slug:
                                cur_needs.append(slug)
                    cur_in_needs = True  # also accept -> lines below
                elif key == "binds":
                    cur_in_binds = True
                continue

            # Handle continuation lines for multi-line blocks
            em = _EDGE_RE.match(line)
            if em and cur_in_needs:
                ref = em.group("ref")
                if ref.startswith("feature://"):
                    slug = ref[len("feature://"):]
                    if slug:
                        cur_needs.append(slug)
                continue
            if em and cur_in_binds:
                continue  # binds are read-only, skip

            if cur_in_scenario:
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith("->"):
                    cur_scenario_lines.append(stripped_line)
                    continue

            # Legacy fallback: treat as intent prose
            cur_in_scenario = False
            cur_in_needs = False
            cur_in_binds = False
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

    # Build sibling index once (O(N)) to avoid O(N*D) repeated scans during parse.
    sibling_idx = tree_align.build_sibling_index(old_meta) if old_meta is not None else None

    index_path = tree_dir / "_index.codoc"
    if index_path.exists():
        try:
            present_proposals, file_lines = _parse_one_file(
                index_path, parsed, seen_proposal_hlcs, old_meta=old_meta,
                sibling_index=sibling_idx,
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
                path, parsed, seen_proposal_hlcs, old_meta=old_meta,
                sibling_index=sibling_idx,
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
