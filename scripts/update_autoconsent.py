"""Vendor DuckDuckGo's autoconsent (the engine that rejects cookie banners) into page/vendor/autoconsent/.

    /usr/bin/python3 -I scripts/update_autoconsent.py VERSION   # download that version from npm (a download: ask first)
    /usr/bin/python3 -I scripts/update_autoconsent.py --check   # the vendored files match SOURCE.json, no network

It fetches the package tarball from the npm registry, refuses it unless its sha512 equals the registry's `integrity`,
and keeps three files byte for byte: the library build, the compact rules and the MPL-2.0 licence. SOURCE.json
records where they came from and their sha256, which the tests and the guard's expansion rely on. Nothing in the
tarball is unpacked to disk: only those three members are read, and a link or a duplicate under their names is refused.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "page" / "vendor" / "autoconsent"
REGISTRY = "https://registry.npmjs.org/@duckduckgo/autoconsent"
# Vendored name -> the member it comes from in the npm tarball.
FILES = {
    "autoconsent.cjs.js": "package/dist/autoconsent.cjs.js",
    "compact-rules.json": "package/rules/compact-rules.json",
    "LICENSE": "package/LICENSE",
}
SOURCE = "SOURCE.json"
MAX_METADATA = 2_000_000
MAX_TARBALL = 30_000_000
MAX_FILE = 5_000_000


def fetch(url: str, max_bytes: int, timeout: float = 60.0) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "browser-shield-autoconsent-update/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # TLS verified by the default context
        if not resp.geturl().startswith("https://"):
            raise ValueError(f"redirected off https: {resp.geturl()}")
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"{url} is larger than {max_bytes} bytes")
    return body


def check_integrity(data: bytes, integrity: str) -> None:
    """npm's Subresource-Integrity string, `sha512-<base64>`; anything else is refused."""
    algo, _, digest = str(integrity).partition("-")
    if algo != "sha512" or not digest:
        raise ValueError(f"unexpected integrity {integrity!r}: only sha512 is accepted")
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != digest:
        raise ValueError("the tarball's sha512 does not match the registry's integrity: refusing it")


def members(tarball: bytes) -> dict:
    """The three wanted files from the tarball, read in memory; links and duplicates under their names are refused."""
    wanted = {member: name for name, member in FILES.items()}
    found: dict = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for info in tar:
            if info.name not in wanted:
                continue
            name = wanted[info.name]
            if name in found:
                raise ValueError(f"{info.name} appears twice in the tarball")
            if not info.isreg():
                raise ValueError(f"{info.name} is not a regular file in the tarball")
            if info.size > MAX_FILE:
                raise ValueError(f"{info.name} is larger than {MAX_FILE} bytes")
            fh = tar.extractfile(info)
            found[name] = fh.read() if fh else b""
    missing = sorted(set(FILES) - set(found))
    if missing:
        raise ValueError(f"the tarball lacks {', '.join(FILES[m] for m in missing)}")
    return found


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def update(version: str, dest: Path = DEST, fetcher: Callable[[str, int], bytes] = fetch) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"{version!r} is not an exact version (like 17.1.0)")
    meta = json.loads(fetcher(f"{REGISTRY}/{version}", MAX_METADATA))
    dist = meta.get("dist") or {}
    tarball_url = str(dist.get("tarball", ""))
    if meta.get("version") != version or not tarball_url.startswith(REGISTRY + "/-/"):
        raise ValueError(f"the registry's answer for {version} does not describe that version's tarball")
    tarball = fetcher(tarball_url, MAX_TARBALL)
    check_integrity(tarball, dist.get("integrity", ""))
    files = members(tarball)
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (dest / name).write_bytes(data)
    source = {
        "package": "@duckduckgo/autoconsent",
        "version": version,
        "tarball": tarball_url,
        "integrity": dist["integrity"],
        "gitHead": meta.get("gitHead", ""),
        "source": f"https://github.com/duckduckgo/autoconsent/tree/v{version}",
        "files": {name: sha256(data) for name, data in sorted(files.items())},
    }
    (dest / SOURCE).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return source


def check(dest: Path = DEST) -> list:
    """Each problem with the vendored copy, as a line; empty when it matches SOURCE.json exactly."""
    try:
        source = json.loads((dest / SOURCE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{dest / SOURCE} cannot be read: {exc}"]
    problems = []
    recorded = source.get("files") or {}
    if set(recorded) != set(FILES):
        problems.append(f"SOURCE.json lists {sorted(recorded)}, expected {sorted(FILES)}")
    for name in sorted(FILES):
        path = dest / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"{name} is missing or not a plain file")
        elif sha256(path.read_bytes()) != recorded.get(name):
            problems.append(f"{name} differs from the sha256 in SOURCE.json")
    extra = sorted(p.name for p in dest.iterdir() if p.name not in FILES and p.name != SOURCE) if dest.is_dir() else []
    if extra:
        problems.append(f"files that are not part of the vendored copy: {', '.join(extra)}")
    return problems


def main(argv: list) -> int:
    if argv == ["--check"]:
        problems = check()
        for p in problems:
            print(f"DRIFT {p}")
        return 1 if problems else 0
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source = update(argv[0])
    print(f"vendored {source['package']} {source['version']} (gitHead {source['gitHead'][:12]}) into {DEST}")
    for name, digest in source["files"].items():
        print(f"  {name}  sha256 {digest}  {(DEST / name).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
