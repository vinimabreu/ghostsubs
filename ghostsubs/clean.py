"""Plain-code cleanup of a charge descriptor, before any model sees it.

A descriptor is what the card network or the bank printed: a payment
processor in front, the merchant somewhere in the middle, and reference codes,
phone numbers, card masks, dates, store numbers and a city at the end. This
module strips what it can recognise with patterns. The result is the name
shown when the model is off, or when the model's answer fails the check.

It also holds the small text helpers the rest of the package shares: the cache
key of a descriptor and the identity two names must share to be merged.
"""

from __future__ import annotations

import re
import unicodedata

# Payment processors that print their own name in front of the merchant's.
# The second item is the processor's identity, used to refuse a model answer
# that names the processor while a merchant follows it.
PROCESSORS: tuple[tuple[str, str], ...] = (
    (r"PAYPAL\s*\*", "paypal"),
    (r"PP\s*\*", "paypal"),
    (r"SQU?\s*\*", "square"),
    (r"GOOGLE\s*\*", "google"),
    (r"APPLE\.COM/BILL\b", "apple"),
    (r"PAG\s*\*", "pagseguro"),
    (r"PG\s*\*", "pagseguro"),
    (r"EC\s*\*", "ec"),
    (r"DM\s*\*", "dlocal"),
    (r"DL\s*\*", "dlocal"),
    (r"DLOCAL\s*\*", "dlocal"),
    (r"MP\s*\*", "mercadopago"),
    (r"MERCADOPAGO\s*\*", "mercadopago"),
    (r"EBN\s*\*", "ebanx"),
    (r"EBANX\s*\*", "ebanx"),
    (r"IFD\s*\*", "ifood"),
    (r"TST\s*\*", "toast"),
    (r"SP\s*\*", "shopify"),
    (r"CKO\s*\*", "checkout"),
    (r"PADDLE\.NET\s*\*", "paddle"),
    (r"FS\s*\*", "fastspring"),
    (r"2CO\.COM\s*[|*]", "2checkout"),
    (r"DRI\s*\*", "digitalriver"),
    (r"STRIPE\s*\*", "stripe"),
)
_PROCESSOR = [(re.compile(r"^\s*" + pattern, re.I), name) for pattern, name in PROCESSORS]

# What a model might call the processor itself.
PROCESSOR_NAMES: dict[str, frozenset[str]] = {
    "paypal": frozenset({"paypal"}),
    "square": frozenset({"square", "sq", "squareup"}),
    "google": frozenset({"google", "googlepay", "googleplay", "googlepayments"}),
    "apple": frozenset({"apple", "itunes", "applebill"}),
    "pagseguro": frozenset({"pagseguro", "pagbank", "pag", "pg"}),
    "ec": frozenset({"ec"}),
    "dlocal": frozenset({"dlocal", "dm", "dl"}),
    "mercadopago": frozenset({"mercadopago", "mp"}),
    "ebanx": frozenset({"ebanx", "ebn"}),
    "ifood": frozenset(),
    "toast": frozenset({"toast", "tst"}),
    "shopify": frozenset({"shopify", "shopifypayments", "sp"}),
    "checkout": frozenset({"checkout", "cko"}),
    "paddle": frozenset({"paddle"}),
    "fastspring": frozenset({"fastspring", "fs"}),
    "2checkout": frozenset({"2checkout", "2co", "verifone"}),
    "digitalriver": frozenset({"digitalriver", "dri"}),
    "stripe": frozenset({"stripe"}),
}

