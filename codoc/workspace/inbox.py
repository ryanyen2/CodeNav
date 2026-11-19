"""The client inbox: how a submission reaches the applier and how its answer
comes back (docs/concurrency.md, A submission's path).

    inbox/<client>.jsonl        submissions, client to applier
    inbox/<client>.ack.jsonl    answers, applier to client
    inbox/offsets.json          how far each client's file has been drained

All three are ignored by git. The file name is the identity: ``actor_for``
maps an inbox name to the actor the applier stamps, so a client never names
its own actor. ``client_payload`` and ``resolve_provisional`` decide what a
submission may assert and what its provisional anchors name.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.doc import lifted_fields
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL, nfc
from codoc.workspace.layout import CODOC_DIR, INBOX_DIR, OFFSETS, atomic_write

# Locally the inbox file name is the identity: any process that writes
# ide.jsonl or cli.jsonl is the person at the keyboard (`local_actor`); the hub
# writes one file per GitHub login, named from its session cookie.
PERSON_CLIENTS = frozenset({"ide", "cli"})
AGENT_CLIENT = re.compile(r"agent-[a-z0-9_-]+")
LOCAL = "human:local"              # the person at the keyboard when git names nobody
ACK_KEEP = 2000                    # ack lines kept per client when a session opens


# ── identity ─────────────────────────────────────────────────────────────────

@functools.cache
def local_actor(root: str | Path) -> str:
    """The person at this machine: ``human:<git config user.email>`` in the
    repository at ``root``, read once per process. ``human:local`` when there
    is no git, no repository config naming one, or no email."""
    try:
        email = subprocess.run(["git", "-C", str(root), "config", "user.email"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        email = ""
    return f"human:{email}" if email else LOCAL


def hub_actor(login: str) -> str:
    """A remote contributor's actor. The ``gh:`` prefix keeps a GitHub login
    named ``local`` (or ``import``) from arriving as the maintainer's own
    identity, here and in the role table."""
    return f"human:gh:{login}"


def actor_for(client: str, codoc_dir: str | Path = CODOC_DIR) -> str | None:
    """The actor the applier stamps on what inbox ``client`` submits, or None
    for a name no actor has (that file is never drained)."""
    if client in PERSON_CLIENTS:
        return local_actor(Path(codoc_dir).parent)
    if client.startswith("hub-") and len(client) > 4:
        return hub_actor(client[4:])
    if AGENT_CLIENT.fullmatch(client):            # an agent names itself in its inbox file: agent-<CODOC_AGENT>
        return f"agent:{client[6:]}"
    return None


# ── submissions ──────────────────────────────────────────────────────────────

def inbox_dir(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / INBOX_DIR


@dataclass
class Submission:
    """One client line: a client-side id plus an op body. The applier mints
    the op id, stamps the actor from the client's identity, applies, logs and
    acks. ``at`` is the wall-clock time of the append in milliseconds; the
    drain takes every client's lines in that order."""
    cid: str
    kind: str
    target: dict
    payload: dict
    batch: str = ""
    plan: str | None = None
    base: dict = field(default_factory=dict)
    caused_by: str | None = None
    at: float = 0.0


def submit(codoc_dir: str | Path, client: str, subs: list[Submission]) -> Path:
    """Append submissions to this client's inbox file, stamped with the time
    of the append: one ``O_APPEND`` write for the whole batch, so concurrent
    writers interleave whole lines."""
    d = inbox_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{client}.jsonl"
    now = time.time() * 1000
    data = "".join(json.dumps({**s.__dict__, "at": s.at or now}, ensure_ascii=False) + "\n"
                   for s in subs).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


@dataclass
class InboxLine:
    """One complete inbox line: a valid submission, or the error that makes it
    invalid (acked back so the client learns), and the offset past its end."""
    sub: Submission | None
    error: str | None
    end: int
    cid: str = ""


def read_inbox(codoc_dir: str | Path, client: str, offset: int = 0) -> list[InboxLine]:
    """Complete lines after byte ``offset``. An unterminated tail is left for
    the next read: a writer may be mid-line."""
    path = inbox_dir(codoc_dir) / f"{client}.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as fh:
        # A client only ever appends; a file shorter than the remembered offset
        # is a new one under the same name, and it is read whole.
        fh.seek(offset if offset <= path.stat().st_size else 0)
        data = fh.read()
    out: list[InboxLine] = []
    pos = 0
    while (nl := data.find(b"\n", pos)) >= 0:
        raw, pos = data[pos:nl], nl + 1
        end = offset + pos
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            out.append(InboxLine(None, f"unreadable: {e}", end))
            continue
        cid = d.get("cid") if isinstance(d, dict) else None
        err = _validate(d)
        if err:
            out.append(InboxLine(None, err, end, cid if isinstance(cid, str) else ""))
            continue
        out.append(InboxLine(Submission(**{k: d[k] for k in Submission.__dataclass_fields__ if k in d and d[k] is not None}),
                             None, end, cid))
    return out


