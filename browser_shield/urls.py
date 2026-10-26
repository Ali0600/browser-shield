"""Where a URL really goes: the scheme and host a browser would navigate to.

A guard that reads the host with urllib.parse can be steered around: browsers follow the WHATWG URL Standard
(https://url.spec.whatwg.org/), which strips tabs and newlines anywhere, reads a backslash as a slash, takes the
host after the LAST @, decodes %-escapes in the host, maps full-width dots and letters, and reads 0x7f.1 or
3232235777 as an IPv4 address. This module follows those steps for http and https and refuses anything it cannot
read (``UnreadableURL``), so the caller can fail closed.
"""

from __future__ import annotations

import bisect
import codecs
import ipaddress
import os
import re
import unicodedata
from collections import namedtuple
from typing import Optional
from urllib.parse import unquote_to_bytes

SPECIAL_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "file": None}
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
# Forbidden domain code points (WHATWG "forbidden domain code point"): C0 controls, space, % and these.
_FORBIDDEN_HOST = set("\x00\t\n\r #/:<>?@[\\]^|%\x7f") | {chr(c) for c in range(0x20)}


class UnreadableURL(ValueError):
    """The URL cannot be parsed the way a browser would; a guard should refuse it."""


class Target(namedtuple("Target", "scheme host port path query url userinfo file_host")):
    """Where a URL goes.

    scheme: lower case, no colon. host: ASCII (punycode), lower case, no trailing dot; IPv4 dotted; IPv6 in brackets;
    "" for non-special schemes. port: int or None. path: raw path, no query or fragment. query: raw, no "?".
    url: the input after preprocessing. userinfo: a "user:password@" came before the host. file_host: for file:
    URLs, the server of a remote share (file://server/share), else "".
    """

    __slots__ = ()

    def __new__(cls, scheme, host, port, path, query, url, userinfo=False, file_host=""):
        return super().__new__(cls, scheme, host, port, path, query, url, userinfo, file_host)

    @property
    def is_special(self) -> bool:
        return self.scheme in SPECIAL_SCHEMES

    @property
    def is_ip(self) -> bool:
        return self.host.startswith("[") or bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", self.host))


# WHATWG strips C0 controls and space; the navigate tool also trims what JavaScript's trim() does (measured
# 2026-10-26: NBSP, U+FEFF and U+2028 around a URL were trimmed and the page loaded).
_TRIM = "".join(chr(c) for c in range(0x21)) + "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009" \
    "\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
MAX_LENGTH = 8192
# Schemes that are never a host:port the tool would default to https.
_NEVER_HOST = {"javascript", "vbscript", "data", "blob", "filesystem", "view-source", "about", "file", "chrome",
               "chrome-extension", "devtools", "intent", "itms-services", "mailto", "tel", "sms", "ws", "wss", "ftp"}


def preprocess(raw: str) -> str:
    """Trim as the browser and the navigate tool do, then remove every tab, CR and LF (WHATWG basic parser)."""
    s = raw.strip(_TRIM)
    return s.replace("\t", "").replace("\n", "").replace("\r", "")


def is_local_path(raw: str) -> bool:
    """True for "/abs", "~/x", "./x", "\\x": the navigate tool opens these as files on this Mac ("//host" is a URL)."""
    s = preprocess(raw)
    return s.startswith(("/", "\\", "~", ".")) and not s.startswith("//")


def interpretations(raw: str, default_scheme: str = "https") -> list[str]:
    """Every reading of an agent-typed URL the navigate tool could act on; a guard keeps the strictest verdict.

    Measured against the tool on 2026-10-26: "example.com" and "//example.com/x" open over https, "example.com:443/a"
    opens https://example.com/a (a browser alone would read "example.com:" as a scheme), and "user:pass@example.com"
    opens example.com. Local paths ("/x", "~/x") are files: see is_local_path.
    """
    s = preprocess(raw)
    if s.startswith("//"):
        return [f"{default_scheme}:{s}"]
    m = _SCHEME.match(s)
    if not m:
        return [f"{default_scheme}://{s}"]
    scheme, rest = m.group(1).lower(), s[m.end():]
    if rest.startswith(("/", "\\")) or scheme in SPECIAL_SCHEMES or scheme in _NEVER_HOST:
        return [s]
    if re.match(r"\d*(?:[/\\?#]|$)", rest):
        return [f"{default_scheme}://{s}"]  # host:port
    if "@" in re.split(r"[/\\?#]", rest, 1)[0]:
        return [s, f"{default_scheme}://{s}"]  # user:password@host, or an app scheme: both are judged
    return [s]