# Labels a bank puts in front of the descriptor.
_BANK_LABELS = re.compile(
    r"^\s*(?:"
    r"COMPRA (?:CARTAO|NO DEBITO|NO CREDITO|DEBITO|CREDITO)(?: INTERNACIONAL)?"
    r"|COMPRA"
    r"|DEB(?:ITO)?\.? ?AUT(?:OM(?:ATICO)?)?\.?"
    r"|PAGTO\.?(?: ELETRON(?:ICO)?)?"
    r"|PIX (?:ENVIADO|ENV|TRANSF|QRS)"
    r"|TRANSF(?:ERENCIA)?(?: ENVIADA)?"
    r"|TED(?: ENVIADA)?"
    r"|POS (?:PURCHASE|DEBIT)"
    r"|(?:DEBIT CARD|VISA|CARD|DEBIT) PURCHASE(?: -)?"
    r"|PURCHASE AUTHORI[ZS]ED ON \d{1,2}/\d{1,2}"
    r"|CHECKCARD \d{4}"
    r"|RECURRING (?:PAYMENT|DEBIT|CHARGE)"
    r"|ACH (?:DEBIT|WITHDRAWAL|PMT|PAYMENT)"
    r"|PREAUTHORI[ZS]ED DEBIT"
    r"|(?:ONLINE|BILL|WEB) PAYMENT(?: TO)?"
    r")\b[\s:*-]*",
    re.I,
)

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
COUNTRIES = frozenset("US USA BR BRA BRASIL BRAZIL GB GBR UK IE IRL NL NLD LU LUX SE SWE DE FR ES SG AU MX".split())
# Cities that card networks often print at the end of a subscription charge.
CITIES = (
    "LOS GATOS", "SAN JOSE", "SAN FRANCISCO", "NEW YORK", "SEATTLE", "SUNNYVALE", "MOUNTAIN VIEW",
    "CUPERTINO", "SANTA MONICA", "BURBANK", "LOS ANGELES", "OAKLAND", "PALO ALTO", "MENLO PARK",
    "REDMOND", "MIAMI", "AUSTIN", "CHICAGO", "BOSTON", "DUBLIN", "LONDON", "AMSTERDAM", "LUXEMBOURG",
    "STOCKHOLM", "SAO PAULO", "RIO DE JANEIRO", "BARUERI", "OSASCO", "BELO HORIZONTE", "CURITIBA",
    "PORTO ALEGRE", "BRASILIA", "CAMPINAS",
)
_CITY_END = re.compile(r"\s(?:" + "|".join(re.escape(c) for c in CITIES) + r")$")
COMPANY_SUFFIXES = frozenset("INC LLC LTD LTDA SA S/A CO CORP PLC GMBH BV ME EIRELI".split())

_DOMAIN = re.compile(r"^(?:[A-Z0-9-]+\.)*?([A-Z0-9-]+)\.(?:COM\.BR|COM|NET|ORG|IO|TV|CO|US|APP|ME)(?:/\S*)?$", re.I)
_NOISE_LABELS = frozenset({"HELP", "SUPPORT", "WWW", "PAY", "BILL", "ACCT", "HELPPAY"})

_CARD_MASK = re.compile(r"(?:\b[X*]{2,}\d{2,4}\b|\b\d{4}[X*]{2,}\d{4}\b|\b(?:CARD|CARTAO|FINAL)\s*\d{4}\b)", re.I)
_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b")
_TIME = re.compile(r"\b(?:\d{1,2}:\d{2}(?:\s?[AP]M)?|\d{1,2}\s?[AP]M)\b", re.I)
_WEEKDAY = re.compile(r"\b(?:MON|TUE|WED|THU|FRI|SAT|SUN)\b", re.I)
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\b\d{3}-\d{4}\b")
_STORE_NUMBER = re.compile(r"#\s*\w+")
_REF = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,}\b", re.I)
_DIGITS = re.compile(r"\b\d{4,}\b")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def squash(text: str) -> str:
    """Case and runs of spaces do not matter when a quote is compared with a descriptor."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().casefold()


_KEY_REF = re.compile(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{6,}\b")


def descriptor_key(raw: str) -> str:
    """The cache key: case, spacing, reference codes and the digits of phones and dates do not
    make a descriptor new, so ``AMZN Mktp US*2K4L19ZX0`` and ``AMZN Mktp US*RT5Y21QX8`` share
    one model call, and so does next month's export."""
    return re.sub(r"\d+", "#", _KEY_REF.sub("#", squash(raw)))


def tokens(text: str) -> list[str]:
    text = strip_accents(unicodedata.normalize("NFKC", text or "")).casefold()
    text = text.replace("+", " plus ").replace("&", " and ")
    return re.findall(r"[a-z0-9]+", text)


FILLER = frozenset({"the", "inc", "llc", "ltd", "ltda", "co", "corp", "sa", "plc", "gmbh", "eireli"})


def identity(name: str) -> str:
    """What two merchant names must share to be the same merchant: ``Disney+`` and
    ``Disney Plus``, ``Amazon.com`` and ``Amazon``, ``The New York Times`` and
    ``New York Times``."""
    text = re.sub(r"\.(?:com|net|org|io|tv|co|app)(?:\.br)?\b", " ", name or "", flags=re.I)
    words = tokens(text)
    kept = [w for w in words if w not in FILLER]
    return "".join(kept or words)


