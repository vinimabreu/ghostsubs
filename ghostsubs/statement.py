"""Read a bank or card statement exported as CSV.

Banks do not agree on anything: the delimiter (comma, semicolon, tab), the
header language, the decimal mark, the date order, whether a charge is
negative or positive, and whether the file starts with a few lines about the
account. This reader finds the header row, maps the columns, and decides each
convention once for the whole file. When it can't decide (a file whose dates
all fit both day-first and month-first, or as many negatives as positives),
it stops and says which flag settles it, instead of guessing.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .clean import squash, strip_accents

DELIMITERS = (",", ";", "\t", "|")
DATE_ORDERS = ("ymd", "dmy", "mdy")

# Header names after normalising (no accents, lowercase, no currency, no punctuation).
# Earlier names win when a file has two of a kind: a transaction date beats a posting date.
DATE_NAMES = ("date", "transaction date", "trans date", "data", "data lancamento", "data de lancamento",
              "data da compra", "data compra", "data movimento", "data mov", "dt", "posted date",
              "post date", "posting date", "booking date", "value date")
DESCRIPTION_NAMES = ("description", "descricao", "title", "titulo", "historico", "memo", "payee", "details",
                     "merchant", "estabelecimento", "lancamento", "name", "narrative", "transaction",
                     "descricao do lancamento", "detalhes")
AMOUNT_NAMES = ("amount", "valor", "value", "montante", "quantia", "valor da transacao", "transaction amount")
DEBIT_NAMES = ("debit", "debito", "debits", "debit amount", "withdrawal", "withdrawals", "saida", "saidas",
               "money out", "paid out")
CREDIT_NAMES = ("credit", "credito", "credits", "credit amount", "deposit", "deposits", "entrada", "entradas",
                "money in", "paid in")
TYPE_NAMES = ("type", "tipo", "transaction type", "d/c", "dc", "debit/credit", "cr/dr", "dr/cr", "natureza")

_CURRENCY = re.compile(r"R\$|US\$|\$|€|£|\bBRL\b|\bUSD\b|\bEUR\b|\bGBP\b", re.I)
_CURRENCY_SHOWN = {"r$": "R$", "brl": "R$", "us$": "US$", "$": "$", "usd": "$", "€": "€", "eur": "€",
                   "£": "£", "gbp": "£"}
_BALANCE_ROW = re.compile(r"^\s*(saldo|balance|opening balance|closing balance|total|subtotal|s a l d o)\b",
                          re.I)
_ISO = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:$|[\sT])")
_NUMERIC = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?:$|\s)")


class StatementError(ValueError):
    """The file can't be read as a statement, or a convention can't be decided."""


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    amount: Decimal  # positive: money out (a charge); negative: money in
    source: str
    line: int


@dataclass
class Statement:
    transactions: list[Transaction]
    currency: str = ""
    decimal: str = "."
    date_order: str = "ymd"
    debits: str = "negative"
    delimiter: str = ","
    skipped: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    duplicates: int = 0
    debits_from: str = ""  # what decided the sign: columns, markers, type column, --debits, or the vote

    @property
    def charges(self) -> list[Transaction]:
        return [t for t in self.transactions if t.amount > 0]

    @property
    def credits(self) -> list[Transaction]:
        return [t for t in self.transactions if t.amount < 0]

    @property
    def first(self) -> date | None:
        return min((t.date for t in self.transactions), default=None)

    @property
    def last(self) -> date | None:
        return max((t.date for t in self.transactions), default=None)


@dataclass(frozen=True)
class _Columns:
    date: int
    description: int
    amount: int | None
    debit: int | None
    credit: int | None
    kind: int | None
    currency: str


def decode(data: bytes) -> str:
    """UTF-8 (with or without a byte order mark), else Windows-1252, which Brazilian and
    US bank exports still use."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _header_name(cell: str) -> str:
    text = strip_accents(cell).casefold()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = _CURRENCY.sub(" ", text)
    text = re.sub(r"[^a-z0-9/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find(names: list[str], wanted: tuple[str, ...], taken: set[int]) -> int | None:
    best: tuple[int, int] | None = None
    for index, name in enumerate(names):
        if index in taken:
            continue
        for rank, candidate in enumerate(wanted):
            if name == candidate or name.startswith(candidate + " "):
                exact = 0 if name == candidate else 1
                score = (rank * 2 + exact, index)
                if best is None or score < best:
                    best = score
                break
    return best[1] if best else None


def map_header(cells: list[str]) -> _Columns | None:
    names = [_header_name(cell) for cell in cells]
    taken: set[int] = set()
    found: dict[str, int | None] = {}
    for key, wanted in (("date", DATE_NAMES), ("debit", DEBIT_NAMES), ("credit", CREDIT_NAMES),
                        ("amount", AMOUNT_NAMES), ("description", DESCRIPTION_NAMES), ("kind", TYPE_NAMES)):
        index = _find(names, wanted, taken)
        found[key] = index
        if index is not None:
            taken.add(index)
    date_col, description = found["date"], found["description"]
    if date_col is None or description is None:
        return None
    if found["amount"] is None and (found["debit"] is None or found["credit"] is None):
        return None
    currency = ""
    for index in (found["amount"], found["debit"], found["credit"]):
        if index is not None:
            match = _CURRENCY.search(cells[index])
            if match:
                currency = _CURRENCY_SHOWN[match.group(0).casefold()]
                break
    use_split = found["amount"] is None
    return _Columns(date_col, description, found["amount"],
                    found["debit"] if use_split else None, found["credit"] if use_split else None,
                    found["kind"], currency)


def _locate_table(text: str) -> tuple[str, int, _Columns, list[list[str]]]:
    lines = text.splitlines()
    candidates: list[tuple[float, int, str, int, _Columns, list[list[str]]]] = []
    for order, delimiter in enumerate(DELIMITERS):
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        for index, row in enumerate(rows[:40]):
            if len(row) < 3:
                continue
            columns = map_header(row)
            if columns is None:
                continue
            body = [r for r in rows[index + 1:] if any(cell.strip() for cell in r)]
            consistent = sum(1 for r in body if len(r) >= len(row) - 1) / max(1, len(body))
            candidates.append((-consistent, index, delimiter, order, columns, rows[index:]))
            break
    if not candidates:
        sample = "; ".join(line.strip() for line in lines[:3] if line.strip())[:160]
        raise StatementError("no header row with a date, a description and an amount column "
                             f"(first lines: {sample!r})")
    candidates.sort(key=lambda c: (c[0], c[1], c[3]))
    _, index, delimiter, _, columns, rows = candidates[0]
    return delimiter, index, columns, rows


def _money_parts(cell: str) -> tuple[bool, str, str | None] | None:
    """(negative, digits, D/C marker) of one written amount, or None when it isn't one."""
    text = cell.strip().replace(" ", " ").replace("−", "-")
    if not text:
        return None
    marker = None
    match = re.search(r"\s*\b([DC])$", text, re.I)
    if match and re.search(r"\d", text[:match.start()]):
        marker = match.group(1).upper()
        text = text[:match.start()]
    negative = False
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    text = _CURRENCY.sub("", text).replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    elif text.endswith("-"):
        negative, text = not negative, text[:-1]
    elif text.startswith("+"):
        text = text[1:]
    if not re.fullmatch(r"\d[\d.,]*|[.,]\d+", text):
        return None
    return negative, text, marker


def _decimal_mark(samples: list[str]) -> str:
    votes: Counter[str] = Counter()
    for digits in samples:
        last = max(digits.rfind(","), digits.rfind("."))
        if last < 0:
            continue
        after = len(digits) - last - 1
        mark = digits[last]
        if after in (1, 2):
            votes[mark] += 2
        elif after == 3 and digits.count(mark) == 1 and ("," in digits) != ("." in digits):
            votes["." if mark == "," else ","] += 1
    if not votes:
        return "."
    return "," if votes[","] > votes["."] else "."


def _to_decimal(digits: str, mark: str) -> Decimal | None:
    other = "." if mark == "," else ","
    text = digits.replace(other, "")
    if text.count(mark) > 1:
        return None
    text = text.replace(mark, ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normalise_order(given: str | None) -> str | None:
    if given is None:
        return None
    text = given.strip().casefold().replace("%", "")
    aliases = {"yyyy-mm-dd": "ymd", "ymd": "ymd", "y-m-d": "ymd", "dd/mm/yyyy": "dmy", "dmy": "dmy",
               "d/m/y": "dmy", "mm/dd/yyyy": "mdy", "mdy": "mdy", "m/d/y": "mdy"}
    if text not in aliases:
        raise StatementError(f"--date-format {given!r}: use dmy (31/01/2026), mdy (01/31/2026) or ymd (2026-01-31)")
    return aliases[text]


def _date_order(values: list[str], forced: str | None) -> str:
    if forced:
        return forced
    day_first = month_first = False
    numeric = 0
    for value in values:
        match = _NUMERIC.match(value)
        if not match:
            continue
        numeric += 1
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            day_first = True
        if second > 12:
            month_first = True
    if not numeric:
        return "ymd"
    if day_first and month_first:
        raise StatementError("the dates disagree: some only work day-first (31/01) and some only month-first "
                             "(01/31). Fix the file or pass --date-format dmy or mdy")
    if day_first:
        return "dmy"
    if month_first:
        return "mdy"
    raise StatementError(f"every date in this file fits both day/month and month/day (no day above 12 in "
                         f"{numeric} dates), so the order can't be told. Pass --date-format dmy (31/01/2026) "
                         "or --date-format mdy (01/31/2026)")


def parse_date(value: str, order: str) -> date | None:
    """A date written as YYYY-MM-DD, or DD/MM/YYYY or MM/DD/YYYY in the given order
    (``-`` and ``.`` work as separators too; two-digit years are 20xx; a time after it is ignored)."""
    match = _ISO.match(value)
    if match:
        year, month, day = (int(g) for g in match.groups())
    else:
        match = _NUMERIC.match(value)
        if not match:
            return None
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        if order == "mdy":
            month, day = first, second
        else:
            day, month = first, second
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _looks_like_date(value: str) -> bool:
    return bool(_ISO.match(value) or _NUMERIC.match(value))


# Values of a type column, compared without accents or case (Débito, DEBITO and debito are one).
_DEBIT_WORDS = re.compile(r"^(d|db|dr|debit|debito|saida|withdrawal|purchase|sale|compra|pagamento)\b")
_CREDIT_WORDS = re.compile(r"^(c|cr|credit|credito|entrada|deposit|refund|return|payment|estorno)\b")

# Descriptions that say which way the money went. Used only to doubt a sign decided by vote.
_MONEY_IN = re.compile(r"\b(salario|salary|payroll|pix recebido|ted recebida|doc recebido|transferencia recebida|"
                       r"recebid[oa]|deposit|deposito|direct dep|refund|estorno|reembolso|rendimento|dividend)")
_MONEY_OUT = re.compile(r"\b(compra|purchase|pagto|pagamento|saque|withdrawal|atm|pos|debit card|debito automatico|"
                        r"deb autom)\b")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text or "")).casefold().strip()


