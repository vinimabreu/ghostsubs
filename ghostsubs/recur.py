"""Find what a statement charges on repeat. Plain code only: no model in here.

Charges are grouped by merchant (the checked names) and each merchant is
searched twice:

1. **The whole merchant.** A chain of charges spaced like a cadence, picking at
   each step the charge closest in amount to the previous one. It counts when:
   at most one other charge from the merchant falls inside it (a duplicate of a
   chain charge, same descriptor and amount within 3 days, is set aside instead);
   its amounts stay within the tolerance (5% by default) or change in a few clean
   steps, the first price recurring on its own before any change and every price
   but the latest holding for at least two charges; it lands on one schedule (a
   calendar date, or for 5 charges or more a fixed number of days); it owns its
   descriptor (at least half of the merchant's charges under the same cleaned
   descriptor in that time, all of them for a yearly pair, which must also agree
   within 1%); and it is not the end of a longer schedule that started a cadence
   earlier. That finds the plain subscription and its price changes. A monthly
   bill whose amount keeps moving is listed apart and called nothing.
2. **The exact amount.** What is left is grouped by amount to the cent, so a
   membership with its own descriptor hiding among purchases at the same store (a
   yearly warehouse club fee among weekly shopping, a ride pass among rides) still
   shows. Here a price counts when it repeats to the cent and the same price
   (within 1%) does not show up off the schedule under the same descriptor. Two
   pieces at different prices join as a price change only under the same
   descriptor and within a factor of 1.5.

Cadence comes from the median interval of the chain. Thresholds (``CADENCES``):
weekly needs 6 charges, monthly and quarterly 3, yearly 2.
"""

from __future__ import annotations

import bisect
import calendar
import statistics
from collections import Counter, defaultdict
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .clean import cleaned_name, identity
from .naming import Name, NamingStats
from .statement import Statement, Transaction

CENT = Decimal("0.01")


@dataclass(frozen=True)
class Cadence:
    name: str
    nominal: int  # days, used to break ties between candidate charges
    lo: int  # an interval in [lo, hi] days fits this cadence
    hi: int
    skip_lo: int | None  # one interval in [skip_lo, skip_hi] counts as one missed charge
    skip_hi: int | None
    min_count: int  # charges needed before it counts as recurring
    grace: int  # days past the expected date before the charge counts as missing
    months: int  # calendar months between charges (0: weekly)
    per_month: Decimal  # charges per month, for the monthly-equivalent cost


CADENCES = (
    Cadence("weekly", 7, 5, 9, 12, 16, 6, 3, 0, Decimal(52) / Decimal(12)),
    Cadence("monthly", 30, 25, 36, 56, 66, 3, 7, 1, Decimal(1)),
    Cadence("quarterly", 91, 80, 100, 170, 195, 3, 14, 3, Decimal(1) / Decimal(3)),
    Cadence("yearly", 365, 350, 380, None, None, 2, 21, 12, Decimal(1) / Decimal(12)),
)
BY_NAME = {c.name: c for c in CADENCES}

TRIAL_RATIO = Decimal("0.25")  # a first charge at most a quarter of the price is a trial
PAIR_TOLERANCE = Decimal("0.01")  # two charges a year apart must agree this closely
NEAR = Decimal("0.01")  # in the exact pass, a price this close under the same descriptor is the same price
LINK_RATIO = Decimal("1.5")  # exact-amount pieces join as a price change only within this ratio
NEW_DAYS = 31  # "new": the charge that made it recurring is this recent
RECENT_DAYS = 62  # a price change or a stop this recent goes in the line
VARYING_MIN = 6  # a monthly charge whose amount moves is listed after six months of it


