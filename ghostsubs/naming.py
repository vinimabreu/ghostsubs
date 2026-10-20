"""Name each charge descriptor with a local model, then check the name against the descriptor.

The model gets one descriptor at a time and returns three things: the merchant,
the piece of the descriptor that names it (the evidence), and a kind. Code then
keeps the name only when:

- the evidence is really in the descriptor (case and runs of spaces aside);
- the merchant is spelled out in the evidence: every word of the name is a
  word of the evidence, a word the descriptor cut short or glued to a little
  more (keeping three quarters of it: ``ENTRETENIMENT``), part of a run of
  words glued together read from the start of a word (``YouTubePremium``), a
  standard abbreviation from ``ABBREVIATIONS`` (``MGMT``, ``SVCS``, ``IMOB``),
  or one of the five brand abbreviations in ``ALIASES`` (``AMZN``);
- the merchant is not the payment processor when a merchant follows it
  (``PAYPAL *SPOTIFY`` is Spotify, not PayPal).

A name that fails is never used. The descriptor cleaned up by code is shown
instead, marked UNVERIFIED, with the reason. The model never gives a number
anything: amounts, dates and cadence all come from the statement and from code.

Answers are cached per model and prompt version. The check runs again on every
use, so a stricter check applies to old answers too.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clean import (FILLER, PROCESSOR_NAMES, cleaned_name, descriptor_key, display, identity,
                    processor_hidden_merchant, squash, tokens)
from .model import Ask

PROMPT_ID = "names-v2"
KINDS = ("subscription", "bill", "loan_or_rent", "other")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "merchant": {"type": "string"},
        "evidence": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
    },
    "required": ["merchant", "evidence", "kind"],
}

# No brand appears in here on purpose: an example answer in a prompt is an answer
# the model reaches for when it is unsure.
SYSTEM = (
    "You read one line of a bank or credit card statement: the descriptor printed for a charge. Say which "
    "business or service charged it.\n"
    "A descriptor often starts with a payment processor or a bank label (PayPal, Google, Apple, Square, "
    "PagSeguro, Mercado Pago, EBANX, dLocal, or words such as purchase, debit, compra) and often ends with "
    "noise: reference codes, phone numbers, store numbers, dates, a city, a state or a country. None of that is "
    "the merchant. When a processor is followed by another name, the merchant is that other name. When nothing "
    "but noise follows the processor, the processor is the merchant.\n"
    "merchant: the business or service, written the way that business writes its own name. Use only words the "
    "descriptor contains: complete a word the descriptor cut short or abbreviated, and expand an abbreviation of "
    "the brand itself, but never add a product, plan or company word the descriptor does not contain.\n"
    "evidence: the shortest piece of the descriptor that contains every word of the merchant name, copied "
    "character for character.\n"
    "kind: subscription for a service paid on a schedule (streaming, music, software, cloud storage, apps, news, "
    "memberships, gyms); bill for utilities, phone, internet, pay TV and insurance; loan_or_rent for rent, "
    "mortgage, loan or financing installments; other for everything else (shops, restaurants, groceries, rides, "
    "travel, transfers, cash)."
)

# Words card networks shorten to fit a descriptor in about 22 characters. An entry turns one
# word of the evidence into one full word; none of them is a brand.
ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "mgmt": ("management",), "mgt": ("management",), "svcs": ("services",), "svc": ("service", "services"),
    "comm": ("communications", "communication"), "comms": ("communications",),
    "srvcs": ("services",), "ins": ("insurance",), "insur": ("insurance",), "wrls": ("wireless",),
    "whse": ("wholesale", "warehouse"), "whsl": ("wholesale",), "mktp": ("marketplace",), "mkt": ("market",),
    "mkts": ("markets",), "intl": ("international",), "natl": ("national",), "corp": ("corporation",),
    "assn": ("association",), "assoc": ("association",), "univ": ("university",), "ctr": ("center", "centre"),
    "dept": ("department",), "pmt": ("payment",), "pymt": ("payment",), "subscr": ("subscription",),
    "memb": ("membership",), "fin": ("financial", "finance"), "finl": ("financial",),
    "util": ("utility", "utilities"), "utils": ("utilities",), "elec": ("electric", "electricity"),
    "prop": ("property",), "props": ("properties",), "apts": ("apartments",), "apt": ("apartment",),
    "entmt": ("entertainment",), "pharm": ("pharmacy",), "phcy": ("pharmacy",), "rest": ("restaurant",),
    "distrib": ("distribuicao", "distribution"), "imob": ("imobiliaria",), "drog": ("drogaria",),
    "supermerc": ("supermercado",), "financ": ("financiamento", "financing"), "hab": ("habitacao",),
}

# Abbreviations a brand prints for itself on card statements where its name is not spelled
# out at all. A closed list in code: each one must still be a word of the evidence.
ALIASES = {
    "amzn": "amazon",
    "msft": "microsoft",
    "nyt": "newyorktimes",
    "nytimes": "newyorktimes",
    "wsj": "wallstreetjournal",
}


def make_private_dir(directory: Path) -> None:
    """Create ``directory`` readable only by you. A folder that already exists (one you pass
    with --cache) keeps its permissions; only the files written into it are made private."""
    if directory.is_dir():
        return
    missing = [directory]
    while not missing[-1].parent.exists() and missing[-1].parent != missing[-1]:
        missing.append(missing[-1].parent)
    directory.mkdir(parents=True, exist_ok=True)
    for created in missing:
        try:
            os.chmod(created, 0o700)
        except OSError:
            pass


def default_cache_dir() -> Path:
    if os.environ.get("GHOSTSUBS_CACHE"):
        return Path(os.environ["GHOSTSUBS_CACHE"]).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "ghostsubs"


@dataclass(frozen=True)
class Answer:
    merchant: str
    evidence: str
    kind: str
    error: str = ""


@dataclass(frozen=True)
class Name:
    raw: str
    name: str
    identity: str
    verified: bool
    kind: str  # one of KINDS (the model's label), or "unknown" when there is no model answer
    evidence: str
    reason: str  # why the model's name was not used; empty when verified
    source: str  # "model", "cache" or "cleanup"
    model_said: str = ""  # the rejected name, when there was one


COVER = 0.75  # a word cut short, or glued to more letters, must keep three quarters of its length


def _read_off(glued: str, tokens_list: list[str]) -> bool:
    """``glued`` can be read off the evidence from the start of one of its words, ending where a
    word ends or after three quarters of the last word it reaches. ``YouTubePremium`` gives
    YouTube Premium and ``AMAZONPRIMEBR`` gives Amazon Prime; ``NETFLIX`` does not give Flix
    and ``HBOMAX`` does not give Hbomaxima."""
    for start in range(len(tokens_list)):
        read = ""
        for token in tokens_list[start:]:
            before = len(read)
            read += token
            if len(read) < len(glued):
                if not glued.startswith(read):
                    break
                continue
            if read.startswith(glued):
                covered = len(glued) - before
                if covered == len(token) or covered >= COVER * len(token):
                    return True
            break
    return False


def _word_spelled(word: str, tokens_list: list[str], whole: set[str]) -> bool:
    if word in whole:
        return True
    for token in tokens_list:
        if len(token) >= 4 and word.startswith(token) and len(token) >= COVER * len(word):
            return True  # the descriptor cut the word short: ENTRETENIMENT
        if len(word) >= 4 and token.startswith(word) and len(word) >= COVER * len(token):
            return True  # the word glued to a little more: AMAZONBR
    return False


def supports(merchant: str, evidence: str) -> bool:
    """True when every word of ``merchant`` is spelled out in ``evidence``: as a word of it, as a
    word the descriptor cut short or glued to a little more (by three quarters of its length at
    least), as part of words glued together, or through ``ABBREVIATIONS`` and ``ALIASES``."""
    words = [w for w in tokens(merchant) if w not in FILLER]
    ev_tokens = tokens(evidence)
    if not words or not ev_tokens:
        return False
    glued = "".join(words)
    aliased = [ALIASES.get(t, t) for t in ev_tokens]
    whole = set(ev_tokens) | set(aliased) | {full for t in ev_tokens for full in ABBREVIATIONS.get(t, ())}
    if len(glued) >= 4 and (_read_off(glued, ev_tokens) or _read_off(glued, aliased)):
        return True
    # A run of evidence words that the descriptor cut short: "DISNEYPLU" for Disney Plus.
    for start in range(len(ev_tokens)):
        span = ""
        for token in ev_tokens[start:]:
            span += token
            if len(span) >= 5 and glued.startswith(span) and len(span) >= COVER * len(glued):
                return True
    return all(_word_spelled(word, ev_tokens, whole) for word in words)


_DOMAIN_NAME = re.compile(r"^(?:(?:www|help|support|pay|billing)\.)?([A-Za-z0-9-]+)\.(?:com|net|org|io|tv|co|app|ai)"
                          r"(?:\.br)?(?:/\S*)?$", re.I)


def tidy(merchant: str) -> str:
    """Formatting only: a name given as a web address becomes the site's name (``HELP.MAX.COM``
    is Max), and a shouted name of two or more words is written in title case."""
    name = re.sub(r"\s+", " ", merchant or "").strip()
    domain = _DOMAIN_NAME.match(name)
    if domain:
        name = domain.group(1)
        if name.isupper() or name.islower():
            name = name.capitalize()
    if name.isupper() and " " in name:
        name = display(name)
    return name


def check(raw: str, answer: Answer) -> str | None:
    """None when the model's name can be used for ``raw``, else the reason it can't."""
    if answer.error:
        return answer.error
    merchant, evidence = tidy(answer.merchant), answer.evidence.strip()
    if not merchant:
        return "the model gave no name"
    if len(merchant) > 48:
        return "the name is longer than 48 characters"
    if not evidence:
        return "the model quoted nothing from the descriptor"
    if squash(evidence) not in squash(raw):
        return f'the quote "{evidence}" is not in the descriptor'
    if not supports(merchant, evidence):
        return f'"{merchant}" is not spelled out in "{evidence}"'
    processor = processor_hidden_merchant(raw)
    if processor and identity(merchant) in PROCESSOR_NAMES.get(processor, frozenset()):
        return f'"{merchant}" is the payment processor; a merchant name follows it'
    return None


def parse_answer(text: str) -> Answer:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return Answer("", "", "", error="the reply was not JSON")
    if not isinstance(data, dict):
        return Answer("", "", "", error="the reply was not a JSON object")
    values = {key: data.get(key) for key in ("merchant", "evidence", "kind")}
    if not all(isinstance(value, str) for value in values.values()):
        return Answer("", "", "", error="the reply is missing merchant, evidence or kind")
    return Answer(values["merchant"] or "", values["evidence"] or "", values["kind"] or "")


def ask_model(ask: Ask, raw: str) -> Answer:
    """One descriptor, one call; a reply that isn't the JSON asked for gets one more try."""
    answer = parse_answer(ask(SYSTEM, f"Descriptor: {raw}", SCHEMA))
    if answer.error:
        answer = parse_answer(ask(SYSTEM, f"Descriptor: {raw}", SCHEMA))
    return answer


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "model"


