"""Write the made-up statements in examples/ and their ground truth.

Every row is invented here, from a fixed seed: the people, landlords,
restaurants, card numbers, phone numbers (555 numbers) and references. Brand
names in descriptors are real on purpose, since recognising them is the job.
Run it again and the files come out byte for byte the same.

    python scripts/make_examples.py               # into examples/
    python scripts/make_examples.py /tmp/copy     # anywhere else, to compare
    python scripts/make_examples.py /tmp/s7 7     # another seed

It writes the four CSV files and examples/truth/statements.json. The naming set,
examples/truth/descriptors.json, is written by hand.
"""

from __future__ import annotations

import calendar
import csv
import io
import json
import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


@dataclass
class Book:
    """Rows of one statement. amount > 0 is a charge, amount < 0 money in."""
    start: date
    end: date
    rows: list[tuple[date, str, Decimal, str]] = field(default_factory=list)

    def add(self, day: date, description: str, amount: Decimal | str, kind: str = "") -> None:
        if self.start <= day <= self.end:
            self.rows.append((day, description, Decimal(amount), kind))

    def sorted(self) -> list[tuple[date, str, Decimal, str]]:
        return sorted(self.rows, key=lambda r: (r[0], r[1], r[2]))


def month_days(start: date, end: date, day: int) -> list[date]:
    out: list[date] = []
    year, month = start.year, start.month
    while True:
        last = calendar.monthrange(year, month)[1]
        current = date(year, month, min(day, last))
        if current > end:
            return out
        if current >= start:
            out.append(current)
        month += 1
        if month > 12:
            year, month = year + 1, 1


def jitter(rng: random.Random, day: date, chance: float = 0.25, most: int = 2) -> date:
    """Card networks post a scheduled charge a day or two late now and then."""
    return day + timedelta(days=rng.randint(1, most)) if rng.random() < chance else day


def money(rng: random.Random, low: float, high: float) -> Decimal:
    return Decimal(f"{rng.uniform(low, high):.2f}")


def ref(rng: random.Random, n: int = 9) -> str:
    while True:
        text = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(n))
        if any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
            return text


def every(rng: random.Random, start: date, end: date, low: int, high: int) -> list[date]:
    days, current = [], start + timedelta(days=rng.randint(0, high))
    while current <= end:
        days.append(current)
        current += timedelta(days=rng.randint(low, high))
    return days


# ---------------------------------------------------------------------------
# bank-a.csv: a Brazilian checking account. Semicolons, dd/mm/yyyy, 1.234,56,
# charges negative, "SALDO" lines in between, UTF-8 with a byte order mark.
# ---------------------------------------------------------------------------

