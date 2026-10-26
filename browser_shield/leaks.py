"""Secrets and payment data that must never leave through a browser: typed into a page, put in a URL, set by a script.

Every pattern is anchored on a vendor prefix or a structure (PEM header, Luhn plus card-brand prefix) so that ordinary
text does not match; the point is to stop a prompt-injected page from talking the agent into pasting a key, not to
find every secret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (name, pattern). Prefix formats: gitleaks' default rules (https://github.com/gitleaks/gitleaks/blob/master/config/gitleaks.toml)
# and GitHub's token-format announcement (https://github.blog/2021-04-05-behind-githubs-new-authentication-token-formats/).
TOKEN_PATTERNS = [
    ("Anthropic API key", r"\bsk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI API key", r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),
    ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    ("GitLab token", r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    ("AWS access key id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("Slack token", r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    ("Slack webhook", r"hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+"),
    ("Discord webhook", r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{30,}"),
    ("Stripe live key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("Hugging Face token", r"\bhf_[A-Za-z0-9]{34,}\b"),
    ("private key", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    ("JSON web token", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]
_TOKENS = [(name, re.compile(p)) for name, p in TOKEN_PATTERNS]

_CARD_CANDIDATE = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")
_SSN = re.compile(r"(?<![\d\-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d\-])")

# Published test numbers that pass Luhn and must keep working against a payment provider's test mode on a dev host
# (Stripe: https://docs.stripe.com/testing#cards; Adyen and Braintree publish the same Visa/Mastercard ones).
TEST_CARDS = {
    "4242424242424242", "4000056655665556", "5555555555554444", "2223003122003222", "5200828282828210",
    "5105105105105100", "378282246310005", "371449635398431", "6011111111111117", "6011000990139424",
    "3056930009020004", "36227206271667", "3566002020360505", "6200000000000005", "4000000000000002",
    "4000000000009995", "4000000000000069", "4000000000000127", "4000000000000119", "4000002500003155",
    "4000002760003184", "4000000000003220", "4111111111111111", "5431111111111111", "4012888888881881",
}


@dataclass(frozen=True)
class Leak:
    kind: str
    excerpt: str  # a masked excerpt, safe to show: never the whole secret


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


def card_brand(digits: str) -> str | None:
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


def find_leaks(text: str, allow_test_cards: bool = True) -> list[Leak]:
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
    return found