def _validate(d) -> str | None:
    if not isinstance(d, dict):
        return "not an object"
    if not isinstance(d.get("cid"), str) or not d["cid"]:
        return "cid must be a non-empty string"
    if not isinstance(d.get("kind"), str):
        return "kind must be a string"
    for key in ("target", "payload"):
        if not isinstance(d.get(key), dict):
            return f"{key} must be an object"
    for key in ("batch", "plan", "caused_by"):
        if d.get(key) is not None and not isinstance(d[key], str):
            return f"{key} must be a string"
    if d.get("base") is not None and not isinstance(d["base"], dict):
        return "base must be an object"
    if d.get("at") is not None and (isinstance(d["at"], bool) or not isinstance(d["at"], (int, float))):
        return "at must be a number"
    if d["kind"] == "ingest":
        if not isinstance(d["target"].get("bid"), str):
            return "ingest target.bid must be a string"
        for key in ("base_text", "new_md"):
            if not isinstance(d["payload"].get(key), str):
                return f"ingest payload.{key} must be a string"
    return None


def clients(codoc_dir: str | Path) -> list[str]:
    d = inbox_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl") if not p.name.endswith(".ack.jsonl"))


# ── answers ──────────────────────────────────────────────────────────────────

def ack(codoc_dir: str | Path, client: str, cid: str, op=None, *, error: str | None = None,
        status: str | None = None) -> None:
    """Tell the client what became of ``cid``: the first op it minted, and
    ``error`` (what was refused, even when other ops landed) or else that op's
    conflict. ``status`` is for an outcome that is not a refusal: a whole-text
    write that asked for nothing mints no op and conflicts with nothing."""
    path = inbox_dir(codoc_dir) / f"{client}.ack.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"cid": cid, "op": op.id if op else "", "conflict": error or (op.conflict if op else None)}
    if status:
        rec["status"] = status
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def read_offsets(codoc_dir: str | Path) -> dict[str, int]:
    """How far into each client's inbox the last session read. Without it an
    applier re-reads every submission ever made and leans on the ack file to
    recognise them, so neither file could be trimmed."""
    p = inbox_dir(codoc_dir) / OFFSETS
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): int(v) for k, v in d.items() if isinstance(v, int)} if isinstance(d, dict) else {}


def write_offsets(codoc_dir: str | Path, offsets: dict[str, int]) -> None:
    atomic_write(inbox_dir(codoc_dir) / OFFSETS, json.dumps(offsets, sort_keys=True))


def rotate_acks(codoc_dir: str | Path, keep: int = ACK_KEEP) -> None:
    """Trim each client's ack file to its newest lines. An ack answers one
    submission; once the remembered offset is past it nothing re-reads that
    line, and the only cost left is the read at every applier start. Called
    when a writing workspace opens.

    This bounds the rule that a resubmitted client id is acked with the same
    op id to the newest ``keep`` acks per client. What is left is a client
    that re-appends an id it sent more than that many acks ago, which means it
    lost its own state and kept its counter; the editor mints ids under a
    per-page random prefix, so a page that restarts never collides with one."""
    d = inbox_dir(codoc_dir)
    if keep < 1 or not d.exists():
        return
    for p in d.glob("*.ack.jsonl"):
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= keep:
            continue
        atomic_write(p, "".join(lines[-keep:]))


def acked(codoc_dir: str | Path) -> dict[str, dict[str, str]]:
    """Per client, every acked cid and the op it minted (``""`` when none):
    what the drain skips, and what a later provisional anchor may name."""
    out: dict[str, dict[str, str]] = {}
    d = inbox_dir(codoc_dir)
    if not d.exists():
        return out
    for p in d.glob("*.ack.jsonl"):
        client = p.name[: -len(".ack.jsonl")]
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    a = json.loads(line)
                    out.setdefault(client, {})[a["cid"]] = a.get("op") or ""
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    return out


# ── what a client may assert ─────────────────────────────────────────────────

