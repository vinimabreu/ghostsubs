"""The command line end to end, with a fake model and a fake push server."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeModel

import ghostsubs.cli as cli
import ghostsubs.model as model
import ghostsubs.notify as notify

STATEMENT = ("date,description,amount\n"
             + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 8))
             + "".join(f"2026-{m:02d}-12,SPOTIFY USA,-11.99\n" for m in range(1, 8))
             + "2026-03-20,KROGER #456,-40.10\n2026-07-20,PAYCHECK,2875.00\n")


def use_model(monkeypatch: pytest.MonkeyPatch, fake: FakeModel | None = None) -> FakeModel:
    fake = fake or FakeModel({"NETFLIX.COM": {"merchant": "Netflix", "evidence": "NETFLIX", "kind": "subscription"},
                              "SPOTIFY USA": {"merchant": "Spotify", "evidence": "SPOTIFY", "kind": "subscription"},
                              "KROGER #456": {"merchant": "Kroger", "evidence": "KROGER", "kind": "other"}})
    monkeypatch.setattr(model, "asker", lambda **kwargs: fake)
    return fake


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "statement.csv"
    path.write_text(STATEMENT, encoding="utf-8")
    return path


def test_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], statement: Path,
              tmp_path: Path) -> None:
    fake = use_model(monkeypatch)
    assert cli.main(["line", str(statement), "--cache", str(tmp_path / "c")]) == 0
    assert capsys.readouterr().out.strip() == "2 subscriptions, 27.48/mo."
    assert len(fake.calls) == 3


def test_second_run_uses_the_cache(monkeypatch: pytest.MonkeyPatch, statement: Path, tmp_path: Path) -> None:
    use_model(monkeypatch)
    cli.main(["line", str(statement), "--cache", str(tmp_path / "c")])
    fake = use_model(monkeypatch, FakeModel())
    cli.main(["line", str(statement), "--cache", str(tmp_path / "c")])
    assert fake.calls == []


def test_report_and_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], statement: Path,
                         tmp_path: Path) -> None:
    use_model(monkeypatch)
    cli.main(["report", str(statement), "--cache", str(tmp_path / "c"), "--currency", "$"])
    text = capsys.readouterr().out
    assert 'NETFLIX.COM  <- "NETFLIX"' in text and "$15.49/mo" in text
    cli.main(["line", str(statement), "--cache", str(tmp_path / "c"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["line"].startswith("2 subscriptions") and data["naming"]["cached"] == 3


def test_no_model_never_calls_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                  statement: Path) -> None:
    def boom(**kwargs: Any) -> Any:
        raise AssertionError("model built")

    monkeypatch.setattr(model, "asker", boom)
    assert cli.main(["line", str(statement), "--no-model"]) == 0
    assert capsys.readouterr().out.startswith("2 recurring charges, 27.48/mo")


def test_model_unavailable_says_how_to_go_on(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                            statement: Path, tmp_path: Path) -> None:
    def down(system: str, user: str, schema: Any = None) -> str:
        raise model.ModelUnavailable("Ollama is not reachable at http://127.0.0.1:11434")

    monkeypatch.setattr(model, "asker", lambda **kwargs: down)
    assert cli.main(["line", str(statement), "--cache", str(tmp_path / "c")]) == 2
    assert "--no-model" in capsys.readouterr().err


def test_remote_model_host_is_refused(capsys: pytest.CaptureFixture[str], statement: Path, tmp_path: Path) -> None:
    assert cli.main(["line", str(statement), "--host", "http://10.1.2.3:11434", "--cache", str(tmp_path / "c")]) == 2
    assert "not this machine" in capsys.readouterr().err


def test_ambiguous_dates_ask_for_the_flag(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    path.write_text("date,description,amount\n01/02/2026,A,-1.00\n03/04/2026,B,-2.00\n", encoding="utf-8")
    assert cli.main(["line", str(path), "--no-model"]) == 2
    assert "--date-format" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("ghostsubs 0.1.0")


def test_bad_flags(capsys: pytest.CaptureFixture[str], statement: Path) -> None:
    assert cli.main(["line", str(statement), "--no-model", "--window", "-1"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["line", str(statement), "--today", "yesterday"])
    with pytest.raises(SystemExit):
        cli.main(["line", str(statement), "--tolerance", "80%"])


class PushServer:
    def __init__(self, status: int = 200, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status, self.fail = status, fail

    def open(self, request: Any, timeout: float = 0) -> Any:
        if self.fail:
            raise OSError("no route")
        self.sent.append(json.loads(request.data))
        server = self

        class Response:
            status = server.status

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

        return Response()


def test_watch_speaks_only_when_something_changed(monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    use_model(monkeypatch)
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "2026-07.csv").write_text(STATEMENT, encoding="utf-8")
    args = ["watch", str(folder), "--cache", str(tmp_path / "c")]
    assert cli.main(args) == 0 and capsys.readouterr().out.startswith("2 subscriptions")
    assert cli.main(args) == 0 and capsys.readouterr().out == ""
    newer = folder / "2026-08.csv"
    newer.write_text(STATEMENT + "2026-08-05,NETFLIX.COM,-17.99\n", encoding="utf-8")
    os.utime(newer, (time.time() + 60, time.time() + 60))
    assert cli.main(args) == 0
    assert "Netflix went up 16% (15.49 -> 17.99)" in capsys.readouterr().out


def test_watch_pushes_with_ntfy_and_retries_after_a_failed_push(monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str],
                                                                tmp_path: Path) -> None:
    use_model(monkeypatch)
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "s.csv").write_text(STATEMENT, encoding="utf-8")
    args = ["watch", str(folder), "--cache", str(tmp_path / "c"), "--ntfy", "my-subs-topic-7f3a"]
    down = PushServer(fail=True)
    monkeypatch.setattr(notify, "_opener", lambda: down)
    assert cli.main(args) == 1
    assert "not saved" in capsys.readouterr().err
    up = PushServer()
    monkeypatch.setattr(notify, "_opener", lambda: up)
    assert cli.main(args) == 0
    assert up.sent[0]["topic"] == "my-subs-topic-7f3a" and up.sent[0]["message"].startswith("2 subscriptions")
    assert cli.main(args) == 0 and len(up.sent) == 1


def test_watch_refuses_a_bad_topic_and_an_empty_folder(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert cli.main(["watch", str(tmp_path), "--no-model"]) == 2
    assert "no .csv" in capsys.readouterr().err
    (tmp_path / "a.csv").write_text(STATEMENT, encoding="utf-8")
    assert cli.main(["watch", str(tmp_path), "--no-model", "--ntfy", "bad topic!"]) == 2
    assert cli.main(["watch", str(tmp_path / "nope"), "--no-model"]) == 2


def test_ntfy_answer_above_300_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "_opener", lambda: PushServer(status=429))
    with pytest.raises(notify.NotifyError, match="429"):
        notify.send("topic", "hello")


def test_watch_reads_every_export_in_the_folder(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                tmp_path: Path) -> None:
    lines = (Path(__file__).resolve().parent.parent / "examples" / "bank-a.csv").read_text(encoding="utf-8-sig")
    header, *rows = lines.splitlines()
    folder = tmp_path / "exports"
    folder.mkdir()
    months: dict[str, list[str]] = {}
    for row in rows:
        day, month, year = row.split(";")[0].split("/")
        months.setdefault(f"{year}-{month}", []).append(row)
    for month, items in months.items():
        (folder / f"extrato-{month}.csv").write_text("\n".join([header, *items]) + "\n", encoding="utf-8")
    assert len(months) == 14
    args = ["watch", str(folder), "--no-model", "--cache", str(tmp_path / "c")]
    assert cli.main(args) == 0
    assert capsys.readouterr().out.startswith("10 recurring charges")
    assert cli.main(args) == 0 and capsys.readouterr().out == ""


def test_one_month_says_it_is_too_short(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    path.write_text("date,description,amount\n2026-10-01,NETFLIX.COM,-15.49\n2026-10-12,SPOTIFY,-11.99\n"
                    "2026-10-20,KROGER,-40.10\n", encoding="utf-8")
    assert cli.main(["line", str(path), "--no-model", "--today", "2026-10-21"]) == 0
    assert "A monthly plan needs 3 charges and this covers 19 days" in capsys.readouterr().out


def test_json_without_a_model_has_one_total(capsys: pytest.CaptureFixture[str], statement: Path) -> None:
    assert cli.main(["line", str(statement), "--no-model", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["subscriptions_monthly"] is None and data["recurring_monthly"] == 27.48
    assert {s["counted_as"] for s in data["series"]} == {"recurring"}


def test_watch_skips_a_file_it_cannot_read_and_ignores_dotfiles(monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str],
                                                                tmp_path: Path) -> None:
    use_model(monkeypatch)
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "statement.csv").write_text(STATEMENT, encoding="utf-8")
    (folder / "contacts.csv").write_text("name,email,score\nA,a@example.com,3\n", encoding="utf-8")
    (folder / "._statement.csv").write_bytes(b"\x00\x05\x16\x07 resource fork")
    assert cli.main(["watch", str(folder), "--cache", str(tmp_path / "c")]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("2 subscriptions") and "skipped" in out.err and "contacts.csv" in out.err
    assert "._statement.csv" not in out.err


def test_watch_tells_the_phone_once_when_it_cannot_run(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "contacts.csv").write_text("name,email,score\nA,a@example.com,3\n", encoding="utf-8")
    server = PushServer()
    monkeypatch.setattr(notify, "_opener", lambda: server)
    args = ["watch", str(folder), "--no-model", "--cache", str(tmp_path / "c"), "--ntfy", "topic-4b1c"]
    assert cli.main(args) == 2 and cli.main(args) == 2
    assert [m["message"] for m in server.sent] == [
        "ghostsubs could not run: none of the 1 .csv files in the folder could be read (first: contacts.csv)"]
    assert "a@example.com" not in server.sent[0]["message"]


def test_watch_tells_the_phone_when_the_model_is_down(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def down(system: str, user: str, schema: Any = None) -> str:
        raise model.ModelUnavailable("Ollama is not reachable at http://127.0.0.1:11434; start it with: ollama serve")

    monkeypatch.setattr(model, "asker", lambda **kwargs: down)
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "s.csv").write_text(STATEMENT, encoding="utf-8")
    server = PushServer()
    monkeypatch.setattr(notify, "_opener", lambda: server)
    args = ["watch", str(folder), "--cache", str(tmp_path / "c"), "--ntfy", "topic-4b1c"]
    assert cli.main(args) == 2 and cli.main(args) == 2
    assert len(server.sent) == 1 and "not reachable" in server.sent[0]["message"]


def test_news_is_only_what_was_not_there_before() -> None:
    from ghostsubs.watch import has_news
    before = {"active": [["Netflix", "monthly", "15.49"]], "renewals": [["Adobe", "2026-10-23"]], "new": [["Max"]],
              "possible_trials": []}
    gone = {"active": [["Netflix", "monthly", "15.49"]], "renewals": [], "new": [], "possible_trials": []}
    assert not has_news(before, gone)
    trial = {**gone, "possible_trials": [["HBO Max", "2026-06-17"]]}
    assert has_news(gone, trial) and has_news(None, gone)


def test_watch_speaks_when_a_trial_turns_paid(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    folder = tmp_path / "exports"
    folder.mkdir()
    base = "date,description,amount\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 8))
    (folder / "a.csv").write_text(base, encoding="utf-8")
    args = ["watch", str(folder), "--no-model", "--cache", str(tmp_path / "c"), "--today", "2026-07-20"]
    assert cli.main(args) == 0 and capsys.readouterr().out
    (folder / "b.csv").write_text("date,description,amount\n2026-06-17,DM *HBOMAX,-1.99\n"
                                  "2026-07-17,DM *HBOMAX,-34.90\n", encoding="utf-8")
    assert cli.main(args) == 0
    assert "Possible trial turned paid: Hbomax" in capsys.readouterr().out
    assert cli.main(args) == 0 and capsys.readouterr().out == ""
