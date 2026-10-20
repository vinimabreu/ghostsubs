"""ghostsubs: drop a bank or card statement CSV, get one line about what you pay on repeat."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import __version__, model, notify
from .naming import NameCache, Namer, default_cache_dir, stderr_progress
from .recur import Analysis, analyze
from .render import as_json, line, report
from .statement import StatementError, read_files
from .watch import csv_files, fingerprint, has_news, load_state, save_state, state_path


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text} is not a date like 2026-10-19") from error


def _percent(text: str) -> Decimal:
    try:
        value = Decimal(text.strip().rstrip("%"))
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"{text} is not a percentage") from error
    if not Decimal(0) <= value <= Decimal(50):
        raise argparse.ArgumentTypeError("the amount tolerance is between 0% and 50%")
    return value / 100


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print everything as JSON")
    common.add_argument("--model", default=model.DEFAULT_MODEL,
                        help=f"Ollama model that names the descriptors (default {model.DEFAULT_MODEL})")
    common.add_argument("--host", default=model.DEFAULT_HOST, help="Ollama address (default %(default)s)")
    common.add_argument("--allow-remote-model", action="store_true",
                        help="let descriptors go to an Ollama host that is not this machine, or to a cloud model")
    common.add_argument("--no-model", action="store_true",
                        help="no model at all: names come from the code cleanup only")
    common.add_argument("--cache", type=Path, default=None,
                        help=f"where names and watch state are kept (default {default_cache_dir()})")
    common.add_argument("--today", type=_day, default=None,
                        help="count days from this date (default: the last date in the statement)")
    common.add_argument("--window", type=int, default=30,
                        help="mention a quarterly or yearly renewal this many days ahead (default 30)")
    common.add_argument("--tolerance", type=_percent, default=Decimal("0.05"),
                        help="amounts this close count as the same price (default 5%%)")
    common.add_argument("--date-format", default=None, help="dmy, mdy or ymd, when the file can't tell")
    common.add_argument("--debits", choices=("negative", "positive"), default=None,
                        help="how the file writes a charge, when it can't be told: -39.90 or 39.90")
    common.add_argument("--currency", default=None, help="symbol to print amounts with, e.g. R$ or $")

    parser = argparse.ArgumentParser(prog="ghostsubs", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ghostsubs {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("line", parents=[common], help="the one line")
    one.add_argument("files", nargs="+", type=Path, help="statement CSV files (several are merged)")
    full = commands.add_parser("report", parents=[common], help="every recurring charge, with its evidence")
    full.add_argument("files", nargs="+", type=Path)
    watch = commands.add_parser("watch", parents=[common],
                                help="every CSV in a folder; prints only when something changed")
    watch.add_argument("directory", type=Path)
    watch.add_argument("--state", type=Path, default=None, help="state file (default: inside the cache folder)")
    watch.add_argument("--ntfy", metavar="TOPIC", default=None,
                       help="also push the line to this ntfy topic (the line leaves the machine)")
    watch.add_argument("--ntfy-server", default=notify.DEFAULT_SERVER)
    return parser


def _analyze(args: argparse.Namespace, files: list[Path]) -> Analysis:
    statement = read_files(files, date_format=args.date_format, debits=args.debits)
    if args.currency is not None:
        statement.currency = args.currency
    cache_dir = args.cache or default_cache_dir()
    if args.no_model:
        namer = Namer(None)
        model_name = ""
    else:
        ask = model.asker(model=args.model, host=args.host, allow_remote=args.allow_remote_model)
        namer = Namer(ask, model=args.model, cache=NameCache(cache_dir, args.model), progress=stderr_progress)
        model_name = args.model
    names = namer.name_all(t.description for t in statement.charges)
    return analyze(statement, names, today=args.today, window=args.window, tolerance=args.tolerance,
                   model=model_name, naming=namer.stats, now=date.today())


def _cannot_run(args: argparse.Namespace, path: Path, state: dict[str, Any], reason: str, pushed: str) -> int:
    """Say why on stderr; with --ntfy, tell the phone once per distinct reason, so a broken setup
    is heard about and a scheduler that runs every day does not repeat it."""
    print(f"ghostsubs: {reason}", file=sys.stderr)
    if args.ntfy and state.get("error") != pushed:
        notify.send(args.ntfy, f"ghostsubs could not run: {pushed}", server=args.ntfy_server)
        save_state(path, {**state, "error": pushed})
    return 2


def _watch(args: argparse.Namespace) -> int:
    if not args.directory.is_dir():
        print(f"ghostsubs: {args.directory} is not a folder", file=sys.stderr)
        return 2
    if args.ntfy and not notify.valid_topic(args.ntfy):
        print("ghostsubs: an ntfy topic is 1 to 64 letters, digits, - or _", file=sys.stderr)
        return 2
    path = args.state or state_path(args.cache or default_cache_dir(), args.directory)
    state = load_state(path)
    files = csv_files(args.directory)
    readable: list[Path] = []
    for file in files:
        try:
            read_files([file], date_format=args.date_format, debits=args.debits)
            readable.append(file)
        except StatementError as error:
            print(f"ghostsubs: skipped {error}", file=sys.stderr)
    if not readable:
        if not files:
            return _cannot_run(args, path, state, f"no .csv file in {args.directory}", "no .csv file in the folder")
        return _cannot_run(args, path, state, f"none of the {len(files)} .csv files in {args.directory} could be read",
                           f"none of the {len(files)} .csv files in the folder could be read (first: {files[0].name})")
    try:
        analysis = _analyze(args, readable)
    except (StatementError, model.ModelUnavailable, model.RemoteHostRefused) as error:
        return _cannot_run(args, path, state, str(error), str(error))
    current = fingerprint(analysis)
    news = has_news(state.get("fingerprint"), current)
    text = line(analysis)
    if news:
        print(json.dumps(as_json(analysis), indent=2, ensure_ascii=False) if args.json else text)
        if args.ntfy:
            notify.send(args.ntfy, text, server=args.ntfy_server)
    save_state(path, {"files": [f.name for f in readable], "fingerprint": current, "line": text,
                      "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.window < 0:
        print("ghostsubs: --window can't be negative", file=sys.stderr)
        return 2
    try:
        if args.command == "watch":
            return _watch(args)
        analysis = _analyze(args, args.files)
    except StatementError as error:
        print(f"ghostsubs: {error}", file=sys.stderr)
        return 2
    except model.RemoteHostRefused as error:
        print(f"ghostsubs: {error}", file=sys.stderr)
        return 2
    except model.ModelUnavailable as error:
        print(f"ghostsubs: {error}. Names asked so far are cached. Run with --no-model to use the code "
              "cleanup only.", file=sys.stderr)
        return 2
    except notify.NotifyError as error:
        print(f"ghostsubs: {error}; the watch state was not saved, so the next run tries again",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(as_json(analysis), indent=2, ensure_ascii=False))
    elif args.command == "line":
        print(line(analysis))
    else:
        print(report(analysis))
    return 0
