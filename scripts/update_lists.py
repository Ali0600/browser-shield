"""Download the threat lists named in lists/sources.json into cache/lists/ and compile cache/blocklist.sqlite.

    /usr/bin/python3 -I scripts/update_lists.py            # fetch every source, then compile
    /usr/bin/python3 -I scripts/update_lists.py --compile  # compile from what is already cached, no network

A fetch that fails, comes back too small or too large never replaces the last good copy (an empty list would
silently switch the protection off); the compile then uses that copy and the run exits 1, naming the failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from browser_shield import lists  # noqa: E402

SOURCES = lists.LISTS_DIR / "sources.json"
LIST_CACHE = lists.CACHE_DIR / "lists"
STATE = LIST_CACHE / "state.json"
CRITICAL_HOSTS = ["www.youtube.com", "i.ytimg.com", "rr1---sn-abc.googlevideo.com", "youtubei.googleapis.com",
                  "yt3.ggpht.com", "www.google.com", "www.gstatic.com", "github.com"]


def fetch(url: str, max_bytes: int, timeout: float = 60.0) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https source: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "browser-shield-list-update/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # TLS verified by the default context
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"larger than {max_bytes} bytes")
    return body


def update_source(src: dict, cache: Path, fetcher: Callable[[str, int], bytes]) -> dict:
    """Fetch one source; keep the old copy unless the new one parses into at least min_entries domains."""
    target = cache / f"{src['name']}.txt"
    record = {"name": src["name"], "url": src["url"], "checked": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        body = fetcher(src["url"], int(src.get("max_bytes", 20_000_000)))
        text = body.decode("utf-8", errors="replace")
        count = sum(1 for line in text.splitlines() if lists.parse_line(line))
        if count < int(src.get("min_entries", 1)):
            raise ValueError(f"only {count} domains, expected at least {src.get('min_entries')}")
        tmp = target.with_suffix(".new")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
        record.update(ok=True, domains=count, fetched=record["checked"])
    except Exception as exc:  # recorded and reported; the last good copy stays
        record.update(ok=False, error=f"{type(exc).__name__}: {exc}", kept_previous=target.exists())
    return record


def compile_cached(sources: list[dict], cache: Path, index: Path) -> dict[str, int]:
    present = [(s["name"], cache / f"{s['name']}.txt") for s in sources if (cache / f"{s['name']}.txt").exists()]
    return lists.compile_index(present, index)


def main(argv: list[str], fetcher: Optional[Callable[[str, int], bytes]] = None, sources_path: Path = SOURCES,
         cache: Path = LIST_CACHE, index: Path = lists.INDEX) -> int:
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    records = []
    if "--compile" not in argv:
        for src in sources:
            rec = update_source(src, cache, fetcher or fetch)
            records.append(rec)
            print(("ok     " if rec["ok"] else "FAILED ") + f"{src['name']}: "
                  + (f"{rec['domains']} domains" if rec["ok"] else f"{rec['error']} (kept previous: {rec['kept_previous']})"))
    counts = compile_cached(sources, cache, index)
    present = [cache / f"{s['name']}.txt" for s in sources if (cache / f"{s['name']}.txt").exists()]
    if present and index.exists():
        # The index is only as fresh as its oldest successful download (a source file is rewritten only by one).
        # Rebuilt from old copies after failed fetches, it must not look new, or the hook's staleness warning and
        # install.py --status would never show that the updates keep failing.
        oldest = min(path.stat().st_mtime for path in present)
        os.utime(index, (oldest, oldest))
    print(f"compiled {index}: " + (", ".join(f"{k} +{v}" for k, v in counts.items()) or "nothing cached"))
    found = lists.Lists(index=index)
    for host in CRITICAL_HOSTS:
        if found.blocked_by(host):
            print(f"FAILED a curated list blocks {host}, which the agent needs")
            return 1
        listed = lists.index_entries(index, host)
        if listed:
            print(f"note   {host} is on {', '.join(listed)}; lists/allow.txt overrides it")
    state = {"compiled": time.strftime("%Y-%m-%dT%H:%M:%S"), "counts": counts, "sources": records}
    (cache / "state.json").write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    failed = [r["name"] for r in records if not r["ok"]]
    if failed:
        print(f"FAILED {len(failed)} source(s): {', '.join(failed)}")
        return 1
    if not counts:
        print("FAILED nothing to compile")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
