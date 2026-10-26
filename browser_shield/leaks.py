"""Secrets and payment data that must never leave through a browser: typed into a page, put in a URL, set by a script.

Every pattern is anchored on a vendor prefix or a structure (PEM header, Luhn plus card-network range) so that
ordinary text does not match; the point is to stop a prompt-injected page from talking the agent into pasting a key,
not to find every secret. Secrets without a known shape are caught by value: any secret-named environment variable
the hook inherits from Claude Code.
"""

from __future__ import annotations

import os
import re
from collections import namedtuple
from typing import Mapping, Optional

# (name, pattern). Prefixes from gitleaks' default rules (config/gitleaks.toml, v8.25), GitHub's token formats
# (github.blog, "Behind GitHub's new authentication token formats"), AWS IAM identifiers (AKIA/ASIA/ABIA/ACCA),
# Slack's token docs (xoxb/xoxp/xapp/xwfp) and Stripe's key docs (sk_/rk_ live and prod keys move real money).
TOKEN_PATTERNS = [
    ("Anthropic API key", r"\bsk-ant-[a-z]+\d{2}-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI API key", r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),
    ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    ("GitLab token", r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    ("AWS access key id", r"\b(?:AKIA|ASIA|ABIA|ACCA|A3T[A-Z0-9])[A-Z2-7]{16}\b"),
    ("Slack token", r"\b(?:xox[abposre]|xapp|xwfp)-[A-Za-z0-9\-]{10,}"),
    ("Slack webhook", r"hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+"),
    ("Discord webhook", r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{30,}"),
    ("Stripe live key", r"\b(?:sk|rk)_(?:live|prod)_[A-Za-z0-9]{20,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("Hugging Face token", r"\bhf_[A-Za-z0-9]{34,}\b"),
    ("private key", r"-----BEGIN [A-Z0-9 _\-]{0,100}PRIVATE KEY"),
    ("JSON web token", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]
_TOKENS = [(name, re.compile(p)) for name, p in TOKEN_PATTERNS]
# Kinds that also appear legitimately in URLs and page scripts: YouTube pages carry a public InnerTube key
# (AIza...), and sign-in and magic links carry JWTs. Typed into a page they are still refused.
SOFT_IN_URLS = {"Google API key", "JSON web token"}

_CARD_CANDIDATE = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")
_SSN = re.compile(r"(?<![\d\-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d\-])")
# Whole underscore-separated words: ANTHROPIC_API_KEY, GITHUB_TOKEN, DB_PASSWORD; not SSH_AUTH_SOCK or *_SESSION_ID.
_SECRET_NAME = re.compile(r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|COOKIE|APIKEY|PAT)(?:_|$)", re.I)
ENV_MIN_LENGTH = 16

# Published sandbox numbers. Every one passes Luhn, so without this list a payment provider's own test card would be
# refused on the user's local checkout. Stripe: docs.stripe.com/testing (success, decline, 3DS); Braintree:
# developer.paypal.com/braintree/docs/reference/general/testing ("Valid card numbers").
TEST_CARDS = {
    # Stripe
    "4242424242424242", "4000056655665556", "5555555555554444", "2223003122003222", "5200828282828210",
    "5105105105105100", "378282246310005", "371449635398431", "6011111111111117", "6011000990139424",
    "6011981111111113", "3056930009020004", "36227206271667", "6555900000604105", "3566002020360505",
    "6200000000000005", "6200000000000047", "6205500000000000004", "4000000000000002", "4000000000009995",
    "4000000000009987", "4000000000009979", "4000000000000069", "4000000000000127", "4000000000000119",
    "4000000000006975", "4000000000000341", "4100000000000019", "4000002500003155", "4000002760003184",
    "4000000000003220", "4000008400000027",
    # Braintree
    "36259600000004", "6011000991300009", "3530111333300000", "2223000048400011", "4111111111111111",
    "4005519200000004", "4009348888881881", "4012000033330026", "4012000077777777", "4012888888881881",
    "4217651111111119", "4500600000000061", "6243030000000001", "6221261111117766", "6223164991230014",
}


# kind, and a masked excerpt that is safe to show and log: never the whole secret. (No dataclasses: the hook starts
# on every tool call, and importing dataclasses costs about 25 ms.)
Leak = namedtuple("Leak", "kind excerpt")


def _mask(s: str) -> str:
    return s[:6] + "…" if len(s) > 8 else "…"


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand(digits: str) -> Optional[str]:
    """The card network whose number ranges and lengths fit, or None: a Luhn pass alone matches 1 in 10 numbers."""
    n = len(digits)
    two, three, four, six = int(digits[:2]), int(digits[:3]), int(digits[:4]), int(digits[:6])
    if digits[0] == "4" and n in (13, 16, 19):
        return "Visa"
    if (51 <= two <= 55 or 222100 <= six <= 272099) and n == 16:
        return "Mastercard"
    if two in (34, 37) and n == 15:
        return "American Express"
    if (four == 6011 or two == 65 or 644 <= three <= 649 or 622126 <= six <= 622925) and 16 <= n <= 19:
        return "Discover"
    if 3528 <= four <= 3589 and 16 <= n <= 19:
        return "JCB"
    if (300 <= three <= 305 or two in (36, 38, 39) or four == 3095) and 14 <= n <= 19:
        return "Diners Club"
    if two == 62 and 16 <= n <= 19:
        return "UnionPay"
    return None


def env_secrets(environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Secret-named environment variables long enough that finding the value in text is no coincidence."""
    environ = os.environ if environ is None else environ
    return {
        k: v for k, v in environ.items()
        if _SECRET_NAME.search(k) and len(v) >= ENV_MIN_LENGTH and not re.search(r"\s", v) and v[0] not in "/~"
    }


def find_leaks(text, allow_test_cards: bool = True, environ: Optional[Mapping[str, str]] = None) -> list[Leak]:
    if not isinstance(text, str) or not text:
        return []
    found: list[Leak] = []
    for name, rx in _TOKENS:
        m = rx.search(text)
        if m:
            found.append(Leak(name, _mask(m.group(0))))
    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if not 13 <= len(digits) <= 19 or not luhn_ok(digits):
            continue
        brand = card_brand(digits)
        if brand is None or (allow_test_cards and digits in TEST_CARDS):
            continue
        found.append(Leak(f"{brand} card number", f"…{digits[-4:]}"))
        break
    if _SSN.search(text):
        found.append(Leak("US Social Security number", "…"))
    for name, value in env_secrets(environ).items():
        if value in text:
            found.append(Leak(f"value of ${name}", "…"))
    return found