def bank_a(rng: random.Random) -> tuple[Book, list[dict[str, Any]]]:
    book = Book(date(2025, 9, 1), date(2026, 10, 16))
    s, e = book.start, book.end
    truth: list[dict[str, Any]] = []

    for d in month_days(s, e, 5):
        variant = "NETFLIX.COM" if d < date(2026, 3, 1) else "NETFLIX ENTRETENIMENTO"
        book.add(jitter(rng, d), variant, "39.90" if d < date(2026, 9, 1) else "44.90")
    truth.append({"merchant": ["Netflix"], "cadence": "monthly", "amount": "44.90", "bucket": "subscription",
                  "status": "active", "changes": [["2026-09-05", "39.90", "44.90"]], "match": "NETFLIX",
                  "note": "price increase; two descriptor variants"})

    for d in month_days(s, e, 12):
        variant = ("PAYPAL *SPOTIFY 4025550134" if d < date(2026, 1, 1)
                   else "EBN*SPOTIFY" if d < date(2026, 6, 1) else "SPOTIFY AB STOCKHOLM SE")
        book.add(jitter(rng, d), variant, "21.90")
    truth.append({"merchant": ["Spotify"], "cadence": "monthly", "amount": "21.90", "bucket": "subscription",
                  "status": "active", "note": "three descriptor variants"})

    book.add(date(2026, 6, 17), "DM *HBOMAX", "1.99")
    for d in month_days(date(2026, 7, 1), e, 17):
        book.add(d, "DM *HBOMAX", "34.90")
    truth.append({"merchant": ["Max", "HBO Max"], "cadence": "monthly", "amount": "34.90", "bucket": "subscription",
                  "status": "active", "trial": "1.99", "new": True, "note": "trial then paid, new"})

    for d in month_days(s, date(2026, 8, 31), 8):
        book.add(jitter(rng, d), "GLOBOPLAY", "22.90")
    truth.append({"merchant": ["Globoplay"], "cadence": "monthly", "amount": "22.90", "bucket": "subscription",
                  "status": "missing", "note": "cancelled after August"})

    for d in month_days(s, e, 20):
        book.add(jitter(rng, d), "AMAZONPRIMEBR", "19.90")
    truth.append({"merchant": ["Amazon Prime", "Prime", "Amazon"], "cadence": "monthly", "amount": "19.90",
                  "bucket": "subscription", "status": "active", "note": "glued descriptor"})
    purchases = [date(2025, 10, 3), date(2025, 11, 28), date(2026, 1, 15), date(2026, 3, 22), date(2026, 7, 9)]
    for d in purchases:
        book.add(d, "AMAZON BR", money(rng, 45, 420))
    refunded = [r for r in book.rows if r[1] == "AMAZON BR" and r[0] == date(2026, 3, 22)][0]
    book.add(date(2026, 4, 2), "ESTORNO AMAZON BR", -refunded[2])

    for d in month_days(s, e, 2):
        book.add(jitter(rng, d), "IFD*IFOOD CLUB", "5.95")
    truth.append({"merchant": ["iFood Club", "iFood"], "cadence": "monthly", "amount": "5.95",
                  "bucket": "subscription", "status": "active", "note": "membership among food orders"})
    restaurants = ["IFD*BURGUER DO BAIRRO", "IFD*SUSHI DA PRACA", "IFD*PIZZARIA BELLA NAPOLI", "IFD*ACAI DO PORTO",
                   "IFD*MARMITA DA VILA"]
    for d in every(rng, s, e, 3, 9):
        book.add(d, rng.choice(restaurants), money(rng, 28, 96))

    for d in month_days(s, e, 26):
        book.add(jitter(rng, d), "UBER *ONE", "9.90")
    truth.append({"merchant": ["Uber One", "Uber"], "cadence": "monthly", "amount": "9.90", "bucket": "subscription",
                  "status": "active", "note": "membership among rides"})
    for d in every(rng, s, e, 1, 5):
        book.add(d, "UBER *TRIP", money(rng, 9, 58))

    for d in month_days(s, e, 7):
        book.add(d, "DEB AUTOM SMART FIT", "129.90")
    truth.append({"merchant": ["Smart Fit"], "cadence": "monthly", "amount": "129.90", "bucket": "subscription",
                  "status": "active"})

    for d in month_days(s, e, 10):
        book.add(d, "TED ALUGUEL IMOB HORIZONTE", "2350.00")
    truth.append({"merchant": ["Imobiliária Horizonte", "Horizonte"],
                  "cadence": "monthly", "amount": "2350.00", "bucket": "other", "status": "active", "note": "rent"})

    for d in month_days(s, e, 15):
        book.add(jitter(rng, d), "VIVO FIBRA", "119.99")
    truth.append({"merchant": ["Vivo Fibra", "Vivo"], "cadence": "monthly", "amount": "119.99", "bucket": "other",
                  "status": "active", "note": "internet bill"})

    for d in month_days(s, e, 18):
        book.add(d, "PORTO SEGURO AUTO", "189.40")
    truth.append({"merchant": ["Porto Seguro", "Porto Seguro Auto"], "cadence": "monthly", "amount": "189.40",
                  "bucket": "other", "status": "active", "note": "insurance bill"})

    for d in month_days(s, e, 22):
        book.add(jitter(rng, d, 0.5, 3), "LUMEVALE ENERGIA DISTRIB", money(rng, 180, 340))
    for d in month_days(s, e, 25):
        book.add(d, "PAGTO FATURA CARTAO", money(rng, 1200, 3800))
    for d in every(rng, s, e, 4, 10):
        book.add(d, "SUPERMERCADO BOA SAFRA", money(rng, 60, 380))
    for d in every(rng, s, e, 9, 30):
        book.add(d, "DROGARIA BOM CUIDADO", money(rng, 18, 140))
    for d in every(rng, s, e, 12, 40):
        book.add(d, rng.choice(["RESTAURANTE SABOR DA SERRA", "PADARIA NOVA AURORA", "BAR DO MIRANTE"]),
                 money(rng, 35, 210))

    for d in month_days(s, e, 5):
        book.add(d, "CREDITO SALARIO", "-8500.00")
    for d in every(rng, s, e, 10, 35):
        book.add(d, "PIX RECEBIDO", -money(rng, 50, 900))
    return book, truth