class NameCache:
    """The model's answers on disk, one file per model and prompt version, readable only by you."""

    def __init__(self, directory: Path, model: str) -> None:
        self.directory = Path(directory)
        self.model = model
        self.path = self.directory / f"names-{_slug(model)}-{PROMPT_ID}.json"
        self._entries: dict[str, dict[str, str]] | None = None

    def _load(self) -> dict[str, dict[str, str]]:
        if self._entries is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                entries = data.get("entries", {}) if isinstance(data, dict) else {}
                self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
            except (OSError, json.JSONDecodeError):
                self._entries = {}
        return self._entries

    def get(self, key: str) -> Answer | None:
        entry = self._load().get(key)
        if entry is None:
            return None
        return Answer(str(entry.get("merchant", "")), str(entry.get("evidence", "")), str(entry.get("kind", "")),
                      str(entry.get("error", "")))

    def put(self, key: str, raw: str, answer: Answer) -> None:
        entry = {"raw": raw, "merchant": answer.merchant, "evidence": answer.evidence, "kind": answer.kind}
        if answer.error:
            entry["error"] = answer.error
        self._load()[key] = entry
        self.save()

    def __len__(self) -> int:
        return len(self._load())

    def save(self) -> None:
        make_private_dir(self.directory)
        payload = {"model": self.model, "prompt": PROMPT_ID, "entries": self._load()}
        handle, temporary = tempfile.mkstemp(dir=self.directory, prefix=".names-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=1, sort_keys=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


@dataclass
class NamingStats:
    descriptors: int = 0  # distinct after the cache key: one model question each
    spellings: int = 0  # distinct descriptors as printed
    asked: int = 0
    cached: int = 0
    verified: int = 0  # spellings whose name passed the check
    unverified: int = 0


class Namer:
    """Names descriptors. ``ask`` is the model (None: code cleanup only)."""

    def __init__(self, ask: Ask | None, *, model: str = "", cache: NameCache | None = None,
                 progress: Callable[[int, int, str], None] | None = None) -> None:
        self.ask = ask
        self.model = model
        self.cache = cache
        self.progress = progress
        self.stats = NamingStats()

    def name_all(self, raws: Iterable[str]) -> dict[str, Name]:
        unique = list(dict.fromkeys(raws))
        representative: dict[str, str] = {}
        for raw in unique:
            representative.setdefault(descriptor_key(raw), raw)
        answers: dict[str, tuple[Answer, str]] = {}
        if self.ask is not None:
            todo = []
            for key, raw in representative.items():
                cached = self.cache.get(key) if self.cache else None
                if cached is not None:
                    answers[key] = (cached, "cache")
                    self.stats.cached += 1
                else:
                    todo.append((key, raw))
            for done, (key, raw) in enumerate(todo, start=1):
                if self.progress:
                    self.progress(done, len(todo), raw)
                answer = ask_model(self.ask, raw)
                self.stats.asked += 1
                if self.cache is not None:
                    self.cache.put(key, raw, answer)
                answers[key] = (answer, "model")
        names: dict[str, Name] = {}
        for raw in unique:
            found = answers.get(descriptor_key(raw))
            names[raw] = self._resolve(raw, found)
        self.stats.descriptors = len(representative)
        self.stats.spellings = len(unique)
        self.stats.verified = sum(1 for n in names.values() if n.verified)
        self.stats.unverified = sum(1 for n in names.values() if not n.verified)
        return names

    @staticmethod
    def _resolve(raw: str, found: tuple[Answer, str] | None) -> Name:
        fallback = cleaned_name(raw)
        if found is None:
            return Name(raw, fallback, identity(fallback), False, "unknown", "", "no model", "cleanup")
        answer, source = found
        reason = check(raw, answer)
        # The kind is a label the model gives and code can't check either way; it is kept
        # when the name fails, and the name is marked UNVERIFIED.
        kind = answer.kind if answer.kind in KINDS else "unknown"
        if reason is None:
            name = tidy(answer.merchant)
            return Name(raw, name, identity(name), True, kind, answer.evidence.strip(), "", source)
        return Name(raw, fallback, identity(fallback), False, kind, answer.evidence.strip(), reason, source,
                    model_said=answer.merchant.strip())


def stderr_progress(done: int, total: int, raw: str) -> None:
    """One line per new descriptor on a terminal, nothing when piped (cron mail stays short)."""
    if sys.stderr.isatty():
        sys.stderr.write(f"\r\033[Knaming {done}/{total}: {raw[:50]}")
        if done == total:
            sys.stderr.write("\n")
        sys.stderr.flush()
