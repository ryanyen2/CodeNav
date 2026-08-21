"""Whether a large file holds intent codoc can address — decided by the parse.

The indexer skipped any file over 1.5 MB, reasoning that a minified bundle, a
generated blob, or a vendored single-file lib is not authored intent and that
parsing and embedding one stalls the loop. The reasoning is right; the measure is
a proxy, and it comes apart exactly where it matters. `test/altair` ships two
sibling modules from one generator: `channels.py` at 1.20 MB, 786 definitions,
indexed — and `core.py` at 1.60 MB, 923 definitions, invisible. Same directory,
same generator, same shape, and what decided between them was 400 KB. `core.py` is
the Vega-Lite schema surface: the single file a reader most needs described.

What actually distinguishes a blob is its SHAPE once parsed, and the parse says so
plainly. Measured over the 826 Python and TypeScript files in this repo and its
corpora, the largest median chunk any of them has is 16,097 bytes (a re-export
`__init__.py` that is one chunk). A one-line data module parses to one chunk with a
median of 1,248,897; a vendored file of four enormous generated functions has a
median of 105,792. Two and a half orders of magnitude of daylight, so
`ORDINARY_DEFINITION_BYTES` sits between them and does not need to be delicate.

So size decides one thing only — whether to READ the file at all, which is a memory
and latency bound and nothing to do with intent (`READ_CEILING_BYTES`; parse and
hash measured at 0.36 s/MB, so 4 MB is ~1.3 s once). Being a cost bound, it is per
KIND of file rather than one number: a notebook's bytes are mostly its outputs, which
nothing parses, so it is held to `NOTEBOOK_READ_CEILING_BYTES` — see `read_ceiling`. Past 1.5 MB a file gets a
*hearing* instead of a silent drop: read it, parse it, and index it if the parse
found definitions of the size a person writes. The hearing is free, because the
indexer parses the file in the next breath anyway.

The ceiling is now a bound on what the DESCRIBING pass can carry, not a guess about
the author. `loop/payload.py` keeps one crowded file's prompt finite by conceding
down to its signatures, and at 4 MB that floor is still a call worth making; well
past it, it is not. Raising the ceiling further waits on splitting a crowded file's
bootstrap pass, which is a different piece of work — so the number stays where the
downstream can honor it, and `pipelines/indexing/survey.py` reports every file it
turns away rather than letting the tree quietly not mention them.
"""
from __future__ import annotations

import pathlib
import statistics
from collections.abc import Iterable

#: past this many bytes a file is not read at all — memory and latency, not intent
READ_CEILING_BYTES = 4_000_000

#: the same bound for a notebook, where the bytes are somewhere else entirely
NOTEBOOK_READ_CEILING_BYTES = 20_000_000

#: at or under this many bytes a file is indexed with no questions asked
HEARING_BYTES = 1_500_000

#: a definition larger than this is not the size a person writes one
ORDINARY_DEFINITION_BYTES = 40_000


def read_ceiling(file_path: str) -> int:
    """The ceiling that applies to *file_path*.

    One number was right while every readable file was read the same way. A notebook
    is not: its size is dominated by OUTPUT — a base64 PNG per plot, a megabyte of
    captured stdout — and none of that is ever parsed, hashed, or shown to a prompt.
    The adapter builds a document out of the cells' source alone, which for a 12 MB
    notebook of figures is a few tens of KB. Holding it to the code ceiling would turn
    away a file whose actual cost is a JSON parse, and turn it away for bytes that
    exist because somebody RAN the notebook — so re-running it would decide whether
    codoc can see it.

    Five times, not unbounded: the file is still read into memory whole and decoded
    before anything can tell where its bytes went, so there has to be a number, and
    this one keeps the transient cost of the worst case in the same range as a 4 MB
    source file's parse.
    """
    if pathlib.Path(file_path).suffix.lower() == ".ipynb":
        return NOTEBOOK_READ_CEILING_BYTES
    return READ_CEILING_BYTES


def too_large_to_read(size: int, *, ceiling: int = READ_CEILING_BYTES) -> bool:
    """True when *size* is past what the loop will pull into memory and describe.

    Callers that hold a path should pass ``ceiling=read_ceiling(path)``. The default
    stays the code ceiling so a caller that has only a size is held to the stricter of
    the two.
    """
    return size > ceiling


def needs_hearing(size: int, *, threshold: int = HEARING_BYTES) -> bool:
    """True when *size* is large enough that the parse should be consulted.

    Below the threshold nothing is consulted and nothing changes: a small module
    that happens to be one enormous dict is still indexed, exactly as before. The
    hearing exists to give a big file a chance, never to take one away.
    """
    return size > threshold


def holds_definitions(
    chunk_sizes: Iterable[int],
    *,
    ordinary: int = ORDINARY_DEFINITION_BYTES,
) -> bool:
    """True when a parse of that shape is somebody's code, addressable by feature.

    The test is the MEDIAN definition, not the count and not the largest. Count says
    nothing — a blob can parse to many pieces and a hand-written module to a few. The
    largest says nothing either: real files carry one outlier (a big dispatch table,
    a long `__init__`) without ceasing to be readable code, and `store/db.py` has a
    55 KB chunk among 95. The median asks the question that matters — *is this file
    made of units a person could read one at a time* — and a file of a few enormous
    generated functions answers no however many bytes it spans.

    A parse that found nothing is not addressable, which is also the honest answer:
    there is nothing for a feature to bind to, so indexing it would be a no-op that
    reads as coverage.
    """
    sizes = list(chunk_sizes)
    if not sizes:
        return False
    return statistics.median(sizes) <= ordinary