def read_text(text: str, *, source: str = "<statement>", date_format: str | None = None,
              debits: str | None = None) -> Statement:
    """Read one statement's text. ``debits`` is ``negative`` (bank style: a charge is -39.90)
    or ``positive`` (card style: a charge is 39.90); by default it is decided from the file."""
    if debits not in (None, "negative", "positive"):
        raise StatementError("--debits is negative (a charge is written -39.90) or positive (39.90)")
    forced_order = _normalise_order(date_format)
    delimiter, header_index, columns, rows = _locate_table(text)
    needed = max(i for i in (columns.date, columns.description, columns.amount, columns.debit, columns.credit,
                             columns.kind) if i is not None)

    Parsed = tuple[int, str, str, tuple[bool, str, str | None] | None, tuple[bool, str, str | None] | None, str]
    parsed: list[Parsed] = []
    skipped: list[str] = []
    for offset, row in enumerate(rows[1:], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) <= needed:
            skipped.append(f"line {offset}: {len(row)} columns")
            continue
        when = row[columns.date].strip()
        description = re.sub(r"\s+", " ", row[columns.description]).strip()
        if not _looks_like_date(when):
            skipped.append(f"line {offset}: no date ({when[:20]!r})")
            continue
        if _BALANCE_ROW.match(description):
            skipped.append(f"line {offset}: balance row ({description[:30]!r})")
            continue
        kind_text = row[columns.kind].strip() if columns.kind is not None else ""
        if columns.amount is not None:
            parts = _money_parts(row[columns.amount])
            if parts is None:
                skipped.append(f"line {offset}: no amount ({row[columns.amount].strip()[:20]!r})")
                continue
            parsed.append((offset, when, description, parts, None, kind_text))
        else:
            assert columns.debit is not None and columns.credit is not None
            debit = _money_parts(row[columns.debit])
            credit = _money_parts(row[columns.credit])
            if debit is None and credit is None:
                skipped.append(f"line {offset}: no debit or credit")
                continue
            parsed.append((offset, when, description, debit, credit, kind_text))
    if not parsed:
        raise StatementError("the header was found but no row below it has a date and an amount")

    digit_samples = [p[1] for row in parsed for p in (row[3], row[4]) if p is not None]
    mark = _decimal_mark(digit_samples)
    order = _date_order([row[1] for row in parsed], forced_order)
    currency = columns.currency
    if not currency:
        for row in rows[1:]:
            for index in (columns.amount, columns.debit, columns.credit):
                if index is not None and index < len(row):
                    match = _CURRENCY.search(row[index])
                    if match:
                        currency = _CURRENCY_SHOWN[match.group(0).casefold()]
                        break
            if currency:
                break

    marker_kinds = {row[3][2] for row in parsed if row[3] is not None and row[3][2]}
    typed = columns.kind is not None and all(
        _DEBIT_WORDS.match(_plain(row[5])) or _CREDIT_WORDS.match(_plain(row[5])) for row in parsed)
    convention = ("columns" if columns.amount is None else
                  "marker" if marker_kinds else ("type column" if typed else ""))
    decided_by = {"columns": "debit and credit columns", "marker": "D/C markers",
                  "type column": "the type column"}.get(convention, "")
    if not convention:
        signs: Counter[str] = Counter()
        for item in parsed:
            parts = item[3]
            value = _to_decimal(parts[1], mark) if parts else None
            if parts and value:
                signs["negative" if parts[0] else "positive"] += 1
        total = sum(signs.values())
        if debits:
            convention, decided_by = debits, "--debits"
        elif total and signs["negative"] / total >= 0.6:
            convention, decided_by = "negative", "the sign of most rows"
        elif total and signs["positive"] / total >= 0.6:
            convention, decided_by = "positive", "the sign of most rows"
        else:
            raise StatementError(f"can't tell which sign is a charge: {signs['negative']} amounts are negative and "
                                 f"{signs['positive']} positive. Pass --debits negative (a charge is written "
                                 "-39.90) or --debits positive (a charge is written 39.90)")

    transactions: list[Transaction] = []
    for offset, when, description, first, second, kind_text in parsed:
        day = parse_date(when, order)
        if day is None:
            raise StatementError(f"{source} line {offset}: {when!r} is not a valid date as {order}")
        if convention == "columns":
            if first is not None and (value := _to_decimal(first[1], mark)):
                amount = abs(value)
            elif second is not None and (value := _to_decimal(second[1], mark)):
                amount = -abs(value)
            else:
                skipped.append(f"line {offset}: debit and credit are both zero")
                continue
        else:
            assert first is not None
            value = _to_decimal(first[1], mark)
            if value is None:
                skipped.append(f"line {offset}: unreadable amount {first[1]!r}")
                continue
            if value == 0:
                skipped.append(f"line {offset}: zero amount")
                continue
            if convention == "marker":
                if first[2] is not None:
                    out = first[2] == "D"
                elif marker_kinds == {"C"}:
                    out = True  # only money in is marked: an unmarked row is a charge
                elif marker_kinds == {"D"}:
                    out = False
                else:
                    out = first[0]
                amount = value if out else -value
            elif convention == "type column":
                amount = value if _DEBIT_WORDS.match(_plain(kind_text)) else -value
            elif convention == "negative":
                amount = value if first[0] else -value
            else:
                amount = -value if first[0] else value
        transactions.append(Transaction(day, description, amount, source, offset))

    if len(skipped) > max(10, len(transactions)):
        raise StatementError(f"{len(skipped)} rows could not be read and only {len(transactions)} could; "
                             f"first problem: {skipped[0]}")
    if decided_by == "the sign of most rows":
        _doubt_the_vote(transactions, convention)
    transactions.sort(key=lambda t: (t.date, t.line))
    return Statement(transactions, currency=currency, decimal=mark, date_order=order, debits=convention,
                     delimiter=delimiter, skipped=skipped, files=[source], debits_from=decided_by)