def parse(raw: str) -> Target:
    """Parse one URL as a browser would. Raises UnreadableURL for anything the WHATWG parser would reject."""
    if len(raw) > MAX_LENGTH:
        raise UnreadableURL(f"URL longer than {MAX_LENGTH} characters")
    if any(0xD800 <= ord(ch) <= 0xDFFF or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in preprocess(raw)):
        raise UnreadableURL("control character or lone surrogate in the URL")
    try:
        return _parse(raw)
    except UnreadableURL:
        raise
    except (UnicodeError, ValueError) as exc:  # a lone surrogate, an encoder refusing a label: unreadable, not a crash
        raise UnreadableURL(f"{type(exc).__name__}: {exc}") from exc


def _parse(raw: str) -> Target:
    s = preprocess(raw)
    m = _SCHEME.match(s)
    if not m:
        raise UnreadableURL(f"no scheme in {raw!r}")
    scheme = m.group(1).lower()
    rest = s[m.end():]
    if scheme not in SPECIAL_SCHEMES:
        return Target(scheme, "", None, "", "", s)
    if scheme == "file":
        m = re.match(r"^[/\\]{2}([^/\\?#]*)", rest)
        share = (m.group(1) if m else "").lower()
        return Target(scheme, "", None, rest, "", s, file_host="" if share in ("", "localhost") else share)
    # Special schemes: any run of / and \ after the colon is skipped, even none ("http:evil.com" is http://evil.com/).
    rest = rest.lstrip("/\\")
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in "/\\?#":
            end = i
            break
    authority, tail = rest[:end], rest[end:]
    # The host follows the LAST "@"; every earlier "@" belongs to the userinfo.
    userinfo = "@" in authority
    if userinfo:
        authority = authority.rsplit("@", 1)[1]
    host_text, port = _split_port(authority)
    host = parse_host(host_text)
    path, query = _split_tail(tail)
    return Target(scheme, host, port, path, query, s, userinfo=userinfo)


def _split_port(authority: str) -> tuple[str, Optional[int]]:
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0:
            raise UnreadableURL("unclosed IPv6 literal")
        host, after = authority[: close + 1], authority[close + 1:]
        if after and not after.startswith(":"):
            raise UnreadableURL("junk after IPv6 literal")
        port_text = after[1:] if after else ""
    elif ":" in authority:
        host, port_text = authority.split(":", 1)
    else:
        host, port_text = authority, ""
    if port_text == "":
        return host, None
    if not port_text.isdigit() or not port_text.isascii():
        raise UnreadableURL(f"bad port {port_text!r}")
    port = int(port_text)
    if port > 65535:
        raise UnreadableURL(f"port {port} out of range")
    return host, port


def _split_tail(tail: str) -> tuple[str, str]:
    tail = tail.split("#", 1)[0]
    path, _, query = tail.partition("?")
    return path.replace("\\", "/"), query


def parse_host(text: str) -> str:
    """WHATWG host parser for special schemes: IPv6 literal, IPv4 (any of its number forms) or an ASCII domain."""
    if text == "":
        raise UnreadableURL("empty host")
    if text.startswith("["):
        if "%" in text:
            raise UnreadableURL(f"zone id in IPv6 literal {text!r}")
        try:
            return "[" + ipaddress.IPv6Address(text[1:-1]).compressed + "]"
        except ValueError as exc:
            raise UnreadableURL(f"bad IPv6 literal {text!r}") from exc
    try:
        decoded = unquote_to_bytes(text).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableURL(f"host is not UTF-8 after %-decoding: {text!r}") from exc
    ascii_host = domain_to_ascii(decoded)
    if _ends_in_number(ascii_host):
        return parse_ipv4(ascii_host)
    return ascii_host


_UTS46 = None


