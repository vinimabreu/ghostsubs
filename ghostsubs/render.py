"""The one line, the report and the JSON. Every number here comes from the statement and recur.py."""

from __future__ import annotations

import textwrap
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .naming import Name
from .recur import CENT, RECENT_DAYS, Analysis, Series, Varying


def money(amount: Decimal, currency: str = "", mark: str = ".") -> str:
    """``R$ 1.234,56`` for a file written with a decimal comma, ``$1,234.56`` otherwise."""
    value = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    whole, cents = f"{abs(value):.2f}".split(".")
    grouped = f"{int(whole):,}"
    text = grouped.replace(",", ".") + "," + cents if mark == "," else grouped + "." + cents
    if not currency:
        return sign + text
    return f"{sign}{currency}{' ' if len(currency) > 1 else ''}{text}"


def _money(analysis: Analysis, amount: Decimal) -> str:
    return money(amount, analysis.statement.currency, analysis.statement.decimal)


def _days(n: int) -> str:
    if n == 0:
        return "today"
    if n < 0:
        return f"{-n} day{'s' if n != -1 else ''} late"
    return f"in {n} day{'s' if n != 1 else ''}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _change_text(analysis: Analysis, series: Series, index: int = -1) -> str:
    change = series.changes[index]
    verb = "went up" if change.new > change.old else "went down"
    return (f"{series.merchant} {verb} {abs(change.percent)}% ({_money(analysis, change.old)} -> "
            f"{_money(analysis, change.new)})")


def line(analysis: Analysis) -> str:
    """One line: the monthly total, then only what changed or needs a decision soon."""
    statement = analysis.statement
    if not analysis.series:
        text = (f"No recurring charges found in {_plural(len(statement.charges), 'charge')} "
                f"({statement.first} to {statement.last}).")
        covered = (statement.last - statement.first).days if statement.first and statement.last else 0
        if covered < 90:
            text += (f" A monthly plan needs 3 charges and this covers {_plural(covered, 'day')}: export more "
                     "months, or keep adding each month's file to the folder.")
        return text + _trial_sentence(analysis)
    subscriptions, others = analysis.subscriptions, analysis.other_recurring
    total = sum((s.monthly for s in subscriptions), Decimal(0))
    if not analysis.model:
        everything = sum((s.monthly for s in analysis.active), Decimal(0))
        head = f"{_plural(len(analysis.active), 'recurring charge')}, {_money(analysis, everything)}/mo"
        others = []
    else:
        head = f"{_plural(len(subscriptions), 'subscription')}, {_money(analysis, total)}/mo"
    if others:
        head += f" (other recurring: {_money(analysis, sum((s.monthly for s in others), Decimal(0)))}/mo)"
    sentences = [head]

    changed = []
    for series in analysis.active:
        for index, change in enumerate(series.changes):
            if 0 <= (analysis.as_of - change.date).days <= RECENT_DAYS:
                changed.append((change.date, _change_text(analysis, series, index)))
    if changed:
        sentences.append(", ".join(text for _, text in sorted(changed, reverse=True)))

    renewals = [s for s in analysis.active if s.cadence.months >= 3 and 0 <= s.renews_in <= analysis.window]
    for series in sorted(renewals, key=lambda s: s.renews_in):
        sentences.append(f"{series.merchant} renews {_days(series.renews_in)}: {_money(analysis, series.amount)}")

    new = [s for s in analysis.active if s.new]
    if new:
        items = []
        for series in new:
            detail = f"{_money(analysis, series.amount)} {series.schedule}"
            if series.trial is not None:
                detail = f"trial {_money(analysis, series.trial.amount)}, now {detail}"
            items.append(f"{series.merchant} ({detail})")
        sentences.append("New: " + ", ".join(items))

    stopped = [s for s in analysis.stopped
               if s.next_expected and (analysis.as_of - s.next_expected).days <= RECENT_DAYS]
    for series in sorted(stopped, key=lambda s: s.last, reverse=True):
        sentences.append(f"No charge from {series.merchant} since {series.last} (cancelled?)")
    return ". ".join(sentences) + "." + _trial_sentence(analysis)


