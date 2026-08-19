"""Reading the export into rows.

Every bank exports a different CSV and none of them agree on column names, so the
header is matched loosely against a list of things banks actually call each
field. What comes out is a Row, and every rule downstream works on those rather
than on whatever the bank happened to write.

Nothing here decides anything. Every judgement belongs to a policy module, where
it can be found and argued with.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# What banks call each field. The first match wins, so the more specific names
# come first: "transaction date" before "date", or a bank that exports both would
# get the posting date and quietly shift every transaction near a month end.
COLUMNS = {
    "made": ["transaction date", "trans date", "date of transaction", "date"],
    "posted": ["posting date", "posted date", "post date", "value date"],
    "description": ["description", "merchant", "details", "narrative", "payee"],
    "amount": ["amount", "value", "debit/credit"],
    "account": ["account", "account name", "account number"],
    "reference": ["reference", "id", "transaction id"],
}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y"]


@dataclass
class Row:
    made: date
    posted: date
    description: str
    amount: Decimal
    account: str = ""
    reference: str = ""
    line: int = 0

    @property
    def is_money_out(self) -> bool:
        # A negative amount is money leaving the account. Some banks export the
        # opposite and put spending in as positive; those exports have to be
        # flipped before they reach here, which sign_convention does.
        return self.amount < 0


def _pick(header: list[str], names: list[str]) -> int | None:
    lowered = [h.strip().casefold() for h in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(text: str) -> Decimal | None:
    cleaned = text.strip().replace(",", "").replace("£", "").replace("$", "").replace("€", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]      # accountants' parentheses
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def read(raw: str) -> list[Row]:
    """A CSV export into rows. Anything unreadable is skipped, not guessed at."""
    reader = csv.reader(io.StringIO(raw))
    try:
        header = next(reader)
    except StopIteration:
        return []

    index = {field: _pick(header, names) for field, names in COLUMNS.items()}
    if index["amount"] is None or index["made"] is None:
        return []

    rows: list[Row] = []
    for number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue

        def cell(field: str) -> str:
            position = index[field]
            return record[position] if position is not None and position < len(record) else ""

        made = parse_date(cell("made"))
        amount = parse_amount(cell("amount"))
        if made is None or amount is None:
            continue
        posted = parse_date(cell("posted")) or made
        rows.append(Row(
            made=made, posted=posted, description=cell("description").strip(),
            amount=amount, account=cell("account").strip(),
            reference=cell("reference").strip(), line=number,
        ))
    return rows