def client_payload(store: Store, kind: str, payload: dict, actor: str = "", target: dict | None = None) -> dict:
    """``payload`` as ``actor`` may send it.

    - Snapshot-only fields are removed, from an amend's or a plan's bodies
      too; a body keeps only its kind, target and payload, since the
      submission decides the plan.
    - A field only the loop's lift writes (``doc.lifted_fields``: a url
      block's fetched excerpt) is removed from a ``block.add``, and a
      ``block.set`` of one is refused: a client never says what a URL served.
    - ``set_by`` and ``prev_set_by`` are removed from every op: the hashes a
      client sends are set by that client. Naming another actor (the loop)
      would tell the reflect pass the change had already been reviewed.
    - A mark that names its placer (``actor``/``op``/``at``) is honoured only
      as the inverse the applier recorded for the sender's own latest change
      of that mark kind on that field: an undo of their removal of a mark
      someone else placed (``_recorded_restore``). The recorded body is used,
      ``chars`` included. Any other mark is the sender's own, and a proposed
      deletion's ``chars`` are the ones the applier reads from its range."""
    out = {k: v for k, v in payload.items() if k not in K.SNAPSHOT_ONLY.get(kind, ())}
    if kind == K.BLOCK_ADD and isinstance(out.get("content"), dict):
        lifted = lifted_fields(str(out.get("kind")))
        out["content"] = {k: v for k, v in out["content"].items() if k not in lifted}
    if kind == K.BLOCK_SET and (b := store.blocks.get(str((target or {}).get("bid") or ""))) is not None \
            and (target or {}).get("path") in lifted_fields(b.kind):
        raise ValueError(f"{b.kind}.{(target or {})['path']} is written by the loop's lift, not by a client")
    out.pop("set_by", None)
    out.pop("prev_set_by", None)
    if kind in ("amend", "propose"):
        out["bodies"] = [{"kind": b["kind"], "target": b.get("target") or {},
                          "payload": client_payload(store, b["kind"], b.get("payload") or {}, actor,
                                                    b.get("target") or {})}
                         for b in out.get("bodies") or []]
    if kind != K.TEXT_MARK:
        return out
    if any(k in out for k in ("actor", "op", "at")) and (
            recorded := _recorded_restore(store, actor, target or {}, out)):
        return dict(recorded)
    for k in ("actor", "op", "at"):
        out.pop(k, None)
    if out.get("kind") == SUGGEST_DEL:
        out["attrs"] = {k: v for k, v in (out.get("attrs") or {}).items() if k != "chars"}
    return out


def _recorded_restore(store: Store, actor: str, target: dict, payload: dict) -> dict | None:
    """The ``text.mark`` payload that ``payload`` repeats (``chars`` aside)
    from the recorded inverse of one of ``actor``'s own mark ops on that field,
    or None. Walking back from the newest op: the sender's own ops are passed
    (an undo flushes several of them together), and the walk stops with None
    at another actor's mark op or verdict that removed or added a mark of this
    placer (by its op id): someone else changed this mark since, so an old undo
    restores nothing in the placer's name."""
    def key(p: dict) -> str:
        return json.dumps({**p, "attrs": {k: v for k, v in (p.get("attrs") or {}).items() if k != "chars"}},
                          sort_keys=True)
    where, placer, want = (target.get("bid"), target.get("path", "text")), payload.get("op"), key(payload)
    for o in reversed(store.ops):
        if o.conflict:
            continue
        if o.kind in K.TURNS and o.actor != actor and any(
                st.get("do") == "mark" and (st.get("bid"), st.get("path")) == where
                and (st.get("mark") or {}).get("op") == placer for st in o.payload.get("steps") or []):
            return None         # a verdict added or removed this placer's mark since
        if o.kind != K.TEXT_MARK or (o.target.get("bid"), o.target.get("path", "text")) != where:
            continue
        if o.actor == actor:
            got = next((b["payload"] for b in o.inverse
                        if b.get("kind") == K.TEXT_MARK and key(b.get("payload") or {}) == want), None)
            if got is not None:
                return got
        elif any(m.get("op") == placer for m in (o.payload.get("removed") or []) + (o.payload.get("added") or [])):
            return None
    return None


def resolve_provisional(payload: dict, minted: dict[str, str], sent: dict[str, str] | None = None) -> dict:
    """Replace ``["$cid", cid, k]`` anchors with ``[op_id, k]`` once the insert
    with that client id has been minted. An unresolvable one is left as is and
    fails as an unknown anchor rather than being guessed at.

    ``k`` is the client's own scalar index into the text it sent; the applier
    stores NFC, so a client that sent a decomposed ``e + ́`` counted one
    character more than the store has. ``sent`` lets the index be mapped
    instead of guessed, so a paste from a Mac anchors where the person meant."""
    out = dict(payload)
    for key in K.ANCHOR_KEYS:
        v = out.get(key)
        if isinstance(v, list) and len(v) == 3 and v[0] == "$cid" and minted.get(v[1]):
            out[key] = [minted[v[1]], _fold_index(v[2], (sent or {}).get(v[1]))]
    if isinstance(out.get("segments"), list):
        out["segments"] = [[minted.get(seg[1], seg[0]) if isinstance(seg[0], str) and seg[0] == "$cid" else seg[0], *seg[1 + (seg[0] == "$cid"):]]
                           if isinstance(seg, list) and seg and seg[0] == "$cid" else seg for seg in out["segments"]]
    return out


def _fold_index(k: int, sent: str | None) -> int:
    """The index of the client's k-th scalar in the NFC text the store kept."""
    if not sent or nfc(sent) == sent:
        return k
    scalars = list(sent)
    return max(0, len(nfc("".join(scalars[: k + 1]))) - 1)
