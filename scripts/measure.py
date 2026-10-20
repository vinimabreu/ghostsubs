"""Measure ghostsubs against a real local model and write the raw results to measurements/.

    python scripts/measure.py                 # gemma4:12b, 3 naming runs
    python scripts/measure.py --model qwen3:8b --runs 1

1. Naming: every descriptor in examples/truth/descriptors.json, asked K times with
   no cache. Each answer is scored after the check: correct, incomplete (a correct
   name cut short, like "Planet Fit"), wrong but passed, or rejected by the check
   (then the code cleanup is shown, and scored too). The code-cleanup-only
   baseline is scored on the same names.
2. Stability: the same K runs, compared descriptor by descriptor.
3. End to end: every example statement, with the model and without, against
   examples/truth/statements.json.
4. Timing: each example run through the command line with an empty cache, then
   again with the cache it left.

Nothing here is estimated: every number in the summary is counted from the
rows written next to it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ghostsubs import model  # noqa: E402
from ghostsubs.clean import cleaned_name, descriptor_key, identity  # noqa: E402
from ghostsubs.naming import PROMPT_ID, NameCache, Namer, ask_model, check, tidy  # noqa: E402
from ghostsubs.recur import Analysis, analyze  # noqa: E402
from ghostsubs.statement import read_files  # noqa: E402

TRUTH = ROOT / "examples" / "truth"
EXAMPLES = ["bank-a.csv", "card-b.csv", "export-c.csv", "bank-d.csv"]


def code_fingerprint() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted((ROOT / "ghostsubs").glob("*.py"))}


def score_name(shown: str, accept: list[str]) -> str:
    wanted = {identity(a) for a in accept}
    got = identity(shown)
    if got in wanted:
        return "correct"
    if any(w.startswith(got) and len(got) >= 3 for w in wanted):
        return "incomplete"
    return "wrong"


def example_keys() -> set[str]:
    """Cache keys of every descriptor in the four example statements: the descriptors the
    prompt, the tables and the rules were looked at against while they were written."""
    keys: set[str] = set()
    for name in EXAMPLES:
        keys.update(descriptor_key(t.description) for t in read_files([ROOT / "examples" / name]).transactions)
    return keys


def naming(ask: model.Ask, rows: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    seen = example_keys()
    for row in rows:
        baseline = cleaned_name(row["descriptor"])
        entry: dict[str, Any] = {"descriptor": row["descriptor"], "truth": row["merchant"], "accept": row["accept"],
                                 "truth_kind": row["kind"], "baseline": baseline,
                                 "in_examples": descriptor_key(row["descriptor"]) in seen,
                                 "baseline_score": score_name(baseline, row["accept"]), "runs": []}
        for _ in range(runs):
            started = time.perf_counter()
            answer = ask_model(ask, row["descriptor"])
            seconds = time.perf_counter() - started
            reason = check(row["descriptor"], answer)
            shown = tidy(answer.merchant) if reason is None else baseline
            if reason is None:
                outcome = {"correct": "correct", "incomplete": "incomplete_passed",
                           "wrong": "wrong_passed"}[score_name(shown, row["accept"])]
            else:
                outcome = "rejected"
            entry["runs"].append({"merchant": answer.merchant, "evidence": answer.evidence, "kind": answer.kind,
                                  "error": answer.error, "check": reason or "ok", "shown": shown,
                                  "outcome": outcome, "shown_score": score_name(shown, row["accept"]),
                                  "seconds": round(seconds, 3)})
        results.append(entry)

    def tally(run: int) -> dict[str, Any]:
        outcomes = Counter(e["runs"][run]["outcome"] for e in results)
        shown = Counter(e["runs"][run]["shown_score"] for e in results)
        rejected_fallback = Counter(e["runs"][run]["shown_score"] for e in results
                                    if e["runs"][run]["outcome"] == "rejected")
        kinds = sum(1 for e in results if e["runs"][run]["kind"] == e["truth_kind"])
        buckets = sum(1 for e in results
                      if (e["runs"][run]["kind"] == "subscription") == (e["truth_kind"] == "subscription"))
        seconds = sorted(e["runs"][run]["seconds"] for e in results)
        return {"correct": outcomes["correct"], "incomplete_passed": outcomes["incomplete_passed"],
                "wrong_passed": outcomes["wrong_passed"], "rejected": outcomes["rejected"],
                "rejected_then_cleanup_correct": rejected_fallback["correct"],
                "shown_correct": shown["correct"], "kind_correct": kinds, "subscription_or_not_correct": buckets,
                "median_seconds": seconds[len(seconds) // 2], "total_seconds": round(sum(seconds), 1)}

    per_run = [tally(i) for i in range(runs)]
    stable = sum(1 for e in results
                 if len({(r["check"] == "ok", identity(r["shown"]), r["kind"]) for r in e["runs"]}) == 1)
    identical = sum(1 for e in results if len({(r["merchant"], r["evidence"], r["kind"]) for r in e["runs"]}) == 1)
    base = Counter(e["baseline_score"] for e in results)
    split = {label: {"descriptors": sum(1 for e in results if e["in_examples"] == inside),
                     "shown_correct": sum(1 for e in results if e["in_examples"] == inside
                                          and e["runs"][0]["shown_score"] == "correct"),
                     "baseline_correct": sum(1 for e in results if e["in_examples"] == inside
                                             and e["baseline_score"] == "correct")}
             for label, inside in (("in_example_statements", True), ("not_in_example_statements", False))}
    return {"descriptors": len(results), "runs": runs, "per_run": per_run, "split": split,
            "baseline": {"correct": base["correct"], "incomplete": base["incomplete"], "wrong": base["wrong"]},
            "stability": {"same_outcome_name_and_kind_in_all_runs": stable,
                          "identical_answer_in_all_runs": identical},
            "rows": results}


def match_series(analysis: Analysis, truth: list[dict[str, Any]]) -> dict[str, Any]:
    detected = list(analysis.series)
    used: set[int] = set()
    rows = []
    for expected in truth:
        wanted = {identity(a) for a in expected["merchant"]}
        hit = None
        loose = None
        for index, series in enumerate(detected):
            if index in used or series.cadence.name != expected["cadence"]:
                continue
            if series.amount != Decimal(expected["amount"]):
                continue
            if identity(series.merchant) in wanted:
                hit = index
                break
            loose = index if loose is None else loose
        row: dict[str, Any] = {"truth": expected["merchant"][0], "cadence": expected["cadence"],
                               "amount": expected["amount"]}
        if hit is None:
            row["found"] = "found_under_another_name" if loose is not None else "missed"
            if loose is not None:
                row["name_shown"] = detected[loose].merchant
                used.add(loose)
            rows.append(row)
            continue
        used.add(hit)
        series = detected[hit]
        row["found"] = "found"
        row["name_shown"] = series.merchant
        row["status_ok"] = ("missing" if series.missing else "active") == expected["status"]
        row["bucket_ok"] = ("subscription" if series.is_subscription else "other") == expected["bucket"]
        want_changes = [tuple(c) for c in expected.get("changes", [])]
        got_changes = [(c.date.isoformat(), f"{c.old:.2f}", f"{c.new:.2f}") for c in series.changes]
        row["changes_ok"] = got_changes == want_changes
        row["trial_ok"] = (f"{series.trial.amount:.2f}" if series.trial else None) == expected.get("trial")
        row["new_ok"] = series.new == bool(expected.get("new", False))
        if "renews_in" in expected:
            row["renews_in_ok"] = series.renews_in == expected["renews_in"]
        rows.append(row)
    extra = [{"name_shown": s.merchant, "cadence": s.cadence.name, "amount": f"{s.amount:.2f}",
              "status": "missing" if s.missing else "active"} for i, s in enumerate(detected) if i not in used]
    found = sum(1 for r in rows if r["found"] == "found")
    checks: Counter[str] = Counter()
    for r in rows:
        for key, value in r.items():
            if key.endswith("_ok"):
                checks[key] += 1
                checks[key + "_true"] += int(value)
    return {"truth": len(truth), "detected": len(detected), "found": found,
            "found_under_another_name": sum(1 for r in rows if r["found"] == "found_under_another_name"),
            "missed": sum(1 for r in rows if r["found"] == "missed"), "false_positives": len(extra),
            "precision": round(found / len(detected), 3) if detected else None,
            "recall": round(found / len(truth), 3) if truth else None,
            "attribute_checks": {k[:-3]: f"{checks[k + '_true']}/{checks[k]}" for k in sorted(checks)
                                 if not k.endswith("_true")},
            "rows": rows, "not_in_truth": extra}


def end_to_end(cache_dir: Path, model_name: str, host: str) -> dict[str, Any]:
    truth = json.loads((TRUTH / "statements.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for name in EXAMPLES:
        statement = read_files([ROOT / "examples" / name])
        raws = [t.description for t in statement.charges]
        with_model = Namer(model.asker(model=model_name, host=host), model=model_name,
                           cache=NameCache(cache_dir, model_name)).name_all(raws)
        without = Namer(None).name_all(raws)
        out[name] = {"with_model": match_series(analyze(statement, with_model, model=model_name),
                                                truth[name]["series"]),
                     "code_cleanup_only": match_series(analyze(statement, without), truth[name]["series"])}
    totals: dict[str, Any] = {}
    for mode in ("with_model", "code_cleanup_only"):
        found = sum(out[n][mode]["found"] for n in EXAMPLES)
        detected = sum(out[n][mode]["detected"] for n in EXAMPLES)
        expected = sum(out[n][mode]["truth"] for n in EXAMPLES)
        totals[mode] = {"truth": expected, "detected": detected, "found": found,
                        "found_under_another_name": sum(out[n][mode]["found_under_another_name"] for n in EXAMPLES),
                        "missed": sum(out[n][mode]["missed"] for n in EXAMPLES),
                        "false_positives": sum(out[n][mode]["false_positives"] for n in EXAMPLES),
                        "precision": round(found / detected, 3) if detected else None,
                        "recall": round(found / expected, 3) if expected else None}
    return {"totals": totals, "files": out}


def timing(model_name: str, host: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name in EXAMPLES:
        path = ROOT / "examples" / name
        with tempfile.TemporaryDirectory() as cache:
            runs: dict[str, Any] = {}
            for label in ("cold", "cached"):
                started = time.perf_counter()
                done = subprocess.run([sys.executable, "-m", "ghostsubs", "report", str(path), "--json", "--cache",
                                       cache, "--model", model_name, "--host", host], cwd=ROOT,
                                      capture_output=True, text=True, check=True)
                seconds = time.perf_counter() - started
                naming_stats = json.loads(done.stdout)["naming"]
                runs[label] = {"seconds": round(seconds, 2), "asked": naming_stats["asked"],
                               "cached": naming_stats["cached"]}
        started = time.perf_counter()
        subprocess.run([sys.executable, "-m", "ghostsubs", "report", str(path), "--no-model"], cwd=ROOT,
                       capture_output=True, text=True, check=True)
        runs["no_model"] = {"seconds": round(time.perf_counter() - started, 2)}
        rows.append({"file": name, **runs})
    return {"rows": rows,
            "cold_total_seconds": round(sum(r["cold"]["seconds"] for r in rows), 2),
            "cached_total_seconds": round(sum(r["cached"]["seconds"] for r in rows), 2),
            "asked_total": sum(r["cold"]["asked"] for r in rows)}


def summary(settings: dict[str, Any], names: dict[str, Any], e2e: dict[str, Any], times: dict[str, Any]) -> str:
    n = names["descriptors"]
    lines = [f"# Measurement {settings['date']}", "",
             f"Model `{settings['model']}` (digest `{settings['digest']}`), prompt `{settings['prompt']}`, "
             f"temperature 0, thinking off, Ollama on {settings['host']}. {settings['machine']}.", "",
             f"## Naming: {n} made-up descriptors, {names['runs']} runs, no cache", "",
             "| | " + " | ".join(f"run {i + 1}" for i in range(names["runs"])) + " |",
             "|---|" + "---|" * names["runs"]]
    for key, label in (("correct", "named by the model, checked, correct"),
                       ("incomplete_passed", "checked, correct but cut short"),
                       ("wrong_passed", "checked, wrong (passed the check)"),
                       ("rejected", "rejected by the check"),
                       ("rejected_then_cleanup_correct", "of those, code cleanup shown and correct"),
                       ("shown_correct", "name shown is correct, all paths"),
                       ("kind_correct", "kind label matches the truth"),
                       ("subscription_or_not_correct", "subscription or not, matches the truth")):
        lines.append(f"| {label} | " + " | ".join(str(r[key]) for r in names["per_run"]) + " |")
    lines.append("| median seconds per call | " + " | ".join(str(r["median_seconds"]) for r in names["per_run"])
                 + " |")
    base = names["baseline"]
    split = names["split"]
    lines += ["", f"Code cleanup only (no model): {base['correct']} of {n} correct, {base['incomplete']} cut short, "
                  f"{base['wrong']} wrong.", "",
              f"Descriptors that also appear in the example statements (looked at while the prompt and the check "
              f"were written): {split['in_example_statements']['shown_correct']} of "
              f"{split['in_example_statements']['descriptors']} shown right (cleanup only "
              f"{split['in_example_statements']['baseline_correct']}). The others: "
              f"{split['not_in_example_statements']['shown_correct']} of "
              f"{split['not_in_example_statements']['descriptors']} (cleanup only "
              f"{split['not_in_example_statements']['baseline_correct']}).", "",
              f"Stability: {names['stability']['same_outcome_name_and_kind_in_all_runs']} of {n} descriptors got the "
              f"same outcome, name and kind in all {names['runs']} runs; "
              f"{names['stability']['identical_answer_in_all_runs']} got a character-identical answer.", "",
              "## End to end: the four example statements", "",
              "| | with the model | code cleanup only |", "|---|---|---|"]
    for key in ("truth", "detected", "found", "found_under_another_name", "missed", "false_positives", "precision",
                "recall"):
        lines.append(f"| {key.replace('_', ' ')} | {e2e['totals']['with_model'][key]} | "
                     f"{e2e['totals']['code_cleanup_only'][key]} |")
    lines += ["", "A series counts as found when merchant, cadence and current amount all match the truth. Found "
                  "under another name: right cadence and amount, wrong merchant name.", "",
              "## Time: command line, empty cache, then the cache it left", "",
              "| file | cold | descriptors asked | cached | no model |", "|---|---|---|---|---|"]
    for row in times["rows"]:
        lines.append(f"| {row['file']} | {row['cold']['seconds']} s | {row['cold']['asked']} | "
                     f"{row['cached']['seconds']} s | {row['no_model']['seconds']} s |")
    lines += ["", f"Total: {times['cold_total_seconds']} s cold, {times['cached_total_seconds']} s cached. The model "
                  "was already loaded in Ollama memory (a warm-up call ran first).", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=model.DEFAULT_MODEL)
    parser.add_argument("--host", default=model.DEFAULT_HOST)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "measurements")
    parser.add_argument("--record", type=Path, default=None,
                        help="also save the example statements' model answers here (the tests replay them)")
    args = parser.parse_args()

    ask = model.asker(model=args.model, host=args.host)
    started = time.perf_counter()
    ask_model(ask, "WARM UP CALL 0000")
    warm_up = round(time.perf_counter() - started, 2)
    now = datetime.now().astimezone()
    settings = {"date": now.date().isoformat(), "started": now.isoformat(timespec="seconds"),
                "model": args.model, "digest": model.digest(args.model, args.host), "prompt": PROMPT_ID,
                "temperature": 0, "think": False, "host": args.host, "runs": args.runs,
                "warm_up_seconds": warm_up, "python": platform.python_version(),
                "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
                "code_sha256": code_fingerprint()}
    rows = json.loads((TRUTH / "descriptors.json").read_text(encoding="utf-8"))
    print(f"naming {len(rows)} descriptors x {args.runs}...", file=sys.stderr)
    names = naming(ask, rows, args.runs)
    print("timing...", file=sys.stderr)
    times = timing(args.model, args.host)
    print("end to end...", file=sys.stderr)
    with tempfile.TemporaryDirectory() as cache:
        e2e = end_to_end(Path(cache), args.model, args.host)
        if args.record:
            data = json.loads(NameCache(Path(cache), args.model).path.read_text(encoding="utf-8"))
            args.record.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                                   encoding="utf-8")
    stamp = settings["date"]
    slug = args.model.replace(":", "-").replace("/", "-")
    args.out.mkdir(parents=True, exist_ok=True)
    for kind, payload in (("naming", names), ("endtoend", e2e), ("timing", times)):
        path = args.out / f"{stamp}-{slug}-{kind}.json"
        path.write_text(json.dumps({"settings": settings, **payload}, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    text = summary(settings, names, e2e, times)
    (args.out / f"{stamp}-{slug}-summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
