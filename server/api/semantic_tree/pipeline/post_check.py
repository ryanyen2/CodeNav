"""
Observational drift detection after inverse sync (tree → code).

Re-extracts affected files and reports what entities exist there. Does not
auto-correct; the caller or user can compare to the edited tree expectations.
"""

import logging
from pathlib import Path
from typing import List, Optional

from api.semantic_tree.models import CodebaseSnapshot, CodeEntity, FileInfo
from api.semantic_tree.extraction.python_extractor import extract_python_file

logger = logging.getLogger(__name__)


class DriftReportItem:
    """One file's re-extracted entities after apply (for comparison with tree)."""
    fpath: str
    entities: List[CodeEntity]

    def __init__(self, fpath: str, entities: List[CodeEntity]):
        self.fpath = fpath
        self.entities = entities


def post_check(
    root_dir: str,
    modified_fpaths: List[str],
) -> List[DriftReportItem]:
    """
    Re-extract entities from the given files after code was applied.
    Returns a list of (fpath, entities) for observational comparison.
    Does not modify anything; never auto-corrects.
    """
    result: List[DriftReportItem] = []
    root = Path(root_dir).resolve()
    for fpath in modified_fpaths:
        path = root / fpath
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Post-check: could not read %s: %s", fpath, e)
            continue
        try:
            file_info = extract_python_file(fpath, source, str(root), include_imports=False)
            result.append(DriftReportItem(fpath=fpath, entities=file_info.entities))
        except Exception as e:
            logger.warning("Post-check: extract failed for %s: %s", fpath, e)
    return result
