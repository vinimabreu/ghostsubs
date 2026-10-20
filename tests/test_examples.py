"""The example statements, end to end, with the answers gemma4:12b gave for them replayed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import EXAMPLES, ROOT, recorded_model

import ghostsubs.cli as cli
import ghostsubs.model as model
from ghostsubs.clean import identity
from ghostsubs.naming import Namer
from ghostsubs.recur import analyze
from ghostsubs.statement import read_files

FILES = ["bank-a.csv", "card-b.csv", "export-c.csv", "bank-d.csv"]

# The output shown in the README, from these files and the recorded answers.
LINES = {
    "bank-a.csv": ("7 subscriptions, R$ 267,35/mo (other recurring: R$ 2.659,39/mo). Netflix went up 13% "
                   "(R$ 39,90 -> R$ 44,90). New: HBO Max (trial R$ 1,99, now R$ 34,90 monthly). No charge from "
                   "Globoplay since 2026-08-08 (cancelled?)."),
    "card-b.csv": ("13 subscriptions, 191.96/mo (other recurring: 217.40/mo). Spotify went up 8% (11.99 -> 12.99). "
                   "Adobe Creative Cloud renews in 9 days: 239.88. New: Audible (trial 0.99, now 14.95 monthly). "
                   "No charge from Max since 2026-08-15 (cancelled?)."),
    "export-c.csv": ("5 subscriptions, R$ 117,01/mo (other recurring: R$ 17,99/mo). Crunchyroll went up 33% "
                     "(R$ 14,99 -> R$ 19,99)."),
    "bank-d.csv": ("1 subscription, 41.99/mo (other recurring: 3,152.57/mo). No charge from SiriusXM since "
                   "2026-07-23 (cancelled?)."),
}

# Without the model: the same schedules, worse names, and one plan per descriptor variant.
NO_MODEL = {
    "bank-a.csv": ("10 recurring charges, R$ 2.926,74/mo. Netflix Entretenimento went up 13% (R$ 39,90 -> "
                   "R$ 44,90). New: Hbomax (trial R$ 1,99, now R$ 34,90 monthly). No charge from Globoplay since "
                   "2026-08-08 (cancelled?)."),
    "bank-d.csv": "6 recurring charges, 3,194.56/mo. No charge from Sxm Siriusxm since 2026-07-23 (cancelled?).",
}

# Names the recorded answers got wrong or cut short, though cadence and amount are right.
NAME_MISSES = {"bank-a.csv": ["Aluguel Imob Horizonte"], "card-b.csv": ["Planet Fit", "Planet Fit"], "export-c.csv": [], "bank-d.csv": ["Quillmont Auto Financial Svcs", "SAV"]}


@pytest.mark.parametrize("name", FILES)
def test_the_line_for_each_example(name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                   tmp_path: Path) -> None:
    fake = recorded_model()
    monkeypatch.setattr(model, "asker", lambda **kwargs: fake)
    assert cli.main(["line", str(EXAMPLES / name), "--cache", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == LINES[name]


@pytest.mark.parametrize("name", sorted(NO_MODEL))
def test_the_line_without_a_model(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["line", str(EXAMPLES / name), "--no-model"]) == 0
    assert capsys.readouterr().out.strip() == NO_MODEL[name]


@pytest.mark.parametrize("name", FILES)
def test_every_planted_series_is_found_with_the_recorded_names(name: str) -> None:
    truth = json.loads((EXAMPLES / "truth" / "statements.json").read_text(encoding="utf-8"))[name]
    statement = read_files([EXAMPLES / name])
    names = Namer(recorded_model(), model="recorded").name_all(t.description for t in statement.charges)
    analysis = analyze(statement, names, model="recorded")
    assert len(analysis.series) == len(truth["series"])
    misses = []
    for expected in truth["series"]:
        same = [s for s in analysis.series if s.cadence.name == expected["cadence"]
                and f"{s.amount:.2f}" == expected["amount"] and s.missing == (expected["status"] == "missing")]
        assert same, expected
        if not any(identity(s.merchant) in {identity(a) for a in expected["merchant"]} for s in same):
            misses.append(same[0].merchant)
    assert sorted(misses) == NAME_MISSES[name]


def test_the_generator_reproduces_the_examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("make_examples", ROOT / "scripts" / "make_examples.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "make_examples", module)  # dataclasses look their module up there
    spec.loader.exec_module(module)
    module.main(tmp_path)
    for name in FILES + ["truth/statements.json"]:
        assert (tmp_path / name).read_bytes() == (EXAMPLES / name).read_bytes(), name


def _generator(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("make_examples", ROOT / "scripts" / "make_examples.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "make_examples", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_other_seeds_report_nothing_that_was_not_planted(seed: int, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Same plans, other dates, amounts and decoys; names from the recorded answers."""
    _generator(monkeypatch).main(tmp_path, seed)
    truth = json.loads((tmp_path / "truth" / "statements.json").read_text(encoding="utf-8"))
    fake = recorded_model()
    for name in FILES:
        statement = read_files([tmp_path / name])
        names = Namer(fake, model="recorded").name_all(t.description for t in statement.charges)
        analysis = analyze(statement, names, model="recorded")
        planted = [(e["cadence"], e["amount"], e["status"] == "missing") for e in truth[name]["series"]]
        reported = [(s.cadence.name, f"{s.amount:.2f}", s.missing) for s in analysis.series]
        assert sorted(reported) == sorted(planted), (seed, name)
        assert analysis.possible_trials == []
