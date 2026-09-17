"""The substrate: persistence and IO around the calculus (spec §7).

- ``clock``     — the hybrid logical clock every op is stamped with.
- ``log``       — session log files (the truth), their validation, and the
                  client inbox the daemon drains.
- ``workspace`` — the daemon-facing façade: open a ``.codoc`` directory,
                  rebuild the store from its logs, submit ops, ingest text.
"""