def _doubt_the_vote(transactions: list[Transaction], convention: str) -> None:
    """A sign decided by counting rows can be backwards in an account that receives more often
    than it pays. If most rows that say money came in (salary, PIX received, deposit, refund)
    end up as charges, or most that say money went out end up as credits, stop and ask."""
    came_in = [t for t in transactions if _MONEY_IN.search(_plain(t.description))
               and not _MONEY_OUT.search(_plain(t.description))]
    went_out = [t for t in transactions if _MONEY_OUT.search(_plain(t.description))
                and not _MONEY_IN.search(_plain(t.description))]
    wrong_in = [t for t in came_in if t.amount > 0]
    wrong_out = [t for t in went_out if t.amount < 0]
    for wrong, group in ((wrong_in, came_in), (wrong_out, went_out)):
        if len(wrong) >= 3 and len(wrong) > len(group) / 2:
            sample = ", ".join(sorted({t.description for t in wrong})[:3])
            other = "negative" if convention == "positive" else "positive"
            raise StatementError(
                f"most rows are {'positive' if convention == 'positive' else 'negative'}, so a charge would be "
                f"{convention}, but then {len(wrong)} rows like {sample} land on the wrong side. Pass "
                f"--debits {other} if a charge is written {'-39.90' if other == 'negative' else '39.90'}, "
                f"or --debits {convention} to keep the vote")