def _trial_sentence(analysis: Analysis) -> str:
    if not analysis.possible_trials:
        return ""
    items = [f"{name} ({_money(analysis, trial.amount)}, then {_money(analysis, full.amount)} on {full.date})"
             for name, trial, full in analysis.possible_trials]
    return " Possible trial turned paid: " + ", ".join(items) + "."


def _wrap(text: str, indent: str, width: int = 100) -> list[str]:
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent,
                         break_on_hyphens=False) or [indent]


def _names_lines(series_names: list[Name]) -> list[str]:
    out = []
    for name in series_names:
        if name.verified:
            out.append(f'      {name.raw}  <- "{name.evidence}"')
        else:
            said = f' (model said "{name.model_said}")' if name.model_said else ""
            out.append(f"      {name.raw}  <- UNVERIFIED: {name.reason}{said}")
    return out


def _series_block(analysis: Analysis, series: Series) -> list[str]:
    cadence = series.schedule
    head = (f"  {series.merchant:<24} {cadence:<13} {_money(analysis, series.amount):>12}"
            f"  {_money(analysis, series.monthly):>12}/mo")
    if series.missing:
        head += f"  expected {series.next_expected}, not charged"
    else:
        head += f"  next {series.next_expected} ({_days(series.renews_in)})"
    lines = [head]
    label = {"subscription": "subscription", "bill": "bill", "loan_or_rent": "rent or loan", "other": "other",
             "unknown": "kind unknown (name unverified)"}[series.kind]
    notes = [label]
    if series.new:
        notes.append("new: became recurring in the last month")
    if series.pass_name == "exact":
        notes.append("found by its exact amount among other charges at this merchant")
    lines.append("      " + " · ".join(notes))
    if series.trial is not None:
        lines.append(f"      trial: {_money(analysis, series.trial.amount)} on {series.trial.date}, then "
                     f"{_money(analysis, series.charges[0].amount)} from {series.charges[0].date}")
    for charge in series.duplicates:
        lines.append(f"      charged twice? {charge.date} {_money(analysis, charge.amount)} repeats a charge "
                     "a few days before or after it, and is left out")
    for index, change in enumerate(series.changes):
        verb = "went up" if change.new > change.old else "went down"
        lines.append(f"      {verb} {abs(change.percent)}% on {change.date}: {_money(analysis, change.old)} -> "
                     f"{_money(analysis, change.new)}")
    seen: dict[str, Name] = {}
    for charge in series.charges + ([series.trial] if series.trial else []):
        name = analysis.names.get(charge.description)
        if name is not None:
            seen.setdefault(charge.description, name)
    lines.append("      named from:")
    lines.extend(_names_lines(list(seen.values())))
    charges = " · ".join(f"{c.date} {_money(analysis, c.amount)}" for c in series.charges)
    lines.extend(_wrap(f"{len(series.charges)} charges: {charges}", "      "))
    return lines


def _varying_block(analysis: Analysis, item: Varying) -> list[str]:
    span = f"{_money(analysis, item.low)} to {_money(analysis, item.high)}"
    lines = [f"  {item.merchant:<24} {item.cadence.name:<13} {span}"]
    charges = " · ".join(f"{c.date} {_money(analysis, c.amount)}" for c in item.charges)
    lines.extend(_wrap(f"{len(item.charges)} charges: {charges}", "      "))
    return lines


