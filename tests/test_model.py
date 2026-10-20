"""The model client talks to this machine only, with no proxy, at temperature 0."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

import ghostsubs.model as model
import ghostsubs.notify as notify

REAL_CALL = model._call
REAL_NOTIFY_OPENER = notify._opener


class FakeOpener:
    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self.reply = reply
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return io.BytesIO(json.dumps(self.reply).encode())


@pytest.mark.parametrize("host", ["http://10.0.0.5:11434", "https://ollama.example.com", "192.168.1.20"])
def test_a_host_off_this_machine_is_refused(host: str) -> None:
    with pytest.raises(model.RemoteHostRefused):
        model.chat("s", "u", host=host)


@pytest.mark.parametrize("host", ["http://127.0.0.1:11434", "localhost:11434", "http://[::1]:11434", "0.0.0.0"])
def test_loopback_hosts(host: str) -> None:
    assert model.is_loopback(host)


def test_the_opener_ignores_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    assert urllib.request.ProxyHandler().proxies  # what a default opener would pick up
    assert not any(getattr(h, "proxies", None) for h in model._OPENER.handlers)


def test_the_request_asks_for_json_at_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = FakeOpener({"message": {"content": '{"merchant": "Hulu"}'}})
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", opener)
    reply = model.asker(model="gemma4:12b")("system", "Descriptor: HULU", {"type": "object"})
    body = json.loads(opener.requests[0].data)
    assert reply == '{"merchant": "Hulu"}'
    assert body["options"]["temperature"] == 0 and body["format"] == {"type": "object"} and body["think"] is False
    assert opener.requests[0].full_url == "http://127.0.0.1:11434/api/chat"


@pytest.mark.parametrize("error, message", [
    (urllib.error.URLError("refused"), "not reachable"),
    (urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"model 'x' not found")), "ollama pull"),
    (urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"oops")), "answered 500"),
])
def test_ollama_errors_become_one_clear_message(monkeypatch: pytest.MonkeyPatch, error: Exception,
                                               message: str) -> None:
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", FakeOpener(error))
    with pytest.raises(model.ModelUnavailable, match=message):
        model.chat("s", "u")


def test_digest_names_the_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", FakeOpener({"models": [{"name": "gemma4:12b", "digest": "6114515d63c1"}]}))
    assert model.digest("gemma4:12b") == "6114515d63c1"


REAL_CHECK = model.check_local_model


class ShowServer(FakeOpener):
    def open(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.reply).encode())


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud",
                                  "gemma3:cloud"])
def test_a_cloud_model_is_refused_by_name(name: str) -> None:
    with pytest.raises(model.RemoteHostRefused, match="cloud"):
        REAL_CHECK(name, "http://127.0.0.1:11434")


def test_a_model_ollama_forwards_elsewhere_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", ShowServer({"remote_host": "https://ollama.com:443",
                                                      "remote_model": "gpt-oss:120b"}))
    with pytest.raises(model.RemoteHostRefused, match="ollama.com"):
        REAL_CHECK("my-alias", "http://127.0.0.1:11434")
    REAL_CHECK("my-alias", "http://127.0.0.1:11434", allow_remote=True)


def test_a_local_model_passes_and_is_checked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    server = ShowServer({"details": {"family": "gemma4"}, "message": {"content": "{}"}})
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", server)
    monkeypatch.setattr(model, "check_local_model", REAL_CHECK)
    ask = model.asker(model="gemma4:12b")
    ask("s", "u", None)
    ask("s", "u", None)
    paths = [r.full_url.rsplit("/", 1)[-1] for r in server.requests]
    assert paths == ["show", "chat", "chat"]


@pytest.mark.parametrize("host, url", [("::1", "http://[::1]:11434"), ("[::1]:11434", "http://[::1]:11434"),
                                       ("localhost", "http://localhost:11434")])
def test_addresses(host: str, url: str) -> None:
    assert model.base_url(host) == url


@pytest.mark.parametrize("host", ["localhost:abc", "http://", "ftp://127.0.0.1"])
def test_a_bad_address_is_a_clear_error(host: str) -> None:
    with pytest.raises(model.ModelUnavailable, match="can't read the Ollama address"):
        model.chat("s", "u", host=host)


def test_no_redirects_are_followed() -> None:
    for opener in (model._OPENER, REAL_NOTIFY_OPENER()):
        assert not any(isinstance(h, urllib.request.HTTPRedirectHandler) for h in opener.handlers)


def test_a_broken_ipv6_address_is_a_clear_error() -> None:
    with pytest.raises(model.ModelUnavailable, match="can't read the Ollama address"):
        model.chat("s", "u", host="http://[::1")


class NotJson(FakeOpener):
    def open(self, request: Any, timeout: float = 0) -> Any:
        return io.BytesIO(b"<html>not ollama</html>")


def test_a_server_that_does_not_answer_json_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model, "_call", REAL_CALL)
    monkeypatch.setattr(model, "_OPENER", NotJson({}))
    with pytest.raises(model.ModelUnavailable, match="not JSON"):
        model.chat("s", "u")
