"""The one test that talks to a real model. Skipped when Ollama or the model is not on this machine."""

from __future__ import annotations

import pytest

from ghostsubs import model
from ghostsubs.naming import Namer


def _ready() -> bool:
    try:
        return model.digest(model.DEFAULT_MODEL) is not None
    except (model.ModelUnavailable, model.RemoteHostRefused):
        return False


@pytest.mark.real_model
def test_the_local_model_names_descriptors_and_the_check_holds() -> None:
    if not _ready():
        pytest.skip(f"no Ollama with {model.DEFAULT_MODEL} on this machine")
    raws = ["PAYPAL *SPOTIFY 4025550134", "NETFLIX.COM 866-555-0142 CA", "AMZN Mktp US*2K4L19ZX0"]
    names = Namer(model.asker(), model=model.DEFAULT_MODEL).name_all(raws)
    assert names[raws[0]].verified and names[raws[0]].identity == "spotify"
    assert names[raws[1]].verified and names[raws[1]].identity == "netflix"
    for name in names.values():
        assert name.verified or name.reason
