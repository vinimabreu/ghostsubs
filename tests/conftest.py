"""No test reaches a model or the network unless it is marked ``real_model``."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"
RECORDED = Path(__file__).resolve().parent / "recorded_names.json"

from ghostsubs.clean import descriptor_key  # noqa: E402
from ghostsubs.statement import Statement, Transaction  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_model: talks to a real Ollama on this machine")


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_model"):
        return
    import ghostsubs.model as model
    import ghostsubs.notify as notify

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(model, "_call", refuse)
    monkeypatch.setattr(model, "check_local_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(notify, "_opener", refuse)


class FakeModel:
    """A model that answers from a table: descriptor (or its cache key) -> answer dict, or a
    callable raw -> reply text. Counts its calls."""

    def __init__(self, answers: dict[str, Any] | Callable[[str], str] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any] | None = None) -> str:
        raw = user.removeprefix("Descriptor: ")
        self.calls.append(raw)
        if callable(self.answers):
            return self.answers(raw)
        answer = self.answers.get(raw, self.answers.get(descriptor_key(raw)))
        if answer is None:
            word = raw.split()[0].strip("*")
            answer = {"merchant": word.title(), "evidence": word, "kind": "other"}
        return answer if isinstance(answer, str) else json.dumps(answer)


def recorded_model() -> FakeModel:
    """Replays the answers gemma4:12b gave for the example statements (tests/recorded_names.json)."""
    data = json.loads(RECORDED.read_text(encoding="utf-8"))
    return FakeModel({key: {k: v for k, v in entry.items() if k != "raw"} for key, entry in data["entries"].items()})


def tx(day: date | str, description: str, amount: str | Decimal, line: int = 0) -> Transaction:
    when = date.fromisoformat(day) if isinstance(day, str) else day
    return Transaction(when, description, Decimal(amount), "test.csv", line)


def monthly(description: str, amount: str, start: str, count: int, *, day_shift: int = 0) -> list[Transaction]:
    from ghostsubs.recur import add_months
    first = date.fromisoformat(start)
    return [tx(add_months(first, i) + timedelta(days=day_shift), description, amount, i) for i in range(count)]


def statement_of(transactions: list[Transaction], *, start: str | None = None, currency: str = "$") -> Statement:
    rows = sorted(transactions, key=lambda t: t.date)
    if start is not None:
        rows.insert(0, tx(start, "OPENING DEPOSIT", "-1.00", -1))
    return Statement(rows, currency=currency, decimal=".", files=["test.csv"])
