"""The ``.codoc`` directory: persistence and IO around the calculus (spec §7).

- ``layout``    — the names of the files in it, atomic write, tolerant read, flock.
- ``config``    — the tracked ``config.json`` (doc language, roles).
- ``clock``     — the hybrid logical clock every op is stamped with.
- ``log``       — session log files (the truth), their validation, and the
                  client inbox the applier drains.
- ``snapshot``  — folding a settled prefix of the log into one snapshot file.
- ``roles``     — who may write what.
- ``workspace`` — ``Workspace``: open the directory, rebuild the store from its
                  logs, submit ops, ingest text, drain the inbox.
- ``validate``  — checking a log file this workspace did not write.
"""
from codoc.workspace.roles import Roles
from codoc.workspace.workspace import Workspace

__all__ = ["Roles", "Workspace"]
