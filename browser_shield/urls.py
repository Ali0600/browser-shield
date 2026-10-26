"""Where a URL really goes: the scheme and host a browser would navigate to.

A guard that reads the host with urllib.parse can be steered around: browsers follow the WHATWG URL Standard
(https://url.spec.whatwg.org/), which strips tabs and newlines anywhere, reads a backslash as a slash, takes the
host after the LAST @, decodes %-escapes in the host, maps full-width dots and letters, and reads 0x7f.1 or
3232235777 as an IPv4 address. This module follows those steps for http and https and refuses anything it cannot
read (``UnreadableURL``), so the caller can fail closed.
"""

from __future__ import annotations

import codecs
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote_to_bytes

SPECIAL_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "file": None}
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
# The navigate tool takes "example.com:8080/x" without a scheme; a browser would read "example.com" as the scheme.
_HOST_PORT = re.compile(r"^[^/\\?#@:]+:\d*(?:[/\\?#]|$)")
# Forbidden domain code points (WHATWG "forbidden domain code point"): C0 controls, space, % and these.
_FORBIDDEN_HOST = set("\x00\t\n\r #/:<>?@[\\]^|%\x7f") | {chr(c) for c in range(0x20)}
# Label separators UTS 46 maps to a full stop.
_DOTS = re.compile("[。．｡]")


class UnreadableURL(ValueError):
    """The URL cannot be parsed the way a browser would; a guard should refuse it."""


@dataclass(frozen=True)
class Target:
    scheme: str  # lower case, without the colon
    host: str  # ASCII (punycode), lower case, no trailing dot; IPv4 dotted; IPv6 in brackets; "" for non-special
    port: Optional[int]
    path: str  # the raw path after the host, without query or fragment ("" for non-special schemes)
    query: str  # raw, without the "?"
    url: str  # the input after the browser's preprocessing

    @property
    def is_special(self) -> bool:
        return self.scheme in SPECIAL_SCHEMES

    @property
    def is_ip(self) -> bool:
        return self.host.startswith("[") or bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", self.host))


def preprocess(raw: str) -> str:
    """Strip leading and trailing C0 controls and spaces, then remove every tab, CR and LF (WHATWG basic parser)."""
    s = raw.strip("".join(chr(c) for c in range(0x21)))
    return s.replace("\t", "").replace("\n", "").replace("\r", "")


def interpretations(raw: str, default_scheme: str = "https") -> list[str]:
    """Every reading of an agent-typed URL a browser tool could act on.

    The navigate tool "defaults to https://" when no protocol is given, but which inputs count as having none is not
    documented: "localhost:3000" has a scheme ("localhost") to a browser and none to a person. A guard checks every
    reading and keeps the strictest verdict.
    """
    s = preprocess(raw)
    m = _SCHEME.match(s)
    if not m:
        return [f"{default_scheme}://{s}"]
    readings = [s]
    if m.group(1).lower() not in SPECIAL_SCHEMES and _HOST_PORT.match(s):
        readings.append(f"{default_scheme}://{s}")
    return readings


def parse(raw: str) -> Target:
    """Parse one URL as a browser would. Raises UnreadableURL for anything the WHATWG parser would reject."""
    s = preprocess(raw)
    m = _SCHEME.match(s)
    if not m:
        raise UnreadableURL(f"no scheme in {raw!r}")
    scheme = m.group(1).lower()
    rest = s[m.end():]
    if scheme not in SPECIAL_SCHEMES:
        return Target(scheme, "", None, "", "", s)
    if scheme == "file":
        return Target(scheme, "", None, rest, "", s)
    # Special schemes: any run of / and \ after the colon is skipped, even none ("http:evil.com" is http://evil.com/).
    rest = rest.lstrip("/\\")
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in "/\\?#":
            end = i
            break
    authority, tail = rest[:end], rest[end:]
    # The host follows the LAST "@"; every earlier "@" belongs to the userinfo.
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    host_text, port = _split_port(authority)
    host = parse_host(host_text)
    path, query = _split_tail(tail)
    return Target(scheme, host, port, path, query, s)


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


def domain_to_ascii(domain: str) -> str:
    """Lower-case ASCII form of a domain: NFKC-mapped (full-width letters and dots), punycode for Unicode labels.

    Python's idna codec is IDNA 2003, a stricter cousin of the UTS 46 mapping browsers use; a label it cannot
    encode is reported unreadable rather than guessed.
    """
    mapped = _DOTS.sub(".", unicodedata.normalize("NFKC", domain)).lower()
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
            out.append(codecs.encode(label, "idna").decode("ascii"))
        except UnicodeError as exc:
            raise UnreadableURL(f"cannot convert host label {label!r} to punycode") from exc
    host = ".".join(out)
    if host == "" or host.startswith(".") or ".." in host:
        raise UnreadableURL(f"empty label in host {domain!r}")
    return host


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
