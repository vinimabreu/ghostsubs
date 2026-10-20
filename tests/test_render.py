"""The line, the report and the JSON say only what code computed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from conftest import monthly, statement_of, tx

from ghostsubs.clean import identity
from ghostsubs.naming import Name
from ghostsubs.recur import analyze
from ghostsubs.render import as_json, line, money, report


def named(raw: str, merchant: str, kind: str = "subscription", verified: bool = True) -> Name:
    return Name(raw, merchant, identity(merchant), verified, kind, raw if verified else "",
                "" if verified else "the quote is not in the descriptor", "model",
                model_said="" if verified else "Wrong Name")


def test_money_in_both_conventions() -> None:
    assert money(Decimal("1234.5"), "R$", ",") == "R$ 1.234,50"
    assert money(Decimal("1234.5"), "$", ".") == "$1,234.50"
    assert money(Decimal("-39.9"), "", ".") == "-39.90"
    assert money(Decimal("0.005"), "€", ".") == "€0.01"


def example():
    charges = (monthly("NETFLIX.COM", "15.49", "2025-08-21", 13) + monthly("NETFLIX.COM", "17.99", "2026-09-21", 1)
               + [tx("2024-10-23", "ADOBE *CC", "239.88"), tx("2025-10-23", "ADOBE *CC", "239.88")]
               + monthly("HELP.MAX.COM", "15.99", "2024-10-15", 23)
               + [tx("2026-06-28", "Audible*X1", "0.99")] + monthly("Audible*X1", "14.95", "2026-07-28", 3)
               + monthly("RENT", "2150.00", "2024-10-01", 25))
    names = {"NETFLIX.COM": named("NETFLIX.COM", "Netflix"), "ADOBE *CC": named("ADOBE *CC", "Adobe"),
             "HELP.MAX.COM": named("HELP.MAX.COM", "Max"), "Audible*X1": named("Audible*X1", "Audible"),
             "RENT": named("RENT", "Rent", "loan_or_rent", verified=False)}
    return analyze(statement_of(charges, start="2024-10-01"), names, today=date(2026, 10, 14), model="gemma4:12b")


def test_the_line() -> None:
    assert line(example()) == (
        "3 subscriptions, $52.93/mo (other recurring: $2,150.00/mo). Netflix went up 16% ($15.49 -> $17.99). "
        "Adobe renews in 9 days: $239.88. New: Audible (trial $0.99, now $14.95 monthly). "
        "No charge from Max since 2026-08-15 (cancelled?).")


def test_nothing_found() -> None:
    analysis = analyze(statement_of([tx("2026-01-01", "SHOP", "10.00")]), {}, model="m")
    assert line(analysis).startswith("No recurring charges found in 1 charge")


def test_without_a_model_the_line_does_not_split_by_kind() -> None:
    analysis = example()
    analysis.model = ""
    assert line(analysis).startswith("4 recurring charges, $2,202.93/mo.")


def test_the_report_shows_evidence_charges_and_unverified_names() -> None:
    text = report(example())
    assert 'NETFLIX.COM  <- "NETFLIX.COM"' in text
    assert "went up 16% on 2026-09-21: $15.49 -> $17.99" in text
    assert "trial: $0.99 on 2026-06-28, then $14.95 from 2026-07-28" in text
    assert "UNVERIFIED: the quote is not in the descriptor" in text
    assert "STOPPED" in text and "2026-08-15 $15.99" in text


def test_json_carries_every_number() -> None:
    data = as_json(example())
    netflix = next(s for s in data["series"] if s["merchant"] == "Netflix")
    assert netflix["changes"] == [{"date": "2026-09-21", "from": 15.49, "to": 17.99, "percent": 16}]
    assert netflix["counted_as"] == "subscription" and len(netflix["charges"]) == 14
    assert data["subscriptions_monthly"] == 52.93 and data["other_recurring_monthly"] == 2150.0
    assert data["unverified"][0]["model_said"] == "Wrong Name"


def test_a_plan_every_four_weeks_and_a_double_charge_in_the_report() -> None:
    from datetime import timedelta
    charges = [tx(date(2025, 10, 3) + timedelta(days=28 * k), "CHEWY AUTOSHIP", "42.99", k) for k in range(13)]
    charges.append(tx("2026-01-24", "CHEWY AUTOSHIP", "42.99", 99))
    names = {"CHEWY AUTOSHIP": named("CHEWY AUTOSHIP", "Chewy")}
    analysis = analyze(statement_of(charges), names, today=date(2026, 9, 20), model="m")
    text = report(analysis)
    assert "every 28 days" in text and "charged twice? 2026-01-2" in text
    data = as_json(analysis)
    assert data["series"][0]["every_days"] == 28 and data["series"][0]["duplicates"][0]["date"] in ("2026-01-23",
                                                                                                   "2026-01-24")
