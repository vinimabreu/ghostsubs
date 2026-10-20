"""Watch a folder of exports: read them all, speak only when something changed.

The state kept between runs is the summary the line is made of (merchants,
amounts, dates), never the statement rows. It sits next to the name cache,
readable only by you.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .naming import make_private_dir
from .recur import Analysis


def csv_files(directory: Path) -> list[Path]:
    """Every export in the folder, oldest name first. Months overlap often; the reader counts a
    row repeated across files once, so dropping one export a month in the folder is enough."""
    return sorted(p for p in Path(directory).iterdir()
                  if p.is_file() and p.suffix.lower() == ".csv" and not p.name.startswith("."))


def state_path(cache_dir: Path, directory: Path) -> Path:
    digest = hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:16]
    return Path(cache_dir) / "watch" / f"{digest}.json"


def fingerprint(analysis: Analysis) -> dict[str, list[list[str]]]:
    """What the line is made of. Two runs with the same fingerprint have nothing new to say."""
    active = analysis.active
    return {
        "active": sorted([s.merchant, s.cadence.name, str(s.amount)] for s in active),
        "changes": sorted([s.merchant, str(c.date), str(c.old), str(c.new)] for s in active for c in s.changes),
        "renewals": sorted([s.merchant, str(s.next_expected)] for s in active
                           if s.cadence.months >= 3 and 0 <= s.renews_in <= analysis.window),
        "new": sorted([s.merchant] for s in active if s.new),
        "stopped": sorted([s.merchant, str(s.last)] for s in analysis.stopped),
        "possible_trials": sorted([name, str(trial.date)] for name, trial, _ in analysis.possible_trials),
    }


def has_news(previous: dict[str, Any] | None, current: dict[str, list[list[str]]]) -> bool:
    """True when the current fingerprint holds an entry the previous one did not. Something
    leaving a list (a renewal passing, a plan no longer new) is not news."""
    if not isinstance(previous, dict):
        return True
    return any(entry not in (previous.get(key) or []) for key, entries in current.items() for entry in entries)


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path = Path(path)
    make_private_dir(path.parent)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".watch-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=1, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