def report(analysis: Analysis) -> str:
    statement = analysis.statement
    stats = analysis.naming
    out = [f"ghostsubs report: {', '.join(statement.files)}",
           f"{_plural(len(statement.charges), 'charge')} and {_plural(len(statement.credits), 'credit')}, "
           f"{statement.first} to {statement.last}, counted from {analysis.as_of}"]
    sign = statement.debits
    if statement.debits_from == "the sign of most rows":
        sign += " (decided by the sign of most rows; --debits changes it)"
    conventions = f"read as: dates {statement.date_order}, decimal '{statement.decimal}', charges {sign}"
    if statement.skipped:
        conventions += f", {len(statement.skipped)} rows skipped (not transactions)"
    if statement.duplicates:
        conventions += f", {statement.duplicates} rows repeated across files counted once"
    out.append(conventions)
    if analysis.future_rows:
        out.append(f"{_plural(analysis.future_rows, 'row')} dated after {analysis.as_of} left out "
                   "(installments booked ahead?); --today counts from another day")
    if analysis.model:
        out.append(f"names: {_plural(stats.spellings, 'descriptor')} ({stats.descriptors} once codes are masked), "
                   f"{stats.asked} asked to {analysis.model} now, {stats.cached} from cache; "
                   f"{stats.verified} named and checked, {stats.unverified} UNVERIFIED (cleaned up by code)")
    else:
        out.append(f"names: {_plural(stats.spellings, 'descriptor')} cleaned up by code (no model)")
    out += ["", line(analysis)]

    def section(title: str, items: list[Series]) -> None:
        if not items:
            return
        total = sum((s.monthly for s in items), Decimal(0))
        out.extend(["", f"{title} ({len(items)}, {_money(analysis, total)}/mo)"])
        for series in items:
            out.extend(_series_block(analysis, series))

    section("SUBSCRIPTIONS", analysis.subscriptions)
    section("OTHER RECURRING: BILLS, RENT, LOANS, OTHER", analysis.other_recurring)
    if analysis.stopped:
        out.extend(["", f"STOPPED: EXPECTED CHARGE MISSING, MAYBE CANCELLED ({len(analysis.stopped)})"])
        for series in analysis.stopped:
            out.extend(_series_block(analysis, series))
    if analysis.varying:
        out.extend(["", f"REGULAR, AMOUNT VARIES, NOT COUNTED ABOVE ({len(analysis.varying)})"])
        for item in analysis.varying:
            out.extend(_varying_block(analysis, item))
    if analysis.possible_trials:
        out.extend(["", f"POSSIBLE TRIAL TURNED PAID ({len(analysis.possible_trials)}): too few charges to "
                        "count as recurring yet"])
        for merchant, trial, full in analysis.possible_trials:
            out.append(f"  {merchant}: {_money(analysis, trial.amount)} on {trial.date}, then "
                       f"{_money(analysis, full.amount)} on {full.date} ({full.description})")
    if analysis.refunds:
        out.extend(["", f"REFUNDS MATCHING A CHARGE ({len(analysis.refunds)})"])
        for refund in analysis.refunds:
            out.append(f"  {refund.credit.date} {_money(analysis, -refund.credit.amount)} back from "
                       f"{refund.merchant} for the {refund.charge.date} charge ({refund.credit.description})")
    unverified = sorted({n.raw: n for n in analysis.unverified if n.source != "cleanup"}.values(),
                        key=lambda n: n.raw)
    if unverified:
        out.extend(["", f"UNVERIFIED NAMES ({len(unverified)}): the model's name failed the check, "
                        "the cleaned descriptor is shown"])
        for name in unverified:
            said = f'model said "{name.model_said}", ' if name.model_said else ""
            out.append(f"  {name.raw}  -> {name.name}  ({said}{name.reason})")
    return "\n".join(out)