def read_file(path: Path | str, *, date_format: str | None = None, debits: str | None = None) -> Statement:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise StatementError(f"can't read {path}: {error.strerror or error}") from error
    try:
        return read_text(decode(data), source=str(path), date_format=date_format, debits=debits)
    except StatementError as error:
        if str(error).startswith(str(path)):
            raise
        raise StatementError(f"{path}: {error}") from error


def read_files(paths: Sequence[Path | str], *, date_format: str | None = None, debits: str | None = None) -> Statement:
    """Several exports at once, for instance one per month. A row that appears in an earlier
    file (same date, description and amount) is counted once; repeats inside one file stay."""
    merged: Statement | None = None
    seen: Counter[tuple[date, str, Decimal]] = Counter()
    for path in paths:
        statement = read_file(path, date_format=date_format, debits=debits)
        if merged is None:
            merged = statement
            seen.update((t.date, squash(t.description), t.amount) for t in statement.transactions)
            continue
        here: Counter[tuple[date, str, Decimal]] = Counter()
        for transaction in statement.transactions:
            key = (transaction.date, squash(transaction.description), transaction.amount)
            here[key] += 1
            if here[key] <= seen[key]:
                merged.duplicates += 1
                continue
            merged.transactions.append(transaction)
        for key, count in here.items():
            seen[key] = max(seen[key], count)
        merged.files.extend(statement.files)
        merged.skipped.extend(f"{statement.files[0]} {s}" for s in statement.skipped)
        merged.currency = merged.currency or statement.currency
    if merged is None:
        raise StatementError("no statement file given")
    merged.transactions.sort(key=lambda t: (t.date, t.source, t.line))
    return merged
