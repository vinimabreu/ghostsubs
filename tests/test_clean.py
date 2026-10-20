"""Code cleanup of descriptors: the fallback name, the cache key and merchant identity."""

from __future__ import annotations

import pytest

from ghostsubs.clean import cleaned_name, descriptor_key, identity, processor_hidden_merchant


@pytest.mark.parametrize("raw, expected", [
    ("PAYPAL *SPOTIFY 4025550134", "Spotify"),
    ("EBN*SPOTIFY", "Spotify"),
    ("NETFLIX.COM 866-555-0142 CA", "Netflix"),
    ("NETFLIX.COM          LOS GATOS    CA", "Netflix"),
    ("APPLE.COM/BILL 866-555-0177 CA", "Apple"),
    ("GOOGLE *YouTubePremium g.co/helppay#", "YouTubePremium"),
    ("UBER *TRIP HELP.UBER.COM", "Uber Trip"),
    ("HELP.MAX.COM", "Max"),
    ("COSTCO WHSE #0482", "Costco Whse"),
    ("Amazon.com*MK1Q73", "Amazon"),
    ("PURCHASE AUTHORIZED ON 03/14 WHOLEFDS MKT 10234 AUSTIN TX", "Wholefds Mkt"),
    ("DEB AUTOM SMART FIT", "Smart Fit"),
    ("TRANSFER TO SAV XXXXXX4821", "Transfer To Sav"),
    ("LYFT *RIDE TUE 5PM", "Lyft Ride"),
    ("7-ELEVEN 34012", "7-Eleven"),
    ("SQ *", "Square"),
])
def test_cleanup(raw: str, expected: str) -> None:
    assert cleaned_name(raw) == expected


def test_cleanup_never_returns_nothing() -> None:
    assert cleaned_name("**** 1234") and cleaned_name("") == "?"


def test_cache_key_ignores_case_spacing_digits_and_references() -> None:
    assert descriptor_key("AMZN Mktp US*2K4L19ZX0") == descriptor_key("amzn mktp us*RT5Y21QX8")
    assert descriptor_key("PAYPAL *SPOTIFY 4025550134") == descriptor_key("PAYPAL  *SPOTIFY 4025550199")
    assert descriptor_key("NETFLIX.COM") != descriptor_key("HULU")


@pytest.mark.parametrize("a, b", [
    ("Disney+", "Disney Plus"),
    ("Amazon.com", "Amazon"),
    ("The New York Times", "New York Times"),
    ("AT&T", "AT and T"),
    ("Spotify AB", "Spotify Ab") ,
])
def test_identity_merges_spellings(a: str, b: str) -> None:
    assert identity(a) == identity(b)


def test_identity_keeps_different_merchants_apart() -> None:
    assert identity("Max") != identity("HBO Max") and identity("Amazon Prime") != identity("Amazon")


def test_processor_with_a_merchant_after_it() -> None:
    assert processor_hidden_merchant("PAYPAL *SPOTIFY 4025550134") == "paypal"
    assert processor_hidden_merchant("APPLE.COM/BILL 866-555-0177 CA") is None
    assert processor_hidden_merchant("NETFLIX.COM") is None