def write_bank_a(book: Book, path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Data", "Histórico", "Valor (R$)"])
    writer.writerow([book.start.strftime("%d/%m/%Y"), "SALDO ANTERIOR", "3.215,40"])
    balance = Decimal("3215.40")
    previous = None
    for day, description, amount, _ in book.sorted():
        if previous is not None and day != previous and day.day % 7 == 0:
            writer.writerow([previous.strftime("%d/%m/%Y"), "SALDO DO DIA", _br(balance)])
        balance -= amount
        writer.writerow([day.strftime("%d/%m/%Y"), description, _br(-amount)])
        previous = day
    path.write_bytes(b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"))


def _br(amount: Decimal) -> str:
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if amount < 0 else "") + text


# ---------------------------------------------------------------------------
# card-b.csv: a US credit card. Commas, MM/DD/YYYY, a Type column, purchases
# negative, 26 months so yearly plans renew in view.
# ---------------------------------------------------------------------------

def card_b(rng: random.Random) -> tuple[Book, list[dict[str, Any]]]:
    book = Book(date(2024, 8, 1), date(2026, 10, 14))
    s, e = book.start, book.end
    truth: list[dict[str, Any]] = []

    for d in (date(2024, 10, 23), date(2025, 10, 23)):
        book.add(d, "ADOBE *CREATIVE CLOUD 408-555-0122 CA", "239.88", "Sale")
    truth.append({"merchant": ["Adobe", "Adobe Creative Cloud"], "cadence": "yearly", "amount": "239.88",
                  "bucket": "subscription", "status": "active", "renews_in": 9, "note": "yearly, renews soon"})

    for d in (date(2024, 12, 2), date(2025, 12, 2)):
        book.add(d, f"Amazon Prime*{ref(rng, 8)}", "139.00", "Sale")
    truth.append({"merchant": ["Amazon Prime", "Prime", "Amazon"], "cadence": "yearly", "amount": "139.00",
                  "bucket": "subscription", "status": "active", "note": "yearly among store purchases"})
    for d in every(rng, s, e, 6, 30):
        book.add(d, rng.choice([f"AMZN Mktp US*{ref(rng)}", f"Amazon.com*{ref(rng, 6)}"]), money(rng, 9, 160), "Sale")
    returned = [r for r in book.rows if r[1].startswith("AMZN Mktp")][5]
    book.add(returned[0] + timedelta(days=9), returned[1], -returned[2], "Return")

    for d in month_days(s, e, 21):
        book.add(jitter(rng, d), "NETFLIX.COM 866-555-0142 CA", "15.49" if d < date(2025, 1, 1) else "17.99", "Sale")
    truth.append({"merchant": ["Netflix"], "cadence": "monthly", "amount": "17.99", "bucket": "subscription",
                  "status": "active", "changes": [["2025-01-21", "15.49", "17.99"]], "match": "NETFLIX"})

    for d in month_days(s, e, 9):
        book.add(jitter(rng, d), "HULU 877-555-0199 CA", "14.99" if d < date(2025, 10, 1) else "17.99", "Sale")
    truth.append({"merchant": ["Hulu"], "cadence": "monthly", "amount": "17.99", "bucket": "subscription",
                  "status": "active", "changes": [["2025-10-09", "14.99", "17.99"]], "match": "HULU"})

    for d in month_days(s, e, 3):
        book.add(jitter(rng, d), "Spotify USA", "11.99" if d < date(2026, 9, 1) else "12.99", "Sale")
    truth.append({"merchant": ["Spotify"], "cadence": "monthly", "amount": "12.99", "bucket": "subscription",
                  "status": "active", "changes": [["2026-09-03", "11.99", "12.99"]], "match": "Spotify"})

    for d in month_days(s, e, 27):
        variant = ("GOOGLE *YouTubePremium g.co/helppay#" if d < date(2025, 5, 1)
                   else "GOOGLE *YouTube Premium" if d < date(2026, 1, 1) else "YOUTUBEPREMIUM GOOGLE.COM CA")
        book.add(jitter(rng, d), variant, "13.99", "Sale")
    truth.append({"merchant": ["YouTube Premium", "YouTube"], "cadence": "monthly", "amount": "13.99",
                  "bucket": "subscription", "status": "active", "note": "three descriptor variants"})

    book.add(date(2026, 6, 28), f"Audible*{ref(rng)}", "0.99", "Sale")
    for d in month_days(date(2026, 7, 1), e, 28):
        book.add(d, f"Audible*{ref(rng)}", "14.95", "Sale")
    truth.append({"merchant": ["Audible"], "cadence": "monthly", "amount": "14.95", "bucket": "subscription",
                  "status": "active", "trial": "0.99", "new": True, "note": "trial then paid, new"})

    for d in month_days(s, date(2026, 8, 31), 15):
        book.add(jitter(rng, d), "HELP.MAX.COM", "15.99", "Sale")
    truth.append({"merchant": ["Max", "HBO Max"], "cadence": "monthly", "amount": "15.99", "bucket": "subscription",
                  "status": "missing", "note": "cancelled after August"})

    for d in (date(2024, 11, 20), date(2025, 11, 20)):
        book.add(d, "COSTCO *ANNUAL RENEWAL", "65.00", "Sale")
    truth.append({"merchant": ["Costco"], "cadence": "yearly", "amount": "65.00", "bucket": "subscription",
                  "status": "active", "note": "membership among weekly shopping"})
    for d in every(rng, s, e, 5, 12):
        book.add(d, "COSTCO WHSE #0482", money(rng, 38, 310), "Sale")

    hello = date(2026, 3, 2)
    for week in range(20):
        book.add(hello + timedelta(days=7 * week), "HELLOFRESH 646-555-0187 NY", "69.99", "Sale")
    truth.append({"merchant": ["HelloFresh"], "cadence": "weekly", "amount": "69.99", "bucket": "subscription",
                  "status": "missing", "note": "weekly, stopped in July"})

    for d in month_days(s, e, 17):
        book.add(d, "PLANET FIT*CLUB FEES", "24.99", "Sale")
    for d in (date(2025, 3, 17), date(2026, 3, 17)):
        book.add(d, "PLANET FIT*ANNUAL FEE", "49.00", "Sale")
    truth.append({"merchant": ["Planet Fitness"], "cadence": "monthly", "amount": "24.99", "bucket": "subscription",
                  "status": "active"})
    truth.append({"merchant": ["Planet Fitness"], "cadence": "yearly", "amount": "49.00", "bucket": "subscription",
                  "status": "active", "note": "yearly fee at the same merchant as the monthly one"})

    for d in month_days(s, e, 11):
        book.add(jitter(rng, d), "OPENAI *CHATGPT SUBSCR", "20.00", "Sale")
    truth.append({"merchant": ["OpenAI", "ChatGPT", "OpenAI ChatGPT"], "cadence": "monthly", "amount": "20.00",
                  "bucket": "subscription", "status": "active"})

    for d in month_days(s, e, 6):
        book.add(d, "NYTIMES*NYTIMES SUBSCR", "4.00" if d < date(2025, 8, 1) else "25.00", "Sale")
    truth.append({"merchant": ["New York Times", "The New York Times", "NYTimes"], "cadence": "monthly",
                  "amount": "25.00", "bucket": "subscription", "status": "active",
                  "changes": [["2025-08-06", "4.00", "25.00"]], "match": "NYTIMES", "note": "intro rate ends"})

    for d in month_days(s, e, 14):
        book.add(d, "APPLE.COM/BILL 866-555-0177 CA", "2.99", "Sale")
    book.add(date(2025, 6, 2), "APPLE.COM/BILL 866-555-0177 CA", "9.99", "Sale")
    truth.append({"merchant": ["Apple", "iCloud"], "cadence": "monthly", "amount": "2.99", "bucket": "subscription",
                  "status": "active", "note": "one app purchase on the same descriptor"})

    for d in month_days(s, e, 24):
        book.add(d, "VERIZON WRLS 555-01 CA", "85.00", "Sale")
    truth.append({"merchant": ["Verizon", "Verizon Wireless"], "cadence": "monthly", "amount": "85.00",
                  "bucket": "other", "status": "active", "note": "phone bill"})
    for d in month_days(s, e, 1):
        book.add(d, "GEICO *AUTO 800-555-0123 DC", "132.40", "Sale")
    truth.append({"merchant": ["GEICO", "GEICO Auto Insurance"], "cadence": "monthly", "amount": "132.40", "bucket": "other",
                  "status": "active", "note": "insurance bill"})
    for d in month_days(s, e, 19):
        book.add(jitter(rng, d, 0.5, 3), "HARBORLITE ELEC OF NY INTELL CK", money(rng, 70, 190), "Sale")

    for d in every(rng, s, e, 5, 9):
        book.add(d, "WHOLEFDS MKT 10234", money(rng, 42, 230), "Sale")
    for d in every(rng, s, e, 8, 25):
        book.add(d, "TRADER JOE S #552", money(rng, 25, 120), "Sale")
    for d in every(rng, s, e, 1, 4):
        hour = rng.choice(["8AM", "9AM", "5PM", "6PM", "11PM"])
        book.add(d, f"LYFT *RIDE {d.strftime('%a').upper()} {hour}", money(rng, 8, 46), "Sale")
    for d in every(rng, s, e, 2, 8):
        book.add(d, "UBER *TRIP HELP.UBER.COM", money(rng, 9, 52), "Sale")
    for d in every(rng, s, e, 1, 4):
        book.add(d, "SQ *CORNER BEAN CAFE", rng.choice(["4.75", "4.75", "4.75", "6.25"]), "Sale")
    for d in every(rng, s, e, 15, 45):
        book.add(d, rng.choice(["TST* LUCKY PIZZA", "TST* HARBOR TACO BAR", "SQ *MAPLE STREET BAKERY"]),
                 money(rng, 14, 85), "Sale")

    for d in month_days(s, e, 27):
        book.add(d, "AUTOPAY PAYMENT - THANK YOU", -money(rng, 900, 2400), "Payment")
    return book, truth


def write_card_b(book: Book, path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"])
    categories = {"Sale": "Shopping", "Payment": "", "Return": "Shopping"}
    for day, description, amount, kind in sorted(book.rows, key=lambda r: (r[0], r[1]), reverse=True):
        posted = day + timedelta(days=1)
        writer.writerow([day.strftime("%m/%d/%Y"), posted.strftime("%m/%d/%Y"), description,
                         categories.get(kind, ""), kind, f"{-amount:.2f}"])
    path.write_text(out.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# export-c.csv: a messy Brazilian card export. Tabs, Windows-1252, a preamble,
# "R$ 1.234,56", credits in parentheses or with -R$, padded descriptors, blank
# lines and a total row.
# ---------------------------------------------------------------------------

def export_c(rng: random.Random) -> tuple[Book, list[dict[str, Any]]]:
    book = Book(date(2025, 10, 1), date(2026, 10, 31))
    s, e = book.start, book.end
    truth: list[dict[str, Any]] = []

    for d in month_days(s, e, 4):
        variant = "DEEZER  PREMIUM" if d.month % 2 else "Deezer Premium  SAO PAULO"
        book.add(jitter(rng, d), variant, "22.90")
    truth.append({"merchant": ["Deezer"], "cadence": "monthly", "amount": "22.90", "bucket": "subscription",
                  "status": "active", "note": "two spellings"})

    for d in (date(2025, 10, 12), date(2026, 1, 12), date(2026, 4, 12), date(2026, 7, 12), date(2026, 10, 12)):
        book.add(d, "PLAYSTATION NETWORK", "119.90")
    truth.append({"merchant": ["PlayStation", "PlayStation Network", "PlayStation Plus"], "cadence": "quarterly",
                  "amount": "119.90", "bucket": "subscription", "status": "active"})

    for d in month_days(s, e, 9):
        book.add(jitter(rng, d), "EBN*CRUNCHYROLL", "14.99" if d < date(2026, 9, 1) else "19.99")
    truth.append({"merchant": ["Crunchyroll"], "cadence": "monthly", "amount": "19.99", "bucket": "subscription",
                  "status": "active", "changes": [["2026-09-09", "14.99", "19.99"]], "match": "EBN*CRUNCHYROLL"})

    for d in month_days(s, e, 21):
        book.add(d, "GOOGLE *Google One", "9.99")
    truth.append({"merchant": ["Google One", "Google"], "cadence": "monthly", "amount": "9.99",
                  "bucket": "subscription", "status": "active"})

    for d in (date(2025, 10, 6), date(2026, 10, 6)):
        book.add(d, "CANVA* I0412-5550142", "289.90")
    truth.append({"merchant": ["Canva"], "cadence": "yearly", "amount": "289.90", "bucket": "subscription",
                  "status": "active"})

    for d in month_days(s, date(2026, 6, 30), 14):
        book.add(jitter(rng, d), "PAG*DUOLINGO", "29.90")
    truth.append({"merchant": ["Duolingo"], "cadence": "monthly", "amount": "29.90", "bucket": "subscription",
                  "status": "missing", "note": "cancelled in June"})

    for d in month_days(s, e, 16):
        book.add(jitter(rng, d), "MP*MELIMAIS", "17.99")
    truth.append({"merchant": ["Meli+", "Meli Mais", "Mercado Livre", "Melimais"], "cadence": "monthly",
                  "amount": "17.99", "bucket": "subscription", "status": "active"})
    for d in every(rng, s, e, 10, 40):
        book.add(d, f"MERCADOLIVRE*{rng.randint(2, 6)}PRODUTOS", money(rng, 30, 650))

    for d in every(rng, s, e, 5, 11):
        book.add(d, "ASSAI ATACADISTA LJ 112", money(rng, 90, 520))
    for d in every(rng, s, e, 1, 4):
        book.add(d, "99APP *99RIDE", money(rng, 7, 39))
    for d in every(rng, s, e, 6, 20):
        book.add(d, rng.choice(["IFD*CANTINA DO LARGO", "IFD*POKE DA ORLA", "IFD*ESPETINHO DO CENTRO"]),
                 money(rng, 32, 110))
    for d in every(rng, s, e, 20, 60):
        book.add(d, rng.choice(["LOJAS AMERICANAS", "DECATHLON BR", "LIVRARIA PAGINA VIVA"]), money(rng, 40, 380))

    for d in month_days(s, e, 28):
        book.add(d, "PAGAMENTO RECEBIDO", -money(rng, 1500, 3200))
    book.add(date(2026, 2, 3), "ESTORNO DECATHLON BR", "-79.90")
    book.add(date(2026, 1, 28), "DECATHLON BR", "79.90")
    return book, truth


def write_export_c(book: Book, path: Path, rng: random.Random) -> None:
    lines = ["Extrato do cartao de credito", "Titular: CLIENTE EXEMPLO", "Cartao final 0000",
             f"Periodo: {book.start.strftime('%d/%m/%Y')} a {book.end.strftime('%d/%m/%Y')}", "",
             "data lançamento\tdescrição\tvalor"]
    total = Decimal(0)
    for day, description, amount, _ in book.sorted():
        total += amount
        if amount < 0 and "ESTORNO" in description:
            value = f"(R$ {_br(-amount)})"
        elif amount < 0:
            value = f"-R$ {_br(-amount)}"
        else:
            value = f"R$ {_br(amount)}"
        padded = description + " " * rng.choice([0, 0, 1, 3])
        lines.append(f"{day.strftime('%d/%m/%Y')}\t{padded}\t{value}")
        if rng.random() < 0.02:
            lines.append("")
    lines.append(f"\tTotal\tR$ {_br(total)}")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))


# ---------------------------------------------------------------------------
# bank-d.csv: a US checking account. ISO dates, Debit and Credit columns.
# ---------------------------------------------------------------------------

def bank_d(rng: random.Random) -> tuple[Book, list[dict[str, Any]]]:
    book = Book(date(2025, 8, 1), date(2026, 9, 30))
    s, e = book.start, book.end
    truth: list[dict[str, Any]] = []

    for d in month_days(s, e, 1):
        book.add(d, "ACH DEBIT OAKWOOD PROPERTY MGMT RENT", "2150.00")
    truth.append({"merchant": ["Oakwood Property Management", "Oakwood"],
                  "cadence": "monthly", "amount": "2150.00", "bucket": "other", "status": "active", "note": "rent"})
    for d in month_days(s, e, 12):
        book.add(d, "ONLINE PAYMENT QUILLMONT AUTO FINANCIAL SVCS", "412.37")
    truth.append({"merchant": ["Quillmont Auto Financial Services", "Quillmont Auto Financial", "Quillmont Auto", "Quillmont"], "cadence": "monthly",
                  "amount": "412.37", "bucket": "other", "status": "active", "note": "car loan"})
    for d in month_days(s, e, 3):
        book.add(d, "TRANSFER TO SAV XXXXXX4821", "500.00")
    truth.append({"merchant": ["Transfer to savings", "Savings", "Transfer"], "cadence": "monthly",
                  "amount": "500.00", "bucket": "other", "status": "active", "note": "transfer to own savings"})
    for d in month_days(s, e, 20):
        book.add(jitter(rng, d, 0.4, 3), "TVGANDE WEB ONLINE", money(rng, 85, 260))
    for d in month_days(s, e, 8):
        book.add(d, "ANYTIME FITNESS", "41.99")
    truth.append({"merchant": ["Anytime Fitness"], "cadence": "monthly", "amount": "41.99", "bucket": "subscription",
                  "status": "active"})
    for d in (date(2025, 8, 25), date(2025, 11, 25), date(2026, 2, 25), date(2026, 5, 25), date(2026, 8, 25)):
        book.add(d, "CITY OF ASHBURY WATER UTIL", "96.00")
    truth.append({"merchant": ["City of Ashbury", "City of Ashbury Water", "City of Ashbury Water Utility",
                               "City of Ashbury Water Utilities", "Ashbury Water"], "cadence": "quarterly",
                  "amount": "96.00", "bucket": "other", "status": "active", "note": "quarterly water bill"})
    for d in month_days(s, e, 16):
        book.add(d, "PRUDENTIAL INS PREM", "58.20")
    truth.append({"merchant": ["Prudential", "Prudential Insurance"], "cadence": "monthly", "amount": "58.20", "bucket": "other",
                  "status": "active", "note": "insurance"})
    for d in month_days(s, date(2026, 7, 31), 23):
        book.add(jitter(rng, d), "SXM*SIRIUSXM.COM/ACCT", "10.99")
    truth.append({"merchant": ["SiriusXM", "Sirius XM", "Sirius XM Radio"], "cadence": "monthly", "amount": "10.99",
                  "bucket": "subscription", "status": "missing", "note": "cancelled after July"})

    for d in every(rng, s, e, 4, 15):
        book.add(d, "ATM WITHDRAWAL 0123 ELM ST", rng.choice(["40.00", "60.00", "100.00", "200.00"]))
    for d in every(rng, s, e, 3, 9):
        book.add(d, "POS PURCHASE KROGER #456", money(rng, 20, 190))
    for d in every(rng, s, e, 9, 30):
        book.add(d, "VENMO PAYMENT 2125550177", money(rng, 15, 140))
    for d in month_days(s, e, 15) + month_days(s, e, 30):
        book.add(d, "PAYROLL DEPOSIT BLUEPEAK LOGISTICS", "-2875.00")
    return book, truth


def write_bank_d(book: Book, path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Date", "Description", "Debit", "Credit", "Balance"])
    balance = Decimal("4120.55")
    for day, description, amount, _ in book.sorted():
        balance -= amount
        writer.writerow([day.isoformat(), description, f"{amount:.2f}" if amount > 0 else "",
                         f"{-amount:.2f}" if amount < 0 else "", f"{balance:.2f}"])
    path.write_text(out.getvalue(), encoding="utf-8")


def _actual_changes(book: Book, truth: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A price change happens on the first charge at the new price, after any posting delay."""
    for entry in truth:
        match = entry.pop("match", None)
        if not match:
            continue
        for change in entry["changes"]:
            change[0] = min(r[0] for r in book.rows
                            if r[1].startswith(match) and r[2] == Decimal(change[2])).isoformat()
    return truth


def _last(book: Book) -> str:
    return max(r[0] for r in book.rows).isoformat()


SEED = 20261019


def main(out: Path = EXAMPLES, seed: int = SEED) -> None:
    """Write the four statements and their truth into ``out``. Another ``seed`` gives other
    dates, amounts and decoys around the same planted plans (scripts/measure_seeds.py)."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "truth").mkdir(exist_ok=True)
    statements = {}
    rng = random.Random(seed)
    book, truth = bank_a(rng)
    write_bank_a(book, out / "bank-a.csv")
    statements["bank-a.csv"] = {"as_of": _last(book), "series": _actual_changes(book, truth)}
    book, truth = card_b(rng)
    write_card_b(book, out / "card-b.csv")
    statements["card-b.csv"] = {"as_of": _last(book), "series": _actual_changes(book, truth)}
    book, truth = export_c(rng)
    write_export_c(book, out / "export-c.csv", rng)
    statements["export-c.csv"] = {"as_of": _last(book), "series": _actual_changes(book, truth)}
    book, truth = bank_d(rng)
    write_bank_d(book, out / "bank-d.csv")
    statements["bank-d.csv"] = {"as_of": _last(book), "series": _actual_changes(book, truth)}
    (out / "truth" / "statements.json").write_text(json.dumps(statements, indent=1, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")


if __name__ == "__main__":
    import sys
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES, int(sys.argv[2]) if len(sys.argv) > 2 else SEED)