def _uts46() -> tuple:
    """(refused range starts, their ends, mapped code points) from uts46.txt, loaded on first use."""
    global _UTS46
    if _UTS46 is None:
        starts, ends, mapped = [], [], {}
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "uts46.txt"), encoding="ascii") as fh:
            for line in fh:
                parts = line.split()
                if parts and parts[0] == "R":
                    starts.append(int(parts[1], 16))
                    ends.append(int(parts[2], 16))
                elif parts and parts[0] == "M":
                    mapped[int(parts[1], 16)] = "".join(chr(int(x, 16)) for x in parts[2:])
        _UTS46 = (starts, ends, mapped)
    return _UTS46


def uts46_map(domain: str) -> str:
    """The UTS 46 mapping step a browser runs on a host: case-fold, map compatibility forms (full-width letters,
    ideographic full stops), drop default-ignorables (variation selectors, soft hyphens), refuse disallowed code
    points, then NFC. The table is scripts/gen_uts46.js's reading of Node's own parser (ICU), not Python's
    Unicode 3.2 idna codec, which keeps characters a browser drops."""
    starts, ends, mapped = _uts46()
    out = []
    for ch in domain:
        cp = ord(ch)
        if cp < 0x80:
            out.append(ch.lower())
            continue
        i = bisect.bisect_right(starts, cp) - 1
        if i >= 0 and cp <= ends[i]:
            raise UnreadableURL(f"U+{cp:04X} in host {domain!r}: browsers refuse it")
        out.append(mapped.get(cp, ch))
    return unicodedata.normalize("NFC", "".join(out))


def domain_to_ascii(domain: str) -> str:
    """Lower-case ASCII form of a domain as a browser computes it: UTS 46 mapping, then punycode per Unicode label."""
    mapped = domain.lower() if domain.isascii() else uts46_map(domain)
    if any(ch in _FORBIDDEN_HOST for ch in mapped):
        raise UnreadableURL(f"forbidden character in host {domain!r}")
    labels = mapped.split(".")
    if labels and labels[-1] == "" and len(labels) > 1:
        labels = labels[:-1]  # one trailing dot is the same host ("evil.com." is "evil.com")
    out = []
    for label in labels:
        if label.isascii():
            out.append(label)
            continue
        try:
            out.append("xn--" + codecs.encode(label, "punycode").decode("ascii"))
        except UnicodeError as exc:
            raise UnreadableURL(f"cannot convert host label {label!r} to punycode") from exc
    if not out or "" in out:
        raise UnreadableURL(f"empty label in host {domain!r}")  # "a..b", ".a", "a.." (one trailing dot is fine)
    return ".".join(out)


def _ends_in_number(host: str) -> bool:
    last = host.rsplit(".", 1)[-1]
    if last.isdigit() and last.isascii():
        return True
    return bool(re.fullmatch(r"0[xX][0-9A-Fa-f]*", last))


def _ipv4_number(part: str) -> int:
    if part == "":
        raise UnreadableURL("empty IPv4 part")
    base = 10
    if part[:2] in ("0x", "0X"):
        part, base = part[2:], 16
    elif len(part) > 1 and part[0] == "0":
        part, base = part[1:], 8
    if part == "":
        return 0
    digits = {10: "0123456789", 16: "0123456789abcdefABCDEF", 8: "01234567"}[base]
    if any(ch not in digits for ch in part):
        raise UnreadableURL(f"bad IPv4 number {part!r}")
    return int(part, base)


def parse_ipv4(host: str) -> str:
    """WHATWG IPv4 parser: 1-4 parts, each decimal, 0x hex or 0-led octal; the last part fills the remaining bytes."""
    parts = host.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts = parts[:-1]
    if len(parts) > 4:
        raise UnreadableURL(f"too many IPv4 parts in {host!r}")
    numbers = [_ipv4_number(p) for p in parts]
    if any(n > 255 for n in numbers[:-1]):
        raise UnreadableURL(f"IPv4 part over 255 in {host!r}")
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        raise UnreadableURL(f"IPv4 address out of range in {host!r}")
    value = numbers[-1]
    for i, n in enumerate(numbers[:-1]):
        value += n * 256 ** (3 - i)
    return ".".join(str((value >> shift) & 255) for shift in (24, 16, 8, 0))


def host_suffixes(host: str) -> list[str]:
    """The host and each parent domain at a label boundary: a.b.evil.com -> [a.b.evil.com, b.evil.com, evil.com, com]."""
    if host.startswith("[") or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return [host]
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(len(labels))]
