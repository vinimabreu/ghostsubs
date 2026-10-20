"""Recurrence is plain code: cadence, price changes, trials, renewals, stops, and the decoys."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from conftest import monthly, statement_of, tx

from ghostsubs.naming import Name
from ghostsubs.recur import Analysis, analyze
from ghostsubs.statement import Transaction


def named(raw: str, merchant: str, kind: str = "subscription") -> Name:
    from ghostsubs.clean import identity
    return Name(raw, merchant, identity(merchant), True, kind, raw, "", "model")


def run(charges: list[Transaction], names: dict[str, Name] | None = None, *, start: str | None = None,
        today: str | None = None) -> Analysis:
    names = names or {}
    for charge in charges:
        names.setdefault(charge.description, named(charge.description, charge.description.title()))
    statement = statement_of(charges, start=start)
    return analyze(statement, names, today=date.fromisoformat(today) if today else None, model="fake")


def only(analysis: Analysis):
    assert len(analysis.series) == 1, [(s.merchant, s.cadence.name, len(s.charges)) for s in analysis.series]
    return analysis.series[0]


def test_three_monthly_charges_are_a_subscription_two_are_not() -> None:
    assert only(run(monthly("NETFLIX", "15.49", "2026-01-05", 3))).cadence.name == "monthly"
    assert run(monthly("NETFLIX", "15.49", "2026-01-05", 2)).series == []


def test_weekly_needs_six() -> None:
    weekly = [tx(date(2026, 3, 2) + timedelta(days=7 * i), "HELLOFRESH", "69.99", i) for i in range(6)]
    series = only(run(weekly))
    assert series.cadence.name == "weekly" and series.monthly == Decimal("303.29")
    assert run(weekly[:5]).series == []


def test_quarterly_and_yearly() -> None:
    quarterly = [tx(d, "CITY WATER", "96.00", i) for i, d in
                 enumerate([date(2025, 8, 25), date(2025, 11, 25), date(2026, 2, 25)])]
    series = only(run(quarterly, today="2026-03-01"))
    assert series.cadence.name == "quarterly" and series.monthly == Decimal("32.00")
    yearly = [tx("2024-10-23", "ADOBE", "239.88", 1), tx("2025-10-23", "ADOBE", "239.88", 2)]
    series = only(run(yearly, today="2026-10-14"))
    assert series.cadence.name == "yearly" and series.monthly == Decimal("19.99")
    assert series.next_expected == date(2026, 10, 23) and series.renews_in == 9 and not series.missing
    assert run(yearly[:1]).series == []


def test_price_increase_with_date_amounts_and_percent() -> None:
    charges = monthly("NETFLIX", "39.90", "2025-09-05", 12) + monthly("NETFLIX", "44.90", "2026-09-05", 2)
    series = only(run(charges))
    change = series.changes[0]
    assert (change.date, change.old, change.new, change.percent) == (date(2026, 9, 5), Decimal("39.90"),
                                                                    Decimal("44.90"), Decimal("13"))
    assert series.amount == Decimal("44.90")


def test_a_new_price_seen_once_counts_after_the_old_one_recurred() -> None:
    charges = monthly("HULU", "14.99", "2026-01-09", 3) + [tx("2026-04-09", "HULU", "17.99", 9)]
    assert only(run(charges)).changes[0].new == Decimal("17.99")
    random_shop = [tx("2026-07-16", "PHARMACY", "76.02"), tx("2026-08-13", "PHARMACY", "78.51"),
                   tx("2026-09-09", "PHARMACY", "127.70")]
    assert run(random_shop).series == []


def test_price_decrease() -> None:
    charges = monthly("GYM", "129.90", "2025-01-07", 6) + monthly("GYM", "99.90", "2025-07-07", 6)
    change = only(run(charges)).changes[0]
    assert change.new < change.old and change.percent == Decimal("-23")


def test_amounts_within_the_tolerance_are_one_price() -> None:
    wobble = ["21.90", "22.31", "21.47", "22.05", "21.70", "22.40"]
    charges = [tx(date(2026, 1, 12) + timedelta(days=30 * i), "SPOTIFY", amount, i) for i, amount in enumerate(wobble)]
    assert only(run(charges)).changes == []


def test_trial_then_paid_and_new() -> None:
    charges = ([tx("2026-06-17", "HBOMAX", "1.99", 0)] + monthly("HBOMAX", "34.90", "2026-07-17", 3))
    series = only(run(charges, start="2025-09-01", today="2026-10-16"))
    assert series.trial is not None and series.trial.amount == Decimal("1.99")
    assert series.new and series.renews_in == 1


def test_not_new_when_the_statement_starts_right_before_it() -> None:
    series = only(run(monthly("NETFLIX", "15.49", "2026-08-05", 3), today="2026-10-16"))
    assert not series.new


def test_missing_charge_means_stopped() -> None:
    charges = monthly("GLOBOPLAY", "22.90", "2025-09-08", 12)
    series = only(run(charges, today="2026-10-16"))
    assert series.missing and series.next_expected == date(2026, 9, 8)
    assert not only(run(charges, today="2026-09-14")).missing


def test_one_missed_month_inside_still_counts() -> None:
    charges = monthly("STRAVA", "11.99", "2026-01-03", 3) + monthly("STRAVA", "11.99", "2026-05-03", 3)
    series = only(run(charges))
    assert len(series.charges) == 6


def test_weekly_groceries_with_moving_amounts_are_not_a_subscription() -> None:
    rng = random.Random(7)
    charges = [tx(date(2025, 9, 6) + timedelta(days=7 * i), "SUPERMERCADO", f"{rng.uniform(60, 380):.2f}", i)
               for i in range(56)]
    assert run(charges).series == []


def test_frequent_rides_are_not_a_subscription() -> None:
    rng = random.Random(3)
    day, charges = date(2025, 9, 1), []
    for i in range(160):
        day += timedelta(days=rng.randint(1, 5))
        charges.append(tx(day, "UBER *TRIP", f"{rng.uniform(9, 58):.2f}", i))
    assert run(charges, {c.description: named(c.description, "Uber", "other") for c in charges}).series == []


def test_a_coffee_at_the_same_price_every_few_days_is_not_a_subscription() -> None:
    rng = random.Random(5)
    day, charges = date(2025, 1, 1), []
    for i in range(200):
        day += timedelta(days=rng.randint(1, 4))
        charges.append(tx(day, "SQ *CAFE", rng.choice(["4.75", "4.75", "4.75", "6.25"]), i))
    assert run(charges).series == []


def test_a_membership_among_purchases_at_the_same_store() -> None:
    rng = random.Random(11)
    day, charges = date(2024, 8, 1), []
    for i in range(110):
        day += timedelta(days=rng.randint(5, 12))
        charges.append(tx(day, "COSTCO WHSE", f"{rng.uniform(38, 310):.2f}", i))
    charges += [tx("2024-11-20", "COSTCO *ANNUAL", "65.00", 900), tx("2025-11-20", "COSTCO *ANNUAL", "65.00", 901)]
    names = {c.description: named(c.description, "Costco", "other") for c in charges}
    series = only(run(charges, names, today="2026-10-14"))
    assert series.cadence.name == "yearly" and series.amount == Decimal("65.00") and series.pass_name == "exact"


def test_a_ride_pass_among_rides() -> None:
    rng = random.Random(2)
    day, charges = date(2025, 9, 1), []
    for i in range(140):
        day += timedelta(days=rng.randint(1, 5))
        charges.append(tx(day, "UBER *TRIP", f"{rng.uniform(9, 58):.2f}", i))
    charges += monthly("UBER *ONE", "9.90", "2025-09-26", 12)
    names = {c.description: named(c.description, "Uber") for c in charges}
    series = only(run(charges, names))
    assert series.amount == Decimal("9.90") and len(series.charges) == 12


def test_a_one_off_purchase_on_the_subscription_descriptor_is_left_out() -> None:
    charges = monthly("APPLE.COM/BILL", "2.99", "2025-01-14", 12) + [tx("2025-06-02", "APPLE.COM/BILL", "9.99", 99)]
    series = only(run(charges))
    assert len(series.charges) == 12 and all(c.amount == Decimal("2.99") for c in series.charges)


def test_a_monthly_bill_whose_amount_moves_is_listed_apart() -> None:
    rng = random.Random(9)
    charges = [tx(d, "LUMEVALE", f"{rng.uniform(180, 340):.2f}", i)
               for i, d in enumerate(c.date for c in monthly("X", "1", "2025-09-22", 13))]
    analysis = run(charges, {c.description: named(c.description, "Lumevale", "bill") for c in charges})
    assert analysis.series == [] and len(analysis.varying) == 1 and analysis.varying[0].cadence.name == "monthly"


def test_the_tail_of_a_moving_bill_is_not_a_plan() -> None:
    amounts = ["190.10", "260.40", "301.22", "215.75", "280.00", "199.95", "240.13", "120.00", "121.50", "119.80"]
    charges = [tx(c.date, "POWERCO", amounts[i], i) for i, c in enumerate(monthly("X", "1", "2025-01-19", 10))]
    assert run(charges).series == []


def test_two_pieces_of_one_plan_are_joined() -> None:
    charges = monthly("PLANET FIT*CLUB", "24.99", "2024-08-17", 26)
    charges += [tx("2025-03-17", "PLANET FIT*ANNUAL", "49.00", 800), tx("2026-03-17", "PLANET FIT*ANNUAL", "49.00", 801)]
    names = {c.description: named(c.description, "Planet Fitness") for c in charges}
    analysis = run(charges, names, today="2026-10-14")
    cadences = sorted((s.cadence.name, len(s.charges), s.missing) for s in analysis.series)
    assert cadences == [("monthly", 26, False), ("yearly", 2, False)]


def test_kind_only_picks_the_total() -> None:
    charges = monthly("RENT", "2350.00", "2026-01-10", 4) + monthly("SPOTIFY", "21.90", "2026-01-12", 4)
    names = {"RENT": named("RENT", "Horizonte", "loan_or_rent"), "SPOTIFY": named("SPOTIFY", "Spotify")}
    analysis = run(charges, names)
    assert [s.merchant for s in analysis.subscriptions] == ["Spotify"]
    assert [s.merchant for s in analysis.other_recurring] == ["Horizonte"]
    rent = analysis.other_recurring[0]
    again = run(charges, {"RENT": named("RENT", "Horizonte", "subscription"), "SPOTIFY": named("SPOTIFY", "Spotify")})
    same = [s for s in again.series if s.merchant == "Horizonte"][0]
    assert (same.cadence, same.amount, same.monthly, same.next_expected) == (rent.cadence, rent.amount, rent.monthly,
                                                                           rent.next_expected)


def test_variants_named_the_same_merge_into_one_series() -> None:
    charges = (monthly("PAYPAL *SPOTIFY 4025550134", "21.90", "2025-09-12", 4)
               + monthly("EBN*SPOTIFY", "21.90", "2026-01-12", 5) + monthly("SPOTIFY AB", "21.90", "2026-06-12", 5))
    names = {c.description: named(c.description, "Spotify") for c in charges}
    series = only(run(charges, names))
    assert len(series.charges) == 14 and not series.missing


def test_a_refund_is_matched_to_its_charge() -> None:
    charges = [tx("2026-03-22", "AMAZON BR", "168.07", 1), tx("2026-04-02", "ESTORNO AMAZON BR", "-168.07", 2)]
    analysis = run(charges, {"AMAZON BR": named("AMAZON BR", "Amazon", "other")})
    assert len(analysis.refunds) == 1 and analysis.refunds[0].charge.date == date(2026, 3, 22)


def test_charges_after_today_are_ignored() -> None:
    charges = monthly("NETFLIX", "15.49", "2026-01-05", 6)
    series = only(run(charges, today="2026-04-01"))
    assert len(series.charges) == 3


def _decoys(seed: int) -> tuple[list[Transaction], dict[str, Name]]:
    """Two years of merchants paid often at moving amounts, plus cash at round amounts."""
    rng = random.Random(seed)
    rows: list[Transaction] = []
    specs = [("UBER *TRIP", "Uber", 1, 5, 9, 58), ("LYFT *RIDE", "Lyft", 2, 7, 8, 46),
             ("TST* LUCKY PIZZA", "Lucky Pizza", 10, 40, 14, 85), ("ATM WITHDRAWAL 0123 ELM ST", "ATM", 4, 15, 0, 0),
             ("SQ *CORNER BEAN CAFE", "Corner Bean Cafe", 1, 4, 3, 7)]
    for raw, _, low_gap, high_gap, low, high in specs:
        day = date(2024, 9, 1) + timedelta(days=rng.randint(0, high_gap))
        while day <= date(2026, 9, 30):
            amount = rng.choice(["40.00", "60.00", "100.00", "200.00"]) if high == 0 else f"{rng.uniform(low, high):.2f}"
            rows.append(tx(day, raw, amount, len(rows)))
            day += timedelta(days=rng.randint(low_gap, high_gap))
    return rows, {raw: named(raw, name, "other") for raw, name, *_ in specs}


def test_frequent_merchants_and_cash_never_form_a_series_across_seeds() -> None:
    for seed in range(25):
        rows, names = _decoys(seed)
        analysis = analyze(statement_of(rows), names, model="fake", now=date(2026, 10, 1))
        assert analysis.series == [], (seed, [(s.merchant, s.cadence.name, str(s.amount)) for s in analysis.series])


def test_a_ride_that_cost_the_same_a_year_apart_is_not_a_yearly_plan() -> None:
    rng = random.Random(4)
    day, charges = date(2024, 9, 1), []
    for i in range(300):
        day += timedelta(days=rng.randint(1, 5))
        charges.append(tx(day, "UBER *TRIP", f"{rng.uniform(9, 58):.2f}", i))
    charges += [tx("2024-11-03", "UBER *TRIP", "23.45", 900), tx("2025-11-03", "UBER *TRIP", "23.45", 901)]
    names = {"UBER *TRIP": named("UBER *TRIP", "Uber", "other")}
    assert run(charges, names, today="2026-10-01").series == []


def test_pieces_far_apart_in_price_are_not_joined() -> None:
    rng = random.Random(8)
    day, charges = date(2025, 1, 1), []
    for i in range(120):
        day += timedelta(days=rng.randint(1, 6))
        charges.append(tx(day, "UBER *TRIP", f"{rng.uniform(9, 58):.2f}", i))
    charges += monthly("UBER *ONE", "9.73", "2025-01-26", 4) + monthly("UBER *ONE", "33.54", "2025-05-26", 4)
    names = {c.description: named(c.description, "Uber") for c in charges}
    analysis = run(charges, names, today="2025-09-30")
    assert all(not s.changes for s in analysis.series)


def test_a_price_change_inside_a_ride_pass_is_joined() -> None:
    rng = random.Random(8)
    day, charges = date(2025, 1, 1), []
    for i in range(120):
        day += timedelta(days=rng.randint(1, 6))
        charges.append(tx(day, "UBER *TRIP", f"{rng.uniform(9, 58):.2f}", i))
    charges += monthly("UBER *ONE", "9.99", "2025-01-26", 5) + monthly("UBER *ONE", "12.99", "2025-06-26", 3)
    names = {c.description: named(c.description, "Uber") for c in charges}
    series = only(run(charges, names, today="2025-09-30"))
    assert series.changes[0].old == Decimal("9.99") and series.changes[0].new == Decimal("12.99")


def test_purchases_about_a_month_apart_that_drift_are_not_a_plan() -> None:
    days = ["2026-01-02", "2026-01-28", "2026-03-04", "2026-03-31", "2026-05-06"]
    assert run([tx(d, "FLORIST", "45.00", i) for i, d in enumerate(days)]).series == []


def test_a_plan_billed_every_four_weeks() -> None:
    charges = [tx(date(2025, 10, 3) + timedelta(days=28 * k), "CHEWY AUTOSHIP", "42.99", k) for k in range(13)]
    series = only(run(charges, today="2026-09-20"))
    assert series.gap_days == 28 and series.next_expected == charges[-1].date + timedelta(days=28)
    assert series.monthly == Decimal("46.74")  # 42.99 x 30.44 / 28


def test_a_plan_billed_every_30_days_for_two_years() -> None:
    charges = [tx(date(2024, 10, 1) + timedelta(days=30 * k), "STREAMBOX", "9.99", k) for k in range(25)]
    assert only(run(charges, today="2026-09-30")).gap_days == 30


def test_a_plan_charged_twice_once_is_still_the_plan() -> None:
    charges = monthly("NETFLIX.COM", "15.49", "2025-10-05", 12)
    charges += [tx("2026-02-06", "NETFLIX.COM", "15.49", 90), tx("2026-06-05", "NETFLIX.COM", "15.49", 91)]
    series = only(run(charges, today="2026-10-10"))
    assert len(series.charges) == 12 and [c.date for c in series.duplicates] == [date(2026, 2, 6), date(2026, 6, 5)]


def test_the_exact_pass_only_looks_at_the_plans_own_descriptor() -> None:
    rng = random.Random(6)
    day, charges = date(2025, 9, 1), []
    for i in range(110):
        day += timedelta(days=rng.randint(1, 6))
        charges.append(tx(day, f"AMZN Mktp US*{i:06d}", f"{rng.uniform(5, 120):.2f}", i))
    charges.append(tx("2025-10-20", "AMZN Mktp US*FFFF01", "14.99", 500))
    charges += [tx(c.date, f"Amazon Prime*PR{k:04d}", "14.99", 600 + k)
                for k, c in enumerate(monthly("X", "1", "2025-09-09", 13))]
    names = {c.description: named(c.description, "Amazon") for c in charges}
    series = only(run(charges, names, today="2026-10-01"))
    assert series.amount == Decimal("14.99") and len(series.charges) == 13


def test_rows_booked_ahead_do_not_move_the_day_counted_from() -> None:
    charges = monthly("NETFLIX", "15.49", "2026-01-05", 10) + [tx("2026-12-16", "LOJA PARCELA 03/04", "250.00", 99)]
    names = {c.description: named(c.description, c.description.title()) for c in charges}
    analysis = analyze(statement_of(charges), names, model="fake", now=date(2026, 10, 19))
    assert analysis.as_of == date(2026, 10, 5) and analysis.future_rows == 1
    assert not only(analysis).missing


def test_a_trial_that_just_turned_paid_is_flagged_early() -> None:
    charges = [tx("2026-06-17", "DM *HBOMAX", "1.99", 0), tx("2026-07-17", "DM *HBOMAX", "34.90", 1)]
    analysis = run(charges, start="2025-09-01", today="2026-07-20")
    assert analysis.series == [] and [t[1].amount for t in analysis.possible_trials] == [Decimal("1.99")]
    second = charges + [tx("2026-08-17", "DM *HBOMAX", "34.90", 2)]
    assert len(run(second, start="2025-09-01", today="2026-08-20").possible_trials) == 1
    third = second + [tx("2026-09-17", "DM *HBOMAX", "34.90", 3)]
    analysis = run(third, start="2025-09-01", today="2026-09-20")
    assert analysis.possible_trials == [] and only(analysis).trial is not None


def test_no_early_trial_at_the_start_of_the_statement_or_for_an_old_pair() -> None:
    charges = [tx("2026-06-17", "SHOP", "1.99", 0), tx("2026-07-17", "SHOP", "34.90", 1)]
    assert run(charges, today="2026-07-20").possible_trials == []
    assert run(charges, start="2025-09-01", today="2026-10-20").possible_trials == []


def test_two_pairs_of_similar_food_orders_are_not_a_plan_with_a_price_change() -> None:
    """Found in another seed of the examples: orders at one restaurant that pair up by chance."""
    rows = [("2025-10-24", "31.46"), ("2025-11-01", "61.04"), ("2025-11-08", "69.22"), ("2025-11-27", "76.68"),
            ("2025-12-29", "47.28"), ("2026-02-23", "69.99"), ("2026-03-13", "52.00"), ("2026-03-23", "68.25"),
            ("2026-04-19", "83.94"), ("2026-06-19", "83.86"), ("2026-07-03", "69.46"), ("2026-07-09", "83.73"),
            ("2026-09-25", "48.71")]
    charges = [tx(d, "IFD*ACAI DO PORTO", a, i) for i, (d, a) in enumerate(rows)]
    assert run(charges, today="2026-10-16").series == []


def test_a_rare_merchant_that_cost_about_the_same_a_year_apart_is_not_yearly() -> None:
    """Also from another seed: twelve pizzas in two years, two of them 5 cents apart, 358 days apart."""
    rows = [("2024-09-21", "23.67"), ("2024-10-18", "66.99"), ("2024-11-29", "17.46"), ("2024-12-19", "37.53"),
            ("2025-04-29", "26.49"), ("2025-05-30", "82.33"), ("2025-07-05", "30.83"), ("2025-11-26", "62.36"),
            ("2026-06-28", "30.88"), ("2026-08-12", "57.15"), ("2026-08-27", "67.29"), ("2026-10-01", "47.57")]
    charges = [tx(d, "TST* LUCKY PIZZA", a, i) for i, (d, a) in enumerate(rows)]
    assert run(charges, today="2026-10-14").series == []


def test_the_early_trial_flag_needs_a_subscription_label_when_there_is_a_model() -> None:
    charges = [tx("2026-09-20", "SQ *NEW TAQUERIA", "4.50", 0), tx("2026-10-08", "SQ *NEW TAQUERIA", "38.00", 1)]
    shop = {"SQ *NEW TAQUERIA": named("SQ *NEW TAQUERIA", "New Taqueria", "other")}
    assert run(charges, shop, start="2025-09-01", today="2026-10-15").possible_trials == []
    unlabelled = {"SQ *NEW TAQUERIA": Name("SQ *NEW TAQUERIA", "New Taqueria", "newtaqueria", False, "unknown", "",
                                           "no model", "cleanup")}
    statement = statement_of(charges, start="2025-09-01")
    analysis = analyze(statement, unlabelled, today=date(2026, 10, 15), model="")
    assert len(analysis.possible_trials) == 1