def _number(amount: Decimal) -> float:
    return float(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def _series_json(analysis: Analysis, series: Series) -> dict[str, Any]:
    names = {}
    for charge in series.charges + ([series.trial] if series.trial else []):
        name = analysis.names.get(charge.description)
        if name is not None and charge.description not in names:
            names[charge.description] = {"descriptor": name.raw, "name": name.name, "verified": name.verified,
                                         "evidence": name.evidence, "reason": name.reason}
    return {
        "merchant": series.merchant,
        "kind": series.kind,
        "counted_as": ("recurring" if not analysis.model else
                       "subscription" if series.is_subscription else "other_recurring"),
        "cadence": series.cadence.name,
        "every_days": series.gap_days,
        "amount": _number(series.amount),
        "monthly": _number(series.monthly),
        "status": "missing" if series.missing else "active",
        "new": series.new,
        "first": series.first.isoformat(),
        "last": series.last.isoformat(),
        "next_expected": series.next_expected.isoformat() if series.next_expected else None,
        "renews_in_days": series.renews_in,
        "found_by": series.pass_name,
        "trial": None if series.trial is None else {"date": series.trial.date.isoformat(),
                                                    "amount": _number(series.trial.amount)},
        "changes": [{"date": c.date.isoformat(), "from": _number(c.old), "to": _number(c.new),
                     "percent": int(c.percent)} for c in series.changes],
        "charges": [{"date": c.date.isoformat(), "amount": _number(c.amount), "descriptor": c.description}
                    for c in series.charges],
        "duplicates": [{"date": c.date.isoformat(), "amount": _number(c.amount), "descriptor": c.description}
                       for c in series.duplicates],
        "names": list(names.values()),
    }


def as_json(analysis: Analysis) -> dict[str, Any]:
    statement = analysis.statement
    stats = analysis.naming
    return {
        "line": line(analysis),
        "as_of": analysis.as_of.isoformat(),
        "currency": statement.currency,
        "statement": {"files": statement.files, "charges": len(statement.charges),
                      "credits": len(statement.credits),
                      "first": statement.first.isoformat() if statement.first else None,
                      "last": statement.last.isoformat() if statement.last else None,
                      "date_order": statement.date_order, "decimal": statement.decimal,
                      "debits": statement.debits, "debits_decided_by": statement.debits_from,
                      "skipped_rows": statement.skipped,
                      "duplicates_across_files": statement.duplicates},
        "model": analysis.model or None,
        "naming": {"descriptors": stats.spellings, "model_questions": stats.descriptors, "asked": stats.asked,
                   "cached": stats.cached, "verified": stats.verified, "unverified": stats.unverified},
        "recurring_monthly": _number(sum((s.monthly for s in analysis.active), Decimal(0))),
        # Without a model there are no kind labels, so the line has one total and so does this.
        "subscriptions_monthly": (_number(sum((s.monthly for s in analysis.subscriptions), Decimal(0)))
                                  if analysis.model else None),
        "other_recurring_monthly": (_number(sum((s.monthly for s in analysis.other_recurring), Decimal(0)))
                                    if analysis.model else None),
        "rows_after_as_of": analysis.future_rows,
        "possible_trials": [{"merchant": name, "trial_date": trial.date.isoformat(),
                             "trial_amount": _number(trial.amount), "paid_date": full.date.isoformat(),
                             "paid_amount": _number(full.amount)} for name, trial, full in analysis.possible_trials],
        "series": [_series_json(analysis, s) for s in analysis.series],
        "amount_varies": [{"merchant": v.merchant, "cadence": v.cadence.name, "low": _number(v.low),
                           "high": _number(v.high),
                           "charges": [{"date": c.date.isoformat(), "amount": _number(c.amount)} for c in v.charges]}
                          for v in analysis.varying],
        "refunds": [{"date": r.credit.date.isoformat(), "amount": _number(-r.credit.amount), "merchant": r.merchant,
                     "charge_date": r.charge.date.isoformat(), "descriptor": r.credit.description}
                    for r in analysis.refunds],
        "unverified": [{"descriptor": n.raw, "shown_as": n.name, "model_said": n.model_said, "reason": n.reason}
                       for n in analysis.unverified if n.source != "cleanup"],
    }
