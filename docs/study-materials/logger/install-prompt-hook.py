#!/usr/bin/env python3
"""Install the study's prompt hook into a project, in either condition.

    python3 install-prompt-hook.py <project> [--participant CODE]

Merges one UserPromptSubmit entry into the project's ``.claude/settings.json``.
Merging matters: in the codoc condition that file already carries codoc's own
hooks, and replacing it would quietly disable the tool being studied. Existing
entries are left alone and this one is replaced if it is already there, so running
it twice is the same as running it once.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "prompt-hook.py"   # how this entry is recognised on a re-run


def install(project: Path, participant: str = "") -> Path:
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Refuse rather than overwrite. A settings file we cannot read is one
            # we cannot safely replace, and in the codoc condition it holds the
            # hooks the study depends on.
            raise SystemExit(f"{settings_path} is not valid JSON; fix it before installing")

    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("UserPromptSubmit", [])

    command = f"{sys.executable} {HERE / MARKER}"
    if participant:
        command = f"CODOC_STUDY_PARTICIPANT={participant} {command}"

    # Drop any previous copy of ours, keep everybody else's.
    kept = []
    for entry in entries:
        inner = [h for h in entry.get("hooks", []) if MARKER not in h.get("command", "")]
        if inner:
            kept.append({**entry, "hooks": inner})
        elif not entry.get("hooks"):
            kept.append(entry)
    kept.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
    hooks["UserPromptSubmit"] = kept

    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    return settings_path


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    participant = ""
    if "--participant" in sys.argv:
        i = sys.argv.index("--participant")
        if i + 1 < len(sys.argv):
            participant = sys.argv[i + 1]
            args = [a for a in args if a != participant]

    project = Path(args[0]).expanduser().resolve()
    if not project.is_dir():
        print(f"not a folder: {project}")
        return 2
    path = install(project, participant)
    print(f"prompt hook installed in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
