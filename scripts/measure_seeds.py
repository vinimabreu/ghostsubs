"""Run the example statements again with other random seeds and count what was reported but not planted.

    python scripts/measure_seeds.py                  # seeds 1 to 10, gemma4:12b
    python scripts/measure_seeds.py --seeds 20 --no-model

scripts/make_examples.py plants the same plans with every seed; the seed moves
the dates, the amounts, the posting delays and every decoy (rides, groceries,
coffee, cash, food orders, purchases at stores that also sell a membership).
The four statements of the main seed are the ones the code was tuned on; these
are not. Each statement is scored against its own truth file, the same way as
scripts/measure.py, and the raw rows go to measurements/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import EXAMPLES, code_fingerprint, match_series  # noqa: E402

from ghostsubs import model  # noqa: E402
from ghostsubs.naming import PROMPT_ID, NameCache, Namer  # noqa: E402
from ghostsubs.recur import analyze  # noqa: E402
from ghostsubs.render import line  # noqa: E402
from ghostsubs.statement import read_files  # noqa: E402


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("make_examples", Path(__file__).resolve().parent / "make_examples.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_examples"] = module
    spec.loader.exec_module(module)
    return module


def run(seeds: list[int], model_name: str, host: str, use_model: bool) -> dict[str, Any]:
    make = _generator()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as work:
        cache = NameCache(Path(work) / "cache", model_name) if use_model else None
        ask = model.asker(model=model_name, host=host) if use_model else None
        for seed in seeds:
            folder = Path(work) / f"seed-{seed}"
            make.main(folder, seed)
            truth = json.loads((folder / "truth" / "statements.json").read_text(encoding="utf-8"))
            for name in EXAMPLES:
                statement = read_files([folder / name])
                namer = Namer(ask, model=model_name, cache=cache)
                names = namer.name_all(t.description for t in statement.charges)
                analysis = analyze(statement, names, model=model_name if use_model else "")
                scored = match_series(analysis, truth[name]["series"])
                extra = [s for s in analysis.series
                         if (s.merchant, s.cadence.name, f"{s.amount:.2f}") in
                         {(x["name_shown"], x["cadence"], x["amount"]) for x in scored["not_in_truth"]}]
                rows.append({
                    "seed": seed, "file": name, "truth": scored["truth"], "detected": scored["detected"],
                    "found": scored["found"], "found_under_another_name": scored["found_under_another_name"],
                    "missed": scored["missed"], "false_positives": scored["not_in_truth"],
                    "false_positives_active": sum(1 for s in extra if not s.missing),
                    "false_renewals_in_line": sum(1 for s in extra if not s.missing and s.cadence.months >= 3
                                                  and 0 <= s.renews_in <= analysis.window),
                    "possible_trials_flagged": [{"merchant": m, "trial": str(t.amount), "paid": str(f.amount),
                                                 "date": f.date.isoformat()}
                                                for m, t, f in analysis.possible_trials],
                    "missed_rows": [r for r in scored["rows"] if r["found"] == "missed"],
                    "line": line(analysis),
                })
    total = {key: sum(r[key] for r in rows) for key in ("truth", "detected", "found", "found_under_another_name",
                                                         "missed", "false_positives_active", "false_renewals_in_line")}
    total["false_positives"] = sum(len(r["false_positives"]) for r in rows)
    total["possible_trials_flagged"] = sum(len(r["possible_trials_flagged"]) for r in rows)
    total["statements"] = len(rows)
    total["statements_with_a_false_positive"] = sum(1 for r in rows if r["false_positives"])
    total["seeds_with_a_false_positive"] = len({r["seed"] for r in rows if r["false_positives"]})
    return {"totals": total, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=model.DEFAULT_MODEL)
    parser.add_argument("--host", default=model.DEFAULT_HOST)
    parser.add_argument("--seeds", type=int, default=10, help="seeds 1 to N")
    parser.add_argument("--no-model", action="store_true", help="code cleanup names only")
    parser.add_argument("--out", type=Path, default=ROOT / "measurements")
    args = parser.parse_args()
    seeds = list(range(1, args.seeds + 1))
    now = datetime.now().astimezone()
    settings = {"date": now.date().isoformat(), "started": now.isoformat(timespec="seconds"),
                "model": None if args.no_model else args.model,
                "digest": None if args.no_model else model.digest(args.model, args.host),
                "prompt": PROMPT_ID, "seeds": seeds, "code_sha256": code_fingerprint()}
    print(f"seeds {seeds[0]} to {seeds[-1]}...", file=sys.stderr)
    result = run(seeds, args.model, args.host, not args.no_model)
    slug = "no-model" if args.no_model else args.model.replace(":", "-").replace("/", "-")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{settings['date']}-{slug}-seeds.json"
    path.write_text(json.dumps({"settings": settings, **result}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    t = result["totals"]
    print(json.dumps(t, indent=1))
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
