"""Domain lists: parse hosts / plain / AdBlock-style files, compile them into one SQLite index, look hosts up by suffix.

A hook runs once per tool call, so loading half a million domains into a set every time would cost about a second;
the compiled index answers a lookup in well under a millisecond without loading anything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import urls

ROOT = Path(__file__).resolve().parent.parent
LISTS_DIR = ROOT / "lists"  # committed, curated


def data_dirs(root: Path = ROOT) -> tuple[Path, Path]:
    """(downloads, index, log and session markers; personal lists) for this copy.

    A plugin install keeps both in the data folder Claude Code gives the plugin, which survives its updates (its
    hook commands pass ${CLAUDE_PLUGIN_DATA} as BROWSER_SHIELD_DATA); any other copy keeps them in its own cache/
    and lists/. An empty or relative value means Claude Code did not fill the folder in: refused, so the guard
    names the problem instead of writing into the plugin's own copy, which the next update replaces.
    """
    data = os.environ.get("BROWSER_SHIELD_DATA")
    if data is None:
        return root / "cache", root / "lists"
    if not os.path.isabs(data):
        raise ValueError(f"BROWSER_SHIELD_DATA is {data!r}, not an absolute path: this Claude Code did not fill in "
                         "the plugin's data folder (${CLAUDE_PLUGIN_DATA}); update Claude Code")
    return Path(data), Path(data) / "local"


CACHE_DIR, LOCAL_DIR = data_dirs()  # downloaded and compiled (git-ignored); the user's own *.local.txt lists
INDEX = CACHE_DIR / "blocklist.sqlite"

_HOSTS_LINE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?|::)\s+(\S+)")
_ADBLOCK_LINE = re.compile(r"^\|\|([^\^/$*]+)\^(?:\$.*)?$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9_\-]{1,63}(?:\.(?!-)[a-z0-9_\-]{1,63})+$")
_NOT_DOMAINS = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback", "0.0.0.0"}


def parse_line(line: str) -> Optional[str]:
    """One domain from a hosts-file, plain-domain or `||domain^` AdBlock line; None for comments and anything else."""
    line = line.strip()
    if not line or line[0] in "#!":
        return None
    # A hosts-file comment starts after whitespace; a "#" inside a token (an AdBlock cosmetic rule such as
    # example.com##.ad) is kept, and the domain check below refuses it.
    line = re.split(r"\s#", line, 1)[0].strip()
    m = _HOSTS_LINE.match(line) or _ADBLOCK_LINE.match(line)
    candidate = m.group(1) if m else (line if " " not in line and "/" not in line else None)
    if candidate is None:
        return None
    candidate = candidate.lower().rstrip(".")
    if candidate.startswith("*."):
        candidate = candidate[2:]
    if candidate in _NOT_DOMAINS or not _DOMAIN.match(candidate):
        return None
    if re.fullmatch(r"[\d.]+", candidate):
        return None  # bare IPs are matched by the IP rules, not as domains
    return candidate


def parse_file(path: Path) -> Iterator[str]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            d = parse_line(line)
            if d:
                yield d


def compile_index(sources: Iterable[tuple[str, Path]], index: Path = INDEX) -> dict[str, int]:
    """Write a fresh index from (list name, file) pairs and return how many domains each contributed.

    Built in a temporary file and moved into place, so a running hook never sees a half-written index.
    """
    import sqlite3

    index.parent.mkdir(parents=True, exist_ok=True)
    tmp = index.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE domains (d TEXT PRIMARY KEY, src TEXT NOT NULL) WITHOUT ROWID")
    counts: dict[str, int] = {}
    for name, path in sources:
        rows = ((d, name) for d in parse_file(path))
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO domains VALUES (?, ?)", rows)
        counts[name] = con.total_changes - before
    con.commit()
    con.close()
    tmp.replace(index)
    return counts


def read_list(path: Path) -> set[str]:
    return set(parse_file(path)) if path.exists() else set()


def index_entries(index: Path, host: str) -> list[str]:
    """The downloaded lists that name host or a parent domain of it ("list: domain"), ignoring the allow list."""
    if not index.exists():
        return []
    import sqlite3

    suffixes = urls.host_suffixes(host)
    con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    try:
        rows = con.execute(f"SELECT d, src FROM domains WHERE d IN ({','.join('?' * len(suffixes))})", suffixes).fetchall()
    finally:
        con.close()
    return [f"{src}: {d}" for d, src in rows]


class Lists:
    """The block and allow sets a guard consults: curated text lists always, the compiled index when it exists."""

    def __init__(self, lists_dir: Path = LISTS_DIR, index: Path = INDEX, local_dir: Optional[Path] = None):
        # The personal lists sit beside the curated ones, except for the copy's own lists in a plugin install, which
        # keeps them in its data folder (data_dirs).
        if local_dir is None:
            local_dir = LOCAL_DIR if lists_dir == LISTS_DIR else lists_dir
        self.lists_dir, self.local_dir = lists_dir, local_dir
        self.allow = read_list(lists_dir / "allow.txt") | read_list(local_dir / "allow.local.txt")
        self.block: dict[str, str] = {}
        for folder, name in ((lists_dir, "ad-hosts.txt"), (local_dir, "block.local.txt")):
            for d in read_list(folder / name):
                self.block.setdefault(d, name)
        self.ask = read_list(lists_dir / "ask-hosts.txt")
        self.extensions = {
            line.split("#", 1)[0].strip().lower()
            for line in (local_dir / "extensions.local.txt").read_text(encoding="utf-8", errors="replace").splitlines()
            if line.split("#", 1)[0].strip()
        } if (local_dir / "extensions.local.txt").exists() else set()
        self.index = index if index.exists() else None
        self.index_error = ""

    def asked_by(self, host: str) -> Optional[tuple[str, str]]:
        """(matching domain, list name) when host is a payment or account host the user decides about."""
        for s in urls.host_suffixes(host):
            if s in self.ask:
                return s, "ask-hosts.txt"
        return None

    def allowed(self, host: str) -> bool:
        return any(s in self.allow for s in urls.host_suffixes(host))

    def blocked_by(self, host: str) -> Optional[tuple[str, str]]:
        """(matching domain, list name) for the most specific listed suffix of host, or None.

        The curated lists are ours and win over the allow list (adservice.google.com stays blocked under an allowed
        google.com); the allow list only overrides the downloaded index, whose false positives we do not control.
        """
        suffixes = urls.host_suffixes(host)
        for s in suffixes:
            if s in self.block:
                return s, self.block[s]
        if self.index is None or self.allowed(host):
            return None
        import sqlite3

        try:
            con = sqlite3.connect(f"file:{self.index}?mode=ro", uri=True)
            try:
                marks = ",".join("?" * len(suffixes))
                rows = dict(con.execute(f"SELECT d, src FROM domains WHERE d IN ({marks})", suffixes).fetchall())
            finally:
                con.close()
        except sqlite3.Error as exc:
            # A damaged index must not lock every navigation: the curated lists still apply, and the hook says so.
            self.index_error = f"{type(exc).__name__}: {exc}"
            return None
        for s in suffixes:
            if s in rows:
                return s, rows[s]
        return None