PROCESSOR_DISPLAY = {
    "paypal": "PayPal", "square": "Square", "google": "Google", "apple": "Apple", "pagseguro": "PagSeguro",
    "ec": "EC", "dlocal": "dLocal", "mercadopago": "Mercado Pago", "ebanx": "EBANX", "ifood": "iFood",
    "toast": "Toast", "shopify": "Shopify", "checkout": "Checkout.com", "paddle": "Paddle",
    "fastspring": "FastSpring", "2checkout": "2Checkout", "digitalriver": "Digital River", "stripe": "Stripe",
}


def split_processor(raw: str) -> tuple[str | None, str]:
    """The last processor in front of a descriptor, if any, and the text after it, with the
    bank's own labels (``COMPRA CARTAO``, ``POS PURCHASE``) taken off first."""
    text = unicodedata.normalize("NFKC", raw or "").replace("\u00a0", " ").strip()
    processor: str | None = None
    previous = None
    while previous != text:
        previous = text
        text = _BANK_LABELS.sub("", text, count=1)
        for pattern, name in _PROCESSOR:
            match = pattern.match(text)
            if match:
                processor, text = name, text[match.end():].strip()
                break
    return processor, text.strip()


def _drop_domains(words: list[str]) -> list[str]:
    """``NETFLIX.COM`` becomes ``NETFLIX``; a help address that repeats a word already
    there (``UBER *TRIP HELP.UBER.COM``) and a path like ``g.co/helppay`` are dropped."""
    plain = {w.upper().strip(".,") for w in words if not _DOMAIN.match(w)}
    kept: list[str] = []
    for word in words:
        match = _DOMAIN.match(word)
        if not match:
            if "/" not in word:
                kept.append(word)
            continue
        label = match.group(1)
        if len(label) > 2 and label.upper() not in _NOISE_LABELS and label.upper() not in plain:
            kept.append(label)
    return kept


def _strip_tail(text: str) -> str:
    words = text.split()
    changed = True
    while changed and len(words) > 1:
        changed = False
        last = words[-1].upper().strip(".,")
        if last in US_STATES or last in COUNTRIES or last in COMPANY_SUFFIXES:
            words.pop()
            changed = True
            continue
        joined = " ".join(words)
        city = _CITY_END.search(joined.upper())
        if city and city.start() > 0:
            words = joined[:city.start()].split()
            changed = True
    return " ".join(words)


def _denoise(text: str) -> str:
    for pattern in (_CARD_MASK, _DATE, _TIME, _PHONE, _STORE_NUMBER):
        text = pattern.sub(" ", text)
    text = text.replace("*", " ").replace("|", " ")
    text = " ".join(_drop_domains(text.split()))
    text = _REF.sub(" ", text)
    text = _DIGITS.sub(" ", text)
    text = _WEEKDAY.sub(" ", text)
    text = re.sub(r"[^\w&+' .-]", " ", text)
    text = re.sub(r"(?<!\w)[.-]+|[.-]+(?!\w)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _strip_tail(text)


def _meaningful(text: str) -> bool:
    words = text.upper().split()
    return bool(re.search(r"[A-Za-z]{2}", text)) and not all(w in US_STATES or w in COUNTRIES for w in words)


def clean(raw: str) -> str:
    """The descriptor with the recognisable noise removed, in the case it came in."""
    text = unicodedata.normalize("NFKC", raw or "").replace("\u00a0", " ")
    fields = re.split(r"\s{2,}", text.strip())
    if len(fields) > 1 and len(re.sub(r"[^A-Za-z]", "", fields[0])) >= 3:
        text = fields[0]
    processor, rest = split_processor(text)
    result = _denoise(rest)
    if _meaningful(result):
        return result
    if processor:
        return PROCESSOR_DISPLAY.get(processor, processor)
    fallback = re.sub(r"\s+", " ", re.sub(r"[^\w&+' ]", " ", raw or "")).strip()
    return fallback[:40] or "?"


def processor_hidden_merchant(raw: str) -> str | None:
    """The processor's identity when a merchant name follows it, else None. A model answer
    naming that processor names the wrong business."""
    processor, rest = split_processor(raw)
    if processor and _meaningful(_denoise(rest)):
        return processor
    return None


def display(text: str) -> str:
    """Capitalise shouted or lowercase words; leave mixed case (YouTube, iFood) alone."""
    words = []
    for word in text.split():
        if word.isupper() or word.islower():
            parts = re.split(r"(['-])", word.lower())
            words.append("".join(part.capitalize() if part not in "'-" else part for part in parts))
        else:
            words.append(word)
    return " ".join(words)


def cleaned_name(raw: str) -> str:
    """The name shown when no checked model answer is available."""
    return display(clean(raw))
