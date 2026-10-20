"""Reading statements: delimiters, headers, amounts, dates, signs, and refusing to guess."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ghostsubs.statement import StatementError, decode, parse_date, read_files, read_text


def amounts(text: str, **kwargs: str) -> list[Decimal]:
    return [t.amount for t in read_text(text, **kwargs).transactions]


def test_brazilian_semicolons_decimal_comma_and_balance_rows() -> None:
    text = ("Data;Histórico;Valor (R$)\n"
            "01/09/2025;SALDO ANTERIOR;3.215,40\n"
            "02/09/2025;NETFLIX.COM;-39,90\n"
            "03/09/2025;TED ALUGUEL;-2.350,00\n"
            "05/09/2025;CREDITO SALARIO;8.500,00\n"
            "15/09/2025;SALDO DO DIA;1.000,00\n"
            "20/09/2025;GLOBOPLAY;-22,90\n")
    statement = read_text(text)
    assert statement.delimiter == ";" and statement.decimal == "," and statement.date_order == "dmy"
    assert statement.currency == "R$" and statement.debits == "negative"
    assert [t.amount for t in statement.transactions] == [Decimal("39.90"), Decimal("2350.00"),
                                                          Decimal("-8500.00"), Decimal("22.90")]
    assert len(statement.skipped) == 2 and all("balance row" in s for s in statement.skipped)


def test_us_card_with_a_type_column() -> None:
    text = ("Transaction Date,Post Date,Description,Category,Type,Amount\n"
            "10/14/2026,10/15/2026,SPOTIFY USA,Shopping,Sale,-11.99\n"
            "10/13/2026,10/14/2026,\"AMAZON.COM, INC\",Shopping,Return,25.00\n"
            "10/12/2026,10/13/2026,AUTOPAY PAYMENT - THANK YOU,,Payment,\"1,200.00\"\n"
            "09/30/2026,10/01/2026,NETFLIX.COM,Shopping,Sale,-17.99\n")
    statement = read_text(text)
    assert statement.debits == "type column" and statement.date_order == "mdy"
    assert [(t.date, t.description, t.amount) for t in statement.transactions] == [
        (date(2026, 9, 30), "NETFLIX.COM", Decimal("17.99")),
        (date(2026, 10, 12), "AUTOPAY PAYMENT - THANK YOU", Decimal("-1200.00")),
        (date(2026, 10, 13), "AMAZON.COM, INC", Decimal("-25.00")),
        (date(2026, 10, 14), "SPOTIFY USA", Decimal("11.99")),
    ]


def test_messy_tab_export_with_preamble_total_and_windows_1252() -> None:
    lines = ["Extrato do cartao", "Periodo: 01/10/2025 a 31/10/2025", "",
             "data lançamento\tdescrição\tvalor",
             "03/10/2025\tDEEZER  PREMIUM \tR$ 22,90",
             "",
             "13/10/2025\tPAGAMENTO RECEBIDO\t-R$ 2.000,00",
             "14/10/2025\tESTORNO DECATHLON BR\t(R$ 79,90)",
             "15/10/2025\tASSAI ATACADISTA\tR$ 1.234,56",
             "16/10/2025\t99APP *99RIDE\tR$ 18,40",
             "\tTotal\tR$ -804,04"]
    raw = ("\r\n".join(lines) + "\r\n").encode("cp1252")
    statement = read_text(decode(raw))
    assert statement.delimiter == "\t" and statement.debits == "positive"
    assert [t.description for t in statement.transactions][0] == "DEEZER PREMIUM"
    assert [t.amount for t in statement.transactions] == [Decimal("22.90"), Decimal("-2000.00"),
                                                          Decimal("-79.90"), Decimal("1234.56"), Decimal("18.40")]
    assert any("no date" in s for s in statement.skipped)


def test_debit_and_credit_columns() -> None:
    text = ("Date,Description,Debit,Credit,Balance\n"
            "2025-08-01,ACH DEBIT OAKWOOD PROPERTY MGMT RENT,2150.00,,1970.55\n"
            "2025-08-15,PAYROLL DEPOSIT,,2875.00,4845.55\n"
            "2025-08-20,ANYTIME FITNESS,41.99,,4803.56\n")
    statement = read_text(text)
    assert statement.debits == "columns"
    assert [t.amount for t in statement.transactions] == [Decimal("2150.00"), Decimal("-2875.00"), Decimal("41.99")]


def test_english_synonyms_and_nubank_style_title_column() -> None:
    assert amounts("date,title,amount\n2026-10-01,Netflix.com,44.90\n2026-10-02,Pagamento recebido,-500.00\n"
                   "2026-10-03,Uber *Trip,12.40\n") == [Decimal("44.90"), Decimal("-500.00"), Decimal("12.40")]
    text = ("Posted Date,Payee,Withdrawal,Deposit\n"
            "2026-01-03,HULU,14.99,\n2026-01-04,PAYCHECK,,900.00\n2026-01-05,KROGER,40.10,\n")
    assert amounts(text) == [Decimal("14.99"), Decimal("-900.00"), Decimal("40.10")]


@pytest.mark.parametrize("cell, expected", [
    ("-R$ 39,90", Decimal("39.90")),
    ("R$ -39,90", Decimal("39.90")),
    ("(12,00)", Decimal("12.00")),
    ("12,00-", Decimal("12.00")),
    ("-1.234,56", Decimal("1234.56")),
])
def test_amount_spellings_with_a_decimal_comma(cell: str, expected: Decimal) -> None:
    text = f"data;descricao;valor\n21/10/2025;LOJA;{cell}\n22/10/2025;OUTRA;-10,50\n23/10/2025;MAIS;-1,99\n"
    assert amounts(text)[0] == expected


@pytest.mark.parametrize("cell, expected", [
    ("\"-1,234.56\"", Decimal("1234.56")),
    ("($12.00)", Decimal("12.00")),
    ("-$39.90", Decimal("39.90")),
])
def test_amount_spellings_with_a_decimal_point(cell: str, expected: Decimal) -> None:
    text = f"date,description,amount\n2026-10-01,SHOP,{cell}\n2026-10-02,OTHER,-10.50\n2026-10-03,MORE,-1.99\n"
    assert amounts(text)[0] == expected


def test_debit_and_credit_markers() -> None:
    text = "data;historico;valor\n01/10/2025;NETFLIX;39,90 D\n15/10/2025;SALARIO;8.500,00 C\n"
    statement = read_text(text)
    assert statement.debits == "marker"
    assert [t.amount for t in statement.transactions] == [Decimal("39.90"), Decimal("-8500.00")]


def test_ambiguous_day_month_order_is_refused() -> None:
    text = "date,description,amount\n01/02/2026,NETFLIX,-15.49\n03/04/2026,HULU,-14.99\n05/06/2026,MAX,-9.99\n"
    with pytest.raises(StatementError, match="--date-format"):
        read_text(text)
    forced = read_text(text, date_format="mdy")
    assert forced.transactions[0].date == date(2026, 1, 2)
    assert read_text(text, date_format="DD/MM/YYYY").transactions[0].date == date(2026, 2, 1)


def test_contradicting_dates_are_refused() -> None:
    text = "date,description,amount\n31/01/2026,A,-1.00\n01/31/2026,B,-2.00\n02/02/2026,C,-3.00\n"
    with pytest.raises(StatementError, match="disagree"):
        read_text(text)


def test_bad_date_format_flag() -> None:
    with pytest.raises(StatementError, match="dmy"):
        read_text("date,description,amount\n2026-01-01,A,-1.00\n", date_format="weekly")


@pytest.mark.parametrize("value, order, expected", [
    ("2026-10-19", "ymd", date(2026, 10, 19)),
    ("2026-10-19 14:22:00", "ymd", date(2026, 10, 19)),
    ("19/10/2026", "dmy", date(2026, 10, 19)),
    ("10/19/2026", "mdy", date(2026, 10, 19)),
    ("19.10.26", "dmy", date(2026, 10, 19)),
    ("31/02/2026", "dmy", None),
])
def test_parse_date(value: str, order: str, expected: date | None) -> None:
    assert parse_date(value, order) == expected


def test_sign_convention_refused_when_even_and_settled_by_flag() -> None:
    text = "date,description,amount\n2026-01-01,A,-1.00\n2026-01-02,B,2.00\n2026-01-03,C,-3.00\n2026-01-04,D,4.00\n"
    with pytest.raises(StatementError, match="--debits"):
        read_text(text)
    assert amounts(text, debits="positive") == [Decimal("-1.00"), Decimal("2.00"), Decimal("-3.00"), Decimal("4.00")]
    assert amounts(text, debits="negative") == [Decimal("1.00"), Decimal("-2.00"), Decimal("3.00"), Decimal("-4.00")]


def test_card_style_majority_is_detected() -> None:
    text = "date,description,amount\n2026-01-01,A,10.00\n2026-01-02,B,20.00\n2026-01-03,PAYMENT,-30.00\n"
    statement = read_text(text)
    assert statement.debits == "positive" and statement.transactions[2].amount == Decimal("-30.00")


def test_no_header_is_refused() -> None:
    with pytest.raises(StatementError, match="no header row"):
        read_text("foo,bar\n1,2\n")


def test_mostly_unreadable_rows_are_refused() -> None:
    rows = "".join(f"2026-01-{i:02d},X,n/a\n" for i in range(1, 15))
    with pytest.raises(StatementError, match="could not be read"):
        read_text("date,description,amount\n2026-01-20,OK,-1.00\n" + rows)


def test_several_files_count_an_overlapping_row_once(tmp_path: Path) -> None:
    september = tmp_path / "sep.csv"
    october = tmp_path / "oct.csv"
    september.write_text("date,description,amount\n2026-09-05,NETFLIX,-15.49\n2026-09-30,COFFEE,-4.75\n"
                         "2026-09-30,COFFEE,-4.75\n", encoding="utf-8")
    october.write_text("date,description,amount\n2026-09-30,COFFEE,-4.75\n2026-10-05,NETFLIX,-15.49\n",
                       encoding="utf-8")
    merged = read_files([september, october])
    assert len(merged.transactions) == 4 and merged.duplicates == 1
    assert [t.description for t in merged.transactions].count("COFFEE") == 2


def test_unreadable_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(StatementError, match="can't read"):
        read_files([tmp_path / "missing.csv"])


def test_decode_falls_back_to_windows_1252() -> None:
    assert decode("descrição".encode("cp1252")) == "descrição"
    assert decode("﻿Data".encode("utf-8")) == "Data"


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_a_type_column_with_accents() -> None:
    statement = read_files([FIXTURES / "tipo_accent.csv"])
    assert statement.debits == "type column"
    salary = [t for t in statement.transactions if t.description == "SALARIO EMPRESA X"]
    netflix = [t for t in statement.transactions if t.description == "NETFLIX.COM"]
    assert salary and all(t.amount < 0 for t in salary) and netflix and all(t.amount > 0 for t in netflix)


def test_a_sign_vote_that_puts_money_in_on_the_charge_side_is_refused() -> None:
    with pytest.raises(StatementError, match="--debits negative"):
        read_files([FIXTURES / "credits_70.csv"])
    statement = read_files([FIXTURES / "credits_70.csv"], debits="negative")
    assert all(t.amount < 0 for t in statement.transactions if "RECEBIDO" in t.description)
    assert statement.debits_from == "--debits"


def test_only_credits_carry_a_marker() -> None:
    statement = read_files([FIXTURES / "marker_partial.csv"])
    assert statement.debits == "marker"
    assert all(t.amount > 0 for t in statement.transactions if t.description == "NETFLIX.COM")
    assert all(t.amount < 0 for t in statement.transactions if t.description == "SALARIO EMPRESA X")


def test_the_sign_vote_is_recorded() -> None:
    text = "date,description,amount\n2026-01-01,SHOP,10.00\n2026-01-02,CAFE,20.00\n2026-01-03,PAYMENT,-30.00\n"
    assert read_text(text).debits_from == "the sign of most rows"
