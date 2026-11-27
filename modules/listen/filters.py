"""Vietnam-finance keyword filter.

`match_vn_finance(text)` returns `(matched, reasons)`. Each reason is
`"<class>:<literal>"` naming which keyword class and which literal hit.
Classes come from the design spec §3.

The input is first normalised (NFKD-stripped of diacritics, casefolded,
whitespace collapsed) so that `Việt Nam`, `viet nam`, and `VIỆT  NAM` all
match the same geo keyword. Bank, payment and vn_text patterns use `\\b`
word boundaries so a bank acronym embedded inside an onion hash does not
match.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

__all__ = ["normalize", "match_vn_finance"]


_BANK_WORDS = (
    "vietcombank", "vcb", "bidv", "techcombank", "tcb", "vpbank",
    "agribank", "sacombank", "acb", "mbbank", "vietinbank", "ctg",
    "hdbank", "eximbank", "shb", "tpbank", "ocb", "seabank", "lpb",
    "msb", "nab", "pgb", "vib",
)
_PAYMENT_WORDS = (
    "napas", "momo", "zalopay", "vnpay", "viettelmoney", "shopeepay",
    "moca", "payoo",
)
_VN_TEXT_WORDS = ("ngan hang", "tai khoan", "the tin dung", "ro ri")

_BANK_RE = re.compile(r"\b(" + "|".join(_BANK_WORDS) + r")\b")
_PAYMENT_RE = re.compile(r"\b(" + "|".join(_PAYMENT_WORDS) + r")\b")
_VN_TEXT_RE = re.compile(r"\b(" + "|".join(_VN_TEXT_WORDS) + r")\b")

_GEO_RE = re.compile(
    r"\b(viet ?nam|hanoi|ho ?chi ?minh|saigon|da ?nang)\b"
    r"|\.vn\b"
)

# Phone forms:
#  - E.164 compact "+84XXXXXXXXX" (9-10 digits after country code)
#  - National "0[35789]XXXXXXXX"
_PHONE_E164_RE = re.compile(r"(?<![\d+])\+?84\d{9,10}\b")
_PHONE_NATL_RE = re.compile(r"\b0[35789]\d{8}\b")

# Collapse separators between two digits so "+84 912 345 678" compacts to
# "+84912345678" for the phone pass (spaces between letters are untouched).
_DIGIT_SEP_RE = re.compile(r"(?<=\d)[\s.\-](?=\d)")


def normalize(text: str) -> str:
    """NFKD-strip diacritics, casefold, collapse whitespace."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


def _compact_digits(text: str) -> str:
    return _DIGIT_SEP_RE.sub("", text)


def match_vn_finance(text: str) -> Tuple[bool, List[str]]:
    """Return (matched, reasons). Reasons label each hit as `class:literal`."""
    s = normalize(text)
    if not s:
        return (False, [])

    reasons: List[str] = []
    for m in _BANK_RE.finditer(s):
        reasons.append(f"bank:{m.group(1)}")
    for m in _PAYMENT_RE.finditer(s):
        reasons.append(f"payment:{m.group(1)}")
    for m in _VN_TEXT_RE.finditer(s):
        reasons.append(f"vn_text:{m.group(1)}")
    for m in _GEO_RE.finditer(s):
        reasons.append(f"geo:{m.group(0)}")

    phone_pass = _compact_digits(s)
    for m in _PHONE_E164_RE.finditer(phone_pass):
        reasons.append(f"phone:{m.group(0)}")
    for m in _PHONE_NATL_RE.finditer(phone_pass):
        reasons.append(f"phone:{m.group(0)}")

    return (bool(reasons), reasons)