@dataclass(frozen=True)
class PriceChange:
    date: date
    old: Decimal
    new: Decimal

    @property
    def percent(self) -> Decimal:
        return ((self.new - self.old) / self.old * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass
class Series:
    merchant: str
    identity: str
    kind: str
    cadence: Cadence
    charges: list[Transaction]
    changes: list[PriceChange] = field(default_factory=list)
    trial: Transaction | None = None
    pass_name: str = "merchant"
    gap_days: int | None = None  # a plan billed every N days instead of on a date
    duplicates: list[Transaction] = field(default_factory=list)  # repeated charges, set aside
    next_expected: date | None = None
    renews_in: int = 0
    missing: bool = False
    new: bool = False

    @property
    def amount(self) -> Decimal:
        return self.charges[-1].amount

    @property
    def monthly(self) -> Decimal:
        per_month = (Decimal("30.44") / Decimal(self.gap_days)) if self.gap_days else self.cadence.per_month
        return (self.amount * per_month).quantize(CENT, rounding=ROUND_HALF_UP)

    @property
    def schedule(self) -> str:
        """How it bills, for the line and the report: monthly, or every 28 days."""
        return f"every {self.gap_days} days" if self.gap_days else self.cadence.name

    @property
    def first(self) -> date:
        return self.charges[0].date

    @property
    def last(self) -> date:
        return self.charges[-1].date

    @property
    def is_subscription(self) -> bool:
        """Kind is a label: it decides which total a series adds to, never whether it recurs."""
        return self.kind in ("subscription", "unknown")


@dataclass
class Varying:
    merchant: str
    identity: str
    kind: str
    cadence: Cadence
    charges: list[Transaction]

    @property
    def low(self) -> Decimal:
        return min(c.amount for c in self.charges)

    @property
    def high(self) -> Decimal:
        return max(c.amount for c in self.charges)


@dataclass(frozen=True)
class Refund:
    credit: Transaction
    charge: Transaction
    merchant: str


@dataclass
class Analysis:
    statement: Statement
    names: dict[str, Name]
    as_of: date
    window: int
    series: list[Series]
    varying: list[Varying]
    refunds: list[Refund]
    model: str = ""
    naming: NamingStats = field(default_factory=NamingStats)
    future_rows: int = 0  # rows dated after the day counted from, left out
    possible_trials: list[tuple[str, Transaction, Transaction]] = field(default_factory=list)

    @property
    def active(self) -> list[Series]:
        return [s for s in self.series if not s.missing]

    @property
    def subscriptions(self) -> list[Series]:
        return [s for s in self.active if s.is_subscription]

    @property
    def other_recurring(self) -> list[Series]:
        return [s for s in self.active if not s.is_subscription]

    @property
    def stopped(self) -> list[Series]:
        return [s for s in self.series if s.missing]

    @property
    def unverified(self) -> list[Name]:
        return [n for n in self.names.values() if not n.verified]


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_date(day: date, cadence: Cadence) -> date:
    return day + timedelta(days=7) if cadence.months == 0 else add_months(day, cadence.months)


def _same_price(amount: Decimal, reference: Decimal, tolerance: Decimal, exact: bool) -> bool:
    if exact:
        return amount == reference
    return abs(amount - reference) <= max(CENT, tolerance * reference)


def _runs(charges: list[Transaction], tolerance: Decimal, exact: bool) -> list[list[Transaction]]:
    runs: list[list[Transaction]] = [[charges[0]]]
    for charge in charges[1:]:
        if _same_price(charge.amount, runs[-1][0].amount, tolerance, exact):
            runs[-1].append(charge)
        else:
            runs.append([charge])
    return runs


@lru_cache(maxsize=4096)
def signature(raw: str) -> str:
    """The descriptor as code cleans it, as an identity: every ride is ``UBER *TRIP``, the pass
    is ``UBER *ONE``. A plan has to own most of the charges printed under its descriptor."""
    return identity(cleaned_name(raw))


class _Index:
    """Charges of one merchant sorted by date, with day numbers for range lookups."""

    def __init__(self, charges: list[Transaction]) -> None:
        self.charges = sorted(charges, key=lambda c: (c.date, c.amount, c.line))
        self.days = [c.date.toordinal() for c in self.charges]
        self.signatures = [signature(c.description) for c in self.charges]

    def duplicates(self, indices: list[int]) -> list[int]:
        """Charges that repeat one of the chain's: same cleaned descriptor, same amount, within
        3 days. A plan charged twice once is still the plan; the extra is reported."""
        chain = set(indices)
        found: set[int] = set()
        for j in indices:
            left = bisect.bisect_left(self.days, self.days[j] - 3)
            right = bisect.bisect_right(self.days, self.days[j] + 3)
            for i in range(left, right):
                if (i not in chain and self.charges[i].amount == self.charges[j].amount
                        and self.signatures[i] == self.signatures[j]):
                    found.add(i)
        return sorted(found)

    def owns_its_descriptor(self, indices: list[int], also: list[int] | None = None) -> bool:
        """At least half of the merchant's charges printed under the chain's descriptors, inside
        the chain's dates, are the chain (all of them for a yearly pair). A ride that cost the
        same twice a year apart is one of a hundred rides under the same descriptor; a ride pass
        is the only thing under its own."""
        mine = {self.signatures[i] for i in indices}
        first, last = self.days[indices[0]], self.days[indices[-1]]
        same = [i for i, day in enumerate(self.days) if first <= day <= last and self.signatures[i] in mine]
        inside = set(indices) | set(also or [])
        owned = sum(1 for i in same if i in inside)
        return owned == len(same) if len(indices) == 2 else 2 * owned >= len(same)

    def between(self, after: int, lo: int, hi: int) -> list[int]:
        start = self.days[after] + lo
        end = self.days[after] + hi
        left = bisect.bisect_left(self.days, start)
        right = bisect.bisect_right(self.days, end)
        return [j for j in range(max(left, after + 1), right)]

    def chain(self, start: int, cadence: Cadence, allowed: set[int]) -> list[int]:
        chain = [start]
        current = start
        skipped = False
        while True:
            candidates = [j for j in self.between(current, cadence.lo, cadence.hi) if j in allowed]
            step = 1
            if not candidates and cadence.skip_lo is not None and cadence.skip_hi is not None and not skipped:
                candidates = [j for j in self.between(current, cadence.skip_lo, cadence.skip_hi) if j in allowed]
                step = 2
            if not candidates:
                return chain
            reference = self.charges[current].amount
            nominal = cadence.nominal * step

            def closeness(j: int) -> tuple[Decimal, int, int]:
                gap = self.days[j] - self.days[current]
                return abs(self.charges[j].amount - reference) / reference, abs(gap - nominal), j

            pick = min(candidates, key=closeness)
            skipped = skipped or step == 2
            chain.append(pick)
            current = pick


@dataclass
class _Candidate:
    indices: list[int]
    cadence: Cadence
    runs: list[list[Transaction]]
    stable: bool
    dups: list[int] = field(default_factory=list)

    @property
    def score(self) -> tuple[int, int, int]:
        return (int(self.stable), len(self.indices), -len(self.runs))


# How far apart, in days, the charges of a series may land from one fixed schedule (the same
# weekday, the same day of the month, the same day of the year), posting delays included.
CALENDAR_SLACK = {"weekly": 3, "monthly": 6, "quarterly": 8, "yearly": 10}


FIXED_GAP_MIN = 5  # charges needed before "every N days" counts as a schedule of its own
FIXED_GAP_SLACK = 4  # a billing system that counts days lands within a posting delay of its count


def _spread(offsets: list[int]) -> int:
    return max(offsets) - min(offsets)


def on_calendar(charges: list[Transaction], cadence: Cadence) -> bool:
    """A plan bills on a calendar date; purchases that happen to be about a month apart drift."""
    first = charges[0].date
    offsets = []
    for charge in charges:
        elapsed = (charge.date - first).days
        if cadence.months == 0:
            expected = first + timedelta(days=7 * round(elapsed / 7))
        else:
            steps = round(elapsed / (30.44 * cadence.months))
            expected = add_months(first, steps * cadence.months)
        offsets.append((charge.date - expected).days)
    return _spread(offsets) <= CALENDAR_SLACK[cadence.name]


def fixed_gap(charges: list[Transaction], cadence: Cadence) -> int | None:
    """The number of days between charges for a plan that bills every N days (every 4 weeks,
    every 30 days) instead of on a date, when 5 charges or more all land on that spacing."""
    if len(charges) < FIXED_GAP_MIN:
        return None
    gaps = [(b.date - a.date).days for a, b in zip(charges, charges[1:])]
    single = [g for g in gaps if cadence.lo <= g <= cadence.hi]
    if not single:
        return None
    gap = round(statistics.median(single))
    first = charges[0].date
    offsets = [(c.date - first).days - gap * round((c.date - first).days / gap) for c in charges]
    return gap if _spread(offsets) <= min(FIXED_GAP_SLACK, CALENDAR_SLACK[cadence.name]) else None


def on_schedule(charges: list[Transaction], cadence: Cadence) -> bool:
    return on_calendar(charges, cadence) or fixed_gap(charges, cadence) is not None


def _cadence_of(charges: list[Transaction]) -> Cadence | None:
    gaps = [(b.date - a.date).days for a, b in zip(charges, charges[1:])]
    if not gaps:
        return None
    middle = statistics.median(gaps)
    for cadence in CADENCES:
        if cadence.lo <= middle <= cadence.hi:
            return cadence
    return None


def _lead_in(index: _Index, remaining: set[int], indices: list[int], cadence: Cadence) -> bool:
    """True when the merchant also charged one cadence before the chain starts (and that
    charge is not a trial). A real plan's chain would have started there; a stable piece
    that starts later is the end of a schedule whose amount keeps moving, even when the
    charge just before it happens to cost about the same."""
    start = indices[0]
    first = index.charges[start]
    for i in remaining:
        if i in indices:
            continue
        gap = index.days[start] - index.days[i]
        if cadence.lo <= gap <= cadence.hi and index.charges[i].amount > TRIAL_RATIO * first.amount:
            return True
    return False


def _merchant_pass(index: _Index, remaining: set[int], tolerance: Decimal) -> _Candidate | None:
    best: _Candidate | None = None
    for cadence in CADENCES:
        for start in sorted(remaining):
            indices = index.chain(start, cadence, remaining)
            if len(indices) < cadence.min_count:
                continue
            charges = [index.charges[i] for i in indices]
            if _cadence_of(charges) is not cadence:
                continue
            dups = index.duplicates(indices)
            inside = set(indices) | set(dups)
            first, last = index.days[indices[0]], index.days[indices[-1]]
            extras = sum(1 for i in remaining if i not in inside and first <= index.days[i] <= last)
            if extras > 1:
                continue
            # Two charges can't show a price change, so a pair must agree within 1%.
            runs = _runs(charges, tolerance if len(charges) > 2 else PAIR_TOLERANCE, exact=False)
            changes = len(runs) - 1
            # A new price seen once counts only when the old one had already made it recurring.
            fresh_step = len(runs[-1]) == 1 and changes > 0
            # A price change counts only after the first price recurred on its own.
            stable = ((changes == 0 or len(runs[0]) >= cadence.min_count)
                      and all(len(run) >= 2 for run in runs[:-1]) and changes <= max(1, len(charges) // 6)
                      and (not fresh_step or (extras == 0 and len(charges) - 1 >= cadence.min_count)))
            if stable and not (on_schedule(charges, cadence) and index.owns_its_descriptor(indices, dups)):
                continue
            if stable and _lead_in(index, remaining, indices + dups, cadence):
                continue  # the tail of a longer schedule whose amount keeps moving: a bill, not a plan
            candidate = _Candidate(indices, cadence, runs, stable, dups)
            if best is None or candidate.score > best.score:
                best = candidate
    return best


def _exact_fragments(index: _Index, remaining: set[int]) -> list[_Candidate]:
    groups: dict[Decimal, list[int]] = defaultdict(list)
    for i in sorted(remaining):
        groups[index.charges[i].amount].append(i)
    fragments: list[_Candidate] = []
    for amount, members in groups.items():
        pool = set(members)
        accepted: set[int] = set()
        while len(pool) >= 2:
            best: _Candidate | None = None
            for cadence in CADENCES:
                for start in sorted(pool):
                    indices = index.chain(start, cadence, pool)
                    if len(indices) < 2:
                        continue
                    charges = [index.charges[i] for i in indices]
                    if _cadence_of(charges) is not cadence:
                        continue
                    first, last = index.days[indices[0]], index.days[indices[-1]]
                    mine = {index.signatures[i] for i in indices}
                    dups = index.duplicates(indices)
                    off = [i for i in range(len(index.charges)) if i not in indices and i not in accepted
                           and i not in dups and abs(index.charges[i].amount - amount) <= NEAR * amount
                           and index.signatures[i] in mine]
                    if any(first <= index.days[i] <= last for i in off) or len(off) > max(1, len(indices) // 4):
                        continue  # the same price also shows up off the schedule under this descriptor
                    if not (on_schedule(charges, cadence) and index.owns_its_descriptor(indices, dups)):
                        continue
                    candidate = _Candidate(indices, cadence, [charges], True, dups)
                    if best is None or candidate.score > best.score:
                        best = candidate
            if best is None:
                break
            fragments.append(best)
            pool -= set(best.indices) | set(best.dups)
            accepted |= set(best.indices) | set(best.dups)
    return fragments


def _link(index: _Index, fragments: list[_Candidate]) -> list[_Candidate]:
    """Join exact-amount fragments into one series when one price ends where the next begins."""
    fragments = sorted(fragments, key=lambda f: index.days[f.indices[0]])
    used: set[int] = set()
    joined: list[_Candidate] = []
    for position, fragment in enumerate(fragments):
        if position in used:
            continue
        used.add(position)
        chain = [fragment]
        while True:
            tail = chain[-1]
            last_day = index.days[tail.indices[-1]]
            old_amount = index.charges[tail.indices[0]].amount
            follower = None
            for other_position, other in enumerate(fragments):
                if other_position in used or other.cadence is not tail.cadence:
                    continue
                gap = index.days[other.indices[0]] - last_day
                new_amount = index.charges[other.indices[0]].amount
                old_again = any(index.days[i] > index.days[other.indices[0]] and index.charges[i].amount == old_amount
                                for i in range(len(index.charges)))
                same_descriptor = bool({index.signatures[i] for i in tail.indices}
                                       & {index.signatures[i] for i in other.indices})
                close = old_amount / LINK_RATIO <= new_amount <= old_amount * LINK_RATIO
                if (tail.cadence.lo <= gap <= tail.cadence.hi and new_amount != old_amount and not old_again
                        and same_descriptor and close):
                    follower = other_position
                    break
            if follower is None:
                break
            used.add(follower)
            chain.append(fragments[follower])
        indices = [i for f in chain for i in f.indices]
        if len(indices) >= chain[0].cadence.min_count:
            runs = [[index.charges[i] for i in f.indices] for f in chain]
            joined.append(_Candidate(indices, chain[0].cadence, runs, True, [i for f in chain for i in f.dups]))
    return joined


def find_series(charges: list[Transaction], tolerance: Decimal = Decimal("0.05")
                ) -> tuple[list[tuple[_Candidate, str]], list[_Candidate], _Index]:
    """Recurring series and regular-but-varying chains among one merchant's charges."""
    index = _Index(charges)
    remaining = set(range(len(index.charges)))
    found: list[tuple[_Candidate, str]] = []
    varying: list[_Candidate] = []
    while True:
        best = _merchant_pass(index, remaining, tolerance)
        if best is None:
            break
        if not best.stable:
            # A stable chain always outranks this one, so none is left. Its charges stay in the
            # pool: a membership can hide among the purchases of a weekly shop.
            if best.cadence.name == "monthly" and len(best.indices) >= VARYING_MIN:
                varying.append(best)
            break
        found.append((best, "merchant"))
        remaining -= set(best.indices) | set(best.dups)
    for candidate in _link(index, _exact_fragments(index, remaining)):
        found.append((candidate, "exact"))
        remaining -= set(candidate.indices) | set(candidate.dups)
    found = _join(index, found, tolerance)
    taken = {i for candidate, _ in found for i in candidate.indices}
    varying = [v for v in varying if not taken & set(v.indices)]
    return found, varying, index


def _join(index: _Index, found: list[tuple[_Candidate, str]], tolerance: Decimal) -> list[tuple[_Candidate, str]]:
    """One plan can come out in two pieces when other charges from the merchant sit inside it
    (a yearly fee on the day of a monthly one). Pieces with the same cadence, one cadence
    apart, are one series; a different price across the gap is a price change."""
    items = sorted(found, key=lambda f: index.days[f[0].indices[0]])
    joined = True
    while joined:
        joined = False
        for a in range(len(items)):
            for b in range(len(items)):
                first, second = items[a][0], items[b][0]
                if a == b or first.cadence is not second.cadence:
                    continue
                gap = index.days[second.indices[0]] - index.days[first.indices[-1]]
                cadence = first.cadence
                fits = cadence.lo <= gap <= cadence.hi or (
                    cadence.skip_lo is not None and cadence.skip_hi is not None
                    and cadence.skip_lo <= gap <= cadence.skip_hi)
                if not fits:
                    continue
                old, new = first.runs[-1][0].amount, second.runs[0][0].amount
                if _same_price(new, old, tolerance, False):
                    runs = first.runs[:-1] + [first.runs[-1] + second.runs[0]] + second.runs[1:]
                else:
                    shared = ({index.signatures[i] for i in first.indices}
                              & {index.signatures[i] for i in second.indices})
                    if not shared or not old / LINK_RATIO <= new <= old * LINK_RATIO:
                        continue  # two plans, or a plan and something else: not one price change
                    runs = first.runs + second.runs
                merged = _Candidate(first.indices + second.indices, cadence, runs, True, first.dups + second.dups)
                origin = "merchant" if items[a][1] == items[b][1] == "merchant" else "exact"
                items = [item for k, item in enumerate(items) if k not in (a, b)] + [(merged, origin)]
                items.sort(key=lambda f: index.days[f[0].indices[0]])
                joined = True
                break
            if joined:
                break
    return items


def _changes(runs: list[list[Transaction]]) -> list[PriceChange]:
    return [PriceChange(b[0].date, a[-1].amount, b[0].amount) for a, b in zip(runs, runs[1:])]


def _majority(names: list[Name]) -> tuple[str, str]:
    """The name shown for a series (checked names first) and its kind label."""
    verified = [n for n in names if n.verified]
    pool = verified or names
    shown = Counter(n.name for n in pool).most_common(1)[0][0]
    labels = [n.kind for n in pool if n.kind != "unknown"] or [n.kind for n in names if n.kind != "unknown"]
    return shown, Counter(labels).most_common(1)[0][0] if labels else "unknown"


def _status(series: Series, as_of: date, statement_start: date) -> None:
    cadence = series.cadence
    if series.gap_days:
        series.next_expected = series.last + timedelta(days=series.gap_days)
    else:
        series.next_expected = next_date(series.last, cadence)
    series.renews_in = (series.next_expected - as_of).days
    series.missing = as_of > series.next_expected + timedelta(days=cadence.grace)
    # New: the charge that made it recurring landed in the last month, and the statement
    # reaches back far enough to show there was no charge one cadence before the first.
    confirming = series.charges[cadence.min_count - 1].date
    began = series.trial.date if series.trial else series.first
    series.new = (cadence.months <= 1 and 0 <= (as_of - confirming).days <= NEW_DAYS
                  and (began - statement_start).days >= cadence.hi)


def _merchant_key(name: Name) -> str:
    return name.identity or identity(name.name)


def analyze(statement: Statement, names: dict[str, Name], *, today: date | None = None, window: int = 30,
            tolerance: Decimal = Decimal("0.05"), model: str = "", naming: NamingStats | None = None,
            now: date | None = None) -> Analysis:
    """Everything the line and the report say, computed from the statement and the checked names.

    Days are counted from ``today`` when given, else from the last date in the statement that is
    not after ``now`` (the real date by default), so installments booked ahead don't move it."""
    now = now or date.today()
    past = [t.date for t in statement.transactions if t.date <= now]
    as_of = today or (max(past) if past else statement.last) or now
    future = sum(1 for t in statement.transactions if t.date > as_of)
    charges = [t for t in statement.charges if t.date <= as_of]
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for charge in charges:
        name = names.get(charge.description) or _fallback(charge.description)
        groups[_merchant_key(name)].append(charge)

    series: list[Series] = []
    varying: list[Varying] = []
    trials: list[tuple[str, Transaction, Transaction]] = []
    for key, members in groups.items():
        found, regular, index = find_series(members, tolerance)
        used: set[int] = set()
        for candidate, pass_name in found:
            items = [index.charges[i] for i in sorted(candidate.indices)]
            used.update(candidate.indices)
            involved = [names.get(c.description) or _fallback(c.description) for c in items]
            shown, kind = _majority(involved)
            item = Series(shown, key, kind, candidate.cadence, items, _changes(candidate.runs), None, pass_name)
            if not on_calendar(items, candidate.cadence):
                item.gap_days = fixed_gap(items, candidate.cadence)
            item.duplicates = [index.charges[i] for i in sorted(candidate.dups)]
            used.update(candidate.dups)
            series.append(item)
        for item in [s for s in series if s.identity == key and s.pass_name == "merchant"]:
            item.trial = _trial(item, index, used)
            if item.trial is not None:
                used.add(index.charges.index(item.trial))
        for candidate in regular:
            items = [index.charges[i] for i in sorted(candidate.indices)]
            involved = [names.get(c.description) or _fallback(c.description) for c in items]
            shown, kind = _majority(involved)
            varying.append(Varying(shown, key, kind, candidate.cadence, items))
        if not found:
            early = _early_trial(index.charges, as_of, statement.first or as_of, tolerance)
            if early is not None:
                shown, kind = _majority([names.get(c.description) or _fallback(c.description) for c in index.charges])
                # A new shop also starts with a small purchase; only a merchant the model calls a
                # subscription gets the early flag (any merchant when there is no model).
                if kind == "subscription" or (not model and kind == "unknown"):
                    trials.append((shown, early[0], early[1]))

    start = statement.first or as_of
    for item in series:
        _status(item, as_of, start)
    series.sort(key=lambda s: (-s.monthly, s.merchant))
    varying.sort(key=lambda v: v.merchant)
    refunds = _refunds(statement, names, as_of)
    return Analysis(statement, names, as_of, window, series, varying, refunds, model, naming or NamingStats(),
                    future_rows=future, possible_trials=sorted(trials, key=lambda t: t[2].date))


def _early_trial(charges: list[Transaction], as_of: date, statement_start: date, tolerance: Decimal
                 ) -> tuple[Transaction, Transaction] | None:
    """A merchant seen only as a small charge followed by one or two full ones about a month
    apart, the last in the past month: a trial that just turned paid, before it has charged
    often enough to count as recurring. Returns (trial, first full charge)."""
    if not 2 <= len(charges) <= 3:
        return None
    trial, full = charges[0], charges[1:]
    if not Decimal(0) < trial.amount <= TRIAL_RATIO * full[0].amount:
        return None
    if (trial.date - statement_start).days < 36 or (as_of - full[-1].date).days > 35:
        return None
    if not 5 <= (full[0].date - trial.date).days <= 36:
        return None
    monthly = BY_NAME["monthly"]
    for a, b in zip(full, full[1:]):
        if not (monthly.lo <= (b.date - a.date).days <= monthly.hi
                and _same_price(b.amount, a.amount, tolerance, False)):
            return None
    return trial, full[0]


def _fallback(raw: str) -> Name:
    shown = cleaned_name(raw)
    return Name(raw, shown, identity(shown), False, "unknown", "", "no name", "cleanup")


def _trial(series: Series, index: _Index, used: set[int]) -> Transaction | None:
    first = series.charges[0]
    best = None
    for i, charge in enumerate(index.charges):
        if i in used:
            continue
        gap = (first.date - charge.date).days
        if 2 <= gap <= 45 and Decimal(0) < charge.amount <= TRIAL_RATIO * first.amount:
            if best is None or charge.date > best.date:
                best = charge
    return best


_REFUND_WORDS = ("estorno", "refund", "return", "devolucao", "reembolso", "reversal", "credit", "credito",
                 "cr", "canc", "cancelamento")


def _strip_refund_words(name: str) -> str:
    words = [w for w in name.split() if w.casefold().strip(".:-*") not in _REFUND_WORDS]
    return " ".join(words) or name


def _refunds(statement: Statement, names: dict[str, Name], as_of: date) -> list[Refund]:
    charges = [t for t in statement.charges if t.date <= as_of]
    taken: set[int] = set()
    found: list[Refund] = []
    for credit in statement.credits:
        if credit.date > as_of:
            continue
        credit_id = identity(_strip_refund_words(cleaned_name(credit.description)))
        best: tuple[int, Transaction] | None = None
        for position, charge in enumerate(charges):
            if position in taken or charge.amount != -credit.amount:
                continue
            if not 0 <= (credit.date - charge.date).days <= 60:
                continue
            name = names.get(charge.description)
            options = {identity(cleaned_name(charge.description))}
            if name is not None:
                options.add(name.identity)
            if credit_id in options or any(o and len(o) >= 4 and o in credit_id for o in options):
                if best is None or charge.date > best[1].date:
                    best = (position, charge)
        if best is not None:
            taken.add(best[0])
            name = names.get(best[1].description)
            found.append(Refund(credit, best[1], name.name if name else cleaned_name(best[1].description)))
    return found
