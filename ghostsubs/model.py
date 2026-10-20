"""The only door to a language model: Ollama on this machine.

The client below refuses any Ollama host that is not loopback unless you pass
``allow_remote=True`` on purpose. Its opener has no proxy handler and no
redirect handler, so neither an ``http_proxy`` variable nor a redirect can carry
a descriptor elsewhere. A local Ollama can also serve cloud models (names ending
in ``-cloud`` or ``:cloud``) that it forwards to ollama.com; those are refused
the same way, by name and by the ``remote_host`` field Ollama reports for them.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

DEFAULT_MODEL = os.environ.get("GHOSTSUBS_MODEL", "gemma4:12b")
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# ask(system, user, schema) -> the model's reply text
Ask = Callable[[str, str, dict[str, Any] | None], str]


class ModelUnavailable(RuntimeError):
    """Ollama is not running, or the model is not pulled."""


class RemoteHostRefused(ValueError):
    """The configured host is not this machine."""


def base_url(host: str) -> str:
    text = (host or "").strip()
    if text.count(":") >= 2 and "[" not in text and "://" not in text:
        text = f"[{text}]"  # a bare IPv6 address such as ::1
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        port = parts.port or 11434
    except ValueError as error:
        raise ModelUnavailable(f"can't read the Ollama address {host!r}: use host:port, "
                               "for example 127.0.0.1:11434") from error
    name = parts.hostname or ""
    if not name or parts.scheme not in ("http", "https"):
        raise ModelUnavailable(f"can't read the Ollama address {host!r}: use host:port, "
                               "for example 127.0.0.1:11434")
    if ":" in name:
        name = f"[{name}]"
    return f"{parts.scheme}://{name}:{port}"


def is_loopback(host: str) -> bool:
    """localhost, 127.0.0.0/8 and ::1, plus 0.0.0.0 and ::, which Ollama uses as a
    listen address and which a client on the same machine reaches locally."""
    name = urlsplit(base_url(host)).hostname or ""
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _build_opener() -> urllib.request.OpenerDirector:
    """HTTP only to the address given: no proxy, no redirects (a 3xx is an error)."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler({}), urllib.request.UnknownHandler(), urllib.request.HTTPHandler(),
                    urllib.request.HTTPSHandler(), urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


_OPENER = _build_opener()


def _call(host: str, path: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base_url(host) + path, data=data,
                                     headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        if error.code == 404:
            raise ModelUnavailable(f"model not found on Ollama ({detail[:120]}); pull it with: ollama pull "
                                   f"{(body or {}).get('model', '<model>')}") from error
        raise ModelUnavailable(f"Ollama answered {error.code}: {detail[:200]}") from error
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as error:
        raise ModelUnavailable(f"Ollama is not reachable at {host}; start it with: ollama serve") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ModelUnavailable(f"the server at {host} did not answer like Ollama (the reply was not JSON)") from error
    if not isinstance(payload, dict):
        raise ModelUnavailable(f"the server at {host} did not answer like Ollama")
    if payload.get("error"):
        raise ModelUnavailable(str(payload["error"]))
    return payload


def chat(system: str, user: str, *, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST,
         allow_remote: bool = False, schema: dict[str, Any] | None = None, temperature: float = 0.0,
         timeout: float = 300) -> str:
    """One non-streaming chat turn at temperature 0. With ``schema`` Ollama constrains
    the reply to that JSON schema."""
    if not allow_remote and not is_loopback(host):
        raise RemoteHostRefused(f"{host} is not this machine; a statement stays local unless you pass "
                                "--allow-remote-model")
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if schema is not None:
        body["format"] = schema
    payload = _call(host, "/api/chat", body, timeout)
    message = payload.get("message")
    return str(message.get("content", "")).strip() if isinstance(message, dict) else ""


def is_cloud_name(model: str) -> bool:
    name = (model or "").strip().casefold()
    return name.endswith("-cloud") or name.endswith(":cloud")


def check_local_model(model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, allow_remote: bool = False) -> None:
    """Refuse a model that Ollama would forward off this machine, unless ``allow_remote``."""
    if allow_remote:
        return
    if not is_loopback(host):
        raise RemoteHostRefused(f"{host} is not this machine; a statement stays local unless you pass "
                                "--allow-remote-model")
    if is_cloud_name(model):
        raise RemoteHostRefused(f"{model} is a cloud model: Ollama would send every descriptor to its servers. "
                                "Use a local model, or pass --allow-remote-model")
    info = _call(host, "/api/show", {"model": model}, 30)
    remote = info.get("remote_host") or info.get("remote_model")
    if remote:
        raise RemoteHostRefused(f"{model} runs on {remote}, not on this machine; use a local model, or pass "
                                "--allow-remote-model")


def asker(*, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, allow_remote: bool = False) -> Ask:
    """A callable ``ask(system, user, schema)`` bound to one model and one host. Before the
    first question it checks that the model runs here (see ``check_local_model``)."""
    checked = False

    def ask(system: str, user: str, schema: dict[str, Any] | None = None) -> str:
        nonlocal checked
        if not checked:
            check_local_model(model, host, allow_remote)
            checked = True
        return chat(system, user, model=model, host=host, allow_remote=allow_remote, schema=schema)
    return ask


def digest(model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, allow_remote: bool = False) -> str | None:
    """The digest Ollama reports for ``model``, so a measurement names the exact weights."""
    if not allow_remote and not is_loopback(host):
        raise RemoteHostRefused(f"{host} is not this machine")
    payload = _call(host, "/api/tags", None, 30)
    for entry in payload.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            value = entry.get("digest")
            return str(value) if value else None
    return None
