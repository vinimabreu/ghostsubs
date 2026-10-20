"""Push the line to a phone with ntfy, the open-source push service.

Apart from ``--allow-remote-model`` (which sends descriptors to the model you
point it at), this is the only thing that leaves the machine, and only when you
pass ``--ntfy``. What goes is the line: merchant names (which can be a person
you pay by transfer, or a cleaned-up descriptor) and amounts, or a short note
when a scheduled run could not finish. On ntfy.sh a
topic has no password, so anyone who knows its name can read it. Pick a random
name (``openssl rand -hex 16``), or run your own ntfy server and pass
``--ntfy-server``. Redirects are not followed.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Protocol

DEFAULT_SERVER = "https://ntfy.sh"
_TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")


class NotifyError(RuntimeError):
    pass


class Opener(Protocol):
    def open(self, fullurl: Any, data: Any = ..., timeout: float | None = ...) -> Any: ...


def valid_topic(topic: str) -> bool:
    return bool(_TOPIC.match(topic or ""))


def message(topic: str, text: str) -> dict[str, Any]:
    return {"topic": topic, "title": "ghostsubs", "message": text, "tags": ["moneybag"], "priority": 3}


def _opener() -> Opener:
    """Proxy settings are honoured (the push goes to the internet anyway); redirects are not."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.UnknownHandler(), urllib.request.HTTPHandler(),
                    urllib.request.HTTPSHandler(), urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def send(topic: str, text: str, *, server: str = DEFAULT_SERVER, opener: Opener | None = None) -> None:
    """Publish with ntfy's JSON body, which carries any character in the message without header encoding."""
    if not valid_topic(topic):
        raise NotifyError("an ntfy topic is 1 to 64 letters, digits, - or _")
    body = json.dumps(message(topic, text), ensure_ascii=False).encode()
    request = urllib.request.Request(server.rstrip("/") + "/", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with (opener or _opener()).open(request, timeout=20) as response:
            status = getattr(response, "status", 200)
            if status >= 300:
                raise NotifyError(f"ntfy answered {status}")
    except NotifyError:
        raise
    except Exception as error:  # urllib raises a handful of types for one failed POST
        raise NotifyError(f"could not reach ntfy at {server}: {error}") from error
