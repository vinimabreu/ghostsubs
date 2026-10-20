"""The model names a descriptor; code checks the name against the descriptor before it is used."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import FakeModel

from ghostsubs.model import ModelUnavailable
from ghostsubs.naming import Answer, NameCache, Namer, check, parse_answer, supports, tidy


def answer(merchant: str, evidence: str, kind: str = "subscription") -> Answer:
    return Answer(merchant, evidence, kind)


@pytest.mark.parametrize("raw, merchant, evidence", [
    ("PAYPAL *SPOTIFY 4025550134", "Spotify", "SPOTIFY"),
    ("NETFLIX.COM 866-555-0142 CA", "Netflix", "NETFLIX.COM"),
    ("GOOGLE *YouTubePremium g.co/helppay#", "YouTube Premium", "YouTubePremium"),
    ("AMZN Mktp US*2K4L19ZX0", "Amazon", "AMZN"),
    ("MSFT *E0300ABCDE", "Microsoft", "MSFT"),
    ("NYTIMES*NYTIMES SUBSCR", "The New York Times", "NYTIMES"),
    ("DISNEY PLU*2K3J4L", "Disney Plus", "DISNEY PLU"),
    ("TED ALUGUEL IMOB HORIZONTE", "Imobiliaria Horizonte", "IMOB HORIZONTE"),
    ("ACH DEBIT OAKWOOD PROPERTY MGMT RENT", "Oakwood Property Management", "OAKWOOD PROPERTY MGMT"),
    ("VERIZON WRLS 555-01 CA", "Verizon Wireless", "VERIZON WRLS"),
    ("DM *HBOMAX", "HBO Max", "HBOMAX"),
    ("HELP.MAX.COM", "HELP.MAX.COM", "HELP.MAX.COM"),
    ("PARAMOUNT+ 855-555-0144", "Paramount+", "PARAMOUNT+"),
    ("AMAZONPRIMEBR", "Amazon Prime", "AMAZONPRIME"),
    ("NETFLIX.COM", "netflix", "netflix.com"),
])
def test_names_the_check_accepts(raw: str, merchant: str, evidence: str) -> None:
    assert check(raw, answer(merchant, evidence)) is None


@pytest.mark.parametrize("raw, merchant, evidence, why", [
    ("PAGTO FATURA CARTAO", "PagSeguro", "PAGTO", "not spelled out"),
    ("PAYPAL *SPOTIFY 4025550134", "PayPal", "PAYPAL", "payment processor"),
    ("NETFLIX.COM", "Netflix", "NETFLIX INC", "not in the descriptor"),
    ("ADOBE *CREATIVE CLOUD 408-555-0122 CA", "Adobe Creative Cloud", "CREATIVE CLOUD", "not spelled out"),
    ("MAXIMUS FOODS", "Max", "MAXIMUS FOODS", "not spelled out"),
    ("HARBOR ED OF NY INTELL CK", "Harbor Edison", "HARBOR ED", "not spelled out"),
    ("SPOTIFY USA", "Spotify Premium", "SPOTIFY", "not spelled out"),
    ("SPOTIFY USA", "", "SPOTIFY", "no name"),
    ("SPOTIFY USA", "Spotify", "", "quoted nothing"),
    ("SPOTIFY USA", "Spotify " * 10, "SPOTIFY", "longer than 48"),
])
def test_names_the_check_refuses(raw: str, merchant: str, evidence: str, why: str) -> None:
    reason = check(raw, answer(merchant, evidence))
    assert reason is not None and why in reason


@pytest.mark.parametrize("merchant, evidence", [
    ("Applebees", "APPLE.COM"),
    ("Ubereats", "UBER"),
    ("Hbomaxima", "HBOMAX"),
    ("Flix", "NETFLIX"),
    ("Pals", "PAYPAL *SPOTIFY"),
])
def test_a_name_made_longer_or_cut_from_inside_a_word_is_refused(merchant: str, evidence: str) -> None:
    assert not supports(merchant, evidence)


@pytest.mark.parametrize("merchant, evidence", [
    ("Netflix Entretenimento", "NETFLIX ENTRETENIMENT"),
    ("Amazon", "AMAZONBR"),
    ("Comcast Cable Communications", "COMCAST CABLE COMM"),
])
def test_words_cut_short_or_glued_to_a_little_more(merchant: str, evidence: str) -> None:
    assert supports(merchant, evidence)


def test_supports_needs_every_word() -> None:
    assert supports("Uber One", "UBER *ONE") and not supports("Uber One Pass", "UBER *ONE")
    assert not supports("Walmart", "WM SUPERCENTER")


def test_tidy_is_formatting_only() -> None:
    assert tidy("HELP.MAX.COM") == "Max" and tidy("VIVO FIBRA") == "Vivo Fibra" and tidy("GEICO") == "GEICO"
    assert tidy("  HBO   Max ") == "HBO Max"


@pytest.mark.parametrize("reply, error", [
    ("not json", "not JSON"),
    ("[1, 2]", "not a JSON object"),
    ('{"merchant": "Spotify"}', "missing"),
    ('{"merchant": "Spotify", "evidence": 3, "kind": "other"}', "missing"),
])
def test_bad_replies(reply: str, error: str) -> None:
    assert error in parse_answer(reply).error


def test_one_call_per_cache_key_and_variants_merge(tmp_path: Path) -> None:
    fake = FakeModel({"PAYPAL *SPOTIFY 4025550134": {"merchant": "Spotify", "evidence": "SPOTIFY",
                                                     "kind": "subscription"},
                      "EBN*SPOTIFY": {"merchant": "Spotify", "evidence": "SPOTIFY", "kind": "subscription"}})
    namer = Namer(fake, model="fake", cache=NameCache(tmp_path, "fake"))
    names = namer.name_all(["PAYPAL *SPOTIFY 4025550134", "PAYPAL *SPOTIFY 4025550199", "EBN*SPOTIFY",
                            "EBN*SPOTIFY"])
    assert len(fake.calls) == 2
    assert {n.identity for n in names.values()} == {"spotify"} and all(n.verified for n in names.values())
    assert namer.stats.asked == 2 and namer.stats.descriptors == 2


def test_cached_answers_skip_the_model_and_are_checked_again(tmp_path: Path) -> None:
    first = FakeModel({"NETFLIX.COM": {"merchant": "Netflix", "evidence": "NETFLIX", "kind": "subscription"}})
    Namer(first, model="m", cache=NameCache(tmp_path, "m")).name_all(["NETFLIX.COM"])
    second = FakeModel()
    namer = Namer(second, model="m", cache=NameCache(tmp_path, "m"))
    names = namer.name_all(["NETFLIX.COM"])
    assert second.calls == [] and names["NETFLIX.COM"].source == "cache" and names["NETFLIX.COM"].verified

    path = NameCache(tmp_path, "m").path
    data = json.loads(path.read_text())
    for entry in data["entries"].values():
        entry["merchant"] = "Hulu"
    path.write_text(json.dumps(data))
    again = Namer(FakeModel(), model="m", cache=NameCache(tmp_path, "m")).name_all(["NETFLIX.COM"])
    assert not again["NETFLIX.COM"].verified and "not spelled out" in again["NETFLIX.COM"].reason


def test_cache_is_per_model_and_private(tmp_path: Path) -> None:
    Namer(FakeModel(), model="gemma4:12b", cache=NameCache(tmp_path, "gemma4:12b")).name_all(["HULU"])
    other = FakeModel()
    Namer(other, model="qwen3:8b", cache=NameCache(tmp_path, "qwen3:8b")).name_all(["HULU"])
    assert other.calls == ["HULU"]
    files = sorted(p.name for p in tmp_path.glob("names-*.json"))
    assert len(files) == 2
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / files[0]).stat().st_mode) == 0o600


def test_a_variant_without_the_quoted_words_is_unverified(tmp_path: Path) -> None:
    fake = FakeModel({"SHOP 1234": {"merchant": "Shop", "evidence": "SHOP 1234", "kind": "other"}})
    names = Namer(fake, model="m").name_all(["SHOP 1234", "SHOP 9876"])
    assert names["SHOP 1234"].verified and not names["SHOP 9876"].verified
    assert len(fake.calls) == 1


def test_a_failed_name_keeps_the_kind_label_and_the_cleanup() -> None:
    fake = FakeModel({"ACH DEBIT OAKWOOD PROPERTY MGMT RENT": {"merchant": "Oakwood Rentals", "evidence": "OAKWOOD",
                                                               "kind": "loan_or_rent"}})
    name = Namer(fake, model="m").name_all(["ACH DEBIT OAKWOOD PROPERTY MGMT RENT"])[
        "ACH DEBIT OAKWOOD PROPERTY MGMT RENT"]
    assert not name.verified and name.kind == "loan_or_rent" and name.name == "Oakwood Property Mgmt Rent"
    assert name.model_said == "Oakwood Rentals"


def test_unknown_kind_and_bad_json_retry() -> None:
    replies = iter(["oops", json.dumps({"merchant": "Hulu", "evidence": "HULU", "kind": "streaming"})])
    fake = FakeModel(lambda raw: next(replies))
    name = Namer(fake, model="m").name_all(["HULU"])["HULU"]
    assert len(fake.calls) == 2 and name.verified and name.kind == "unknown"


def test_two_bad_replies_fall_back_to_cleanup() -> None:
    fake = FakeModel(lambda raw: "still not json")
    name = Namer(fake, model="m").name_all(["NETFLIX.COM 866-555-0142 CA"])["NETFLIX.COM 866-555-0142 CA"]
    assert not name.verified and name.name == "Netflix" and "not JSON" in name.reason


def test_no_model_means_cleanup_only() -> None:
    name = Namer(None).name_all(["PAYPAL *SPOTIFY 4025550134"])["PAYPAL *SPOTIFY 4025550134"]
    assert name.name == "Spotify" and name.source == "cleanup" and not name.verified


def test_model_going_away_keeps_what_was_cached(tmp_path: Path) -> None:
    def flaky(raw: str) -> str:
        if raw == "HULU":
            raise ModelUnavailable("gone")
        return json.dumps({"merchant": "Netflix", "evidence": "NETFLIX", "kind": "subscription"})

    cache = NameCache(tmp_path, "m")
    with pytest.raises(ModelUnavailable):
        Namer(FakeModel(flaky), model="m", cache=cache).name_all(["NETFLIX.COM", "HULU"])
    assert len(NameCache(tmp_path, "m")) == 1


def test_an_existing_cache_folder_keeps_its_permissions(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("permissions are POSIX")
    mine = tmp_path / "mine"
    mine.mkdir(mode=0o755)
    os.chmod(mine, 0o755)
    Namer(FakeModel(), model="m", cache=NameCache(mine, "m")).name_all(["HULU"])
    assert stat.S_IMODE(mine.stat().st_mode) == 0o755
    fresh = tmp_path / "new" / "ghostsubs"
    Namer(FakeModel(), model="m", cache=NameCache(fresh, "m")).name_all(["HULU"])
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700
