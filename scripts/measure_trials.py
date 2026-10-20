"""How often "possible trial turned paid" fires on new shops, with and without kind labels.

    python scripts/measure_trials.py

Two years of a weekly supermarket, a café, and N new shops a month, each visited
one to three times at random amounts, and no plan at all. Every month-end is
run as a statement on its own, ten seeds per setting. With the model, the
shops carry the label "other" (what the model gives a shop); without one they
carry no label. Every flag counted here is false. No model is called: the
names are fixed, so this measures the rule, not the model.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import code_fingerprint  # noqa: E402

from ghostsubs.clean import identity  # noqa: E402
from ghostsubs.naming import Name  # noqa: E402
from ghostsubs.recur import analyze  # noqa: E402
from ghostsubs.statement import Statement, Transaction  # noqa: E402


def shops(seed: int, new_per_month: int) -> list[tuple[date, str, str]]:
    rng = random.Random(seed)
    rows: list[tuple[date, str, str]] = []
    start, end = date(2024, 10, 1), date(2026, 10, 15)
    day = start
    while day <= end:
        if day.weekday() == 5:
            rows.append((day, "SUPERMARKET", f"{rng.uniform(40, 160):.2f}"))
        if rng.random() < 0.3:
            rows.append((day, "CORNER CAFE", f"{rng.choice([3.5, 4.25, 4.75, 5.5, 6.25]):.2f}"))
        day += timedelta(days=1)
    number, month = 0, start
    while month <= end:
        for _ in range(new_per_month):
            number += 1
            first = month + timedelta(days=rng.randrange(28))
            for visit in range(rng.choice([1, 1, 2, 2, 3])):
                when = first + timedelta(days=rng.randrange(0, 40)) if visit else first
                if when <= end:
                    rows.append((when, f"SHOP {number:04d}", f"{min(400.0, rng.lognormvariate(3, 0.9)):.2f}"))
        month = (month.replace(day=28) + timedelta(days=5)).replace(day=1)
    return sorted(rows)


def flags(rows: list[tuple[date, str, str]], until: date, labelled: bool) -> int:
    seen = [r for r in rows if r[0] <= until]
    charges = [Transaction(d, text, Decimal(a), "sim", i) for i, (d, text, a) in enumerate(seen)]
    names = {text: (Name(text, text.title(), identity(text), True, "other", text, "", "model") if labelled
                    else Name(text, text.title(), identity(text), False, "unknown", "", "no model", "cleanup"))
             for _, text, _ in seen}
    analysis = analyze(Statement(charges, currency="$"), names, today=until, now=until,
                       model="labels" if labelled else "")
    return len(analysis.possible_trials)


def main() -> None:
    results: list[dict[str, Any]] = []
    for new_per_month in (4, 12):
        for labelled in (True, False):
            runs = flagged = total = 0
            for seed in range(1, 11):
                rows = shops(seed, new_per_month)
                for month in range(1, 13):
                    until = date(2025 + (9 + month) // 12, (9 + month) % 12 + 1, 1) - timedelta(days=1)
                    count = flags(rows, until, labelled)
                    runs += 1
                    flagged += bool(count)
                    total += count
            results.append({"new_shops_per_month": new_per_month, "labels": "other" if labelled else "none",
                            "month_end_runs": runs, "runs_with_a_false_flag": flagged, "false_flags": total})
    now = datetime.now().astimezone()
    payload = {"settings": {"date": now.date().isoformat(), "started": now.isoformat(timespec="seconds"),
                            "seeds": list(range(1, 11)), "code_sha256": code_fingerprint()},
               "results": results}
    out = ROOT / "measurements" / f"{now.date().isoformat()}-trial-flags.json"
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
