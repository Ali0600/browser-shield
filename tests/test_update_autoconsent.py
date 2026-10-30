"""scripts/update_autoconsent.py with a stub fetcher and tarballs built here: nothing touches the network."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update_autoconsent as ua  # noqa: E402

VERSION = "9.9.9"
TARBALL_URL = f"{ua.REGISTRY}/-/autoconsent-{VERSION}.tgz"
GOOD = {
    "package/dist/autoconsent.cjs.js": b'"use strict";\nmodule.exports = {};\n',
    "package/rules/compact-rules.json": b'{"v":1,"s":[],"r":[]}',
    "package/LICENSE": b"Mozilla Public License Version 2.0\n",
    "package/README.md": b"not vendored\n",
}


def tarball(entries) -> bytes:
    """entries: (name, bytes) for a file, or (name, ('symlink', target)) for a link, in this order."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in entries:
            info = tarfile.TarInfo(name)
            if isinstance(body, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, body[1]
                tar.addfile(info)
            else:
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.dest = self.tmp / "vendor"

    def fetcher(self, tgz: bytes, meta: dict = None):
        meta = meta or {"version": VERSION, "gitHead": "abc123",
                        "dist": {"tarball": TARBALL_URL, "integrity": integrity(tgz)}}
        urls = []

        def fetch(url, max_bytes):
            urls.append(url)
            if url == f"{ua.REGISTRY}/{VERSION}":
                return json.dumps(meta).encode()
            if url == meta["dist"]["tarball"]:
                return tgz
            raise AssertionError(f"unexpected fetch {url}")
        return fetch, urls

    def test_vendors_the_three_files_byte_for_byte_and_records_them(self):
        tgz = tarball(GOOD.items())
        fetch, urls = self.fetcher(tgz)
        source = ua.update(VERSION, self.dest, fetch)
        self.assertEqual(urls, [f"{ua.REGISTRY}/{VERSION}", TARBALL_URL])
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(list(ua.FILES) + [ua.SOURCE]))
        for name, member in ua.FILES.items():
            self.assertEqual((self.dest / name).read_bytes(), GOOD[member])
            self.assertEqual(source["files"][name], hashlib.sha256(GOOD[member]).hexdigest())
        self.assertEqual(json.loads((self.dest / ua.SOURCE).read_text()), source)
        self.assertEqual(source["integrity"], integrity(tgz))
        self.assertEqual(ua.check(self.dest), [])

    def test_a_tarball_whose_sha512_differs_from_the_registry_is_refused(self):
        tgz = tarball(GOOD.items())
        fetch, _ = self.fetcher(tgz, {"version": VERSION, "dist": {"tarball": TARBALL_URL,
                                                                    "integrity": integrity(tgz + b"x")}})
        with self.assertRaisesRegex(ValueError, "does not match"):
            ua.update(VERSION, self.dest, fetch)
        self.assertFalse(self.dest.exists())

    def test_only_sha512_integrity_is_accepted(self):
        tgz = tarball(GOOD.items())
        sha1 = "sha1-" + base64.b64encode(hashlib.sha1(tgz).digest()).decode()
        fetch, _ = self.fetcher(tgz, {"version": VERSION, "dist": {"tarball": TARBALL_URL, "integrity": sha1}})
        with self.assertRaisesRegex(ValueError, "only sha512"):
            ua.update(VERSION, self.dest, fetch)

    def test_a_tarball_url_off_the_registry_is_refused(self):
        tgz = tarball(GOOD.items())
        meta = {"version": VERSION, "dist": {"tarball": "https://evil.example/x.tgz", "integrity": integrity(tgz)}}
        fetch, urls = self.fetcher(tgz, meta)
        with self.assertRaisesRegex(ValueError, "does not describe"):
            ua.update(VERSION, self.dest, fetch)
        self.assertEqual(urls, [f"{ua.REGISTRY}/{VERSION}"])

    def test_only_an_exact_version_is_fetched(self):
        for bad in ("latest", "17", "^17.1.0", "17.1.0/../x"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "exact version"):
                ua.update(bad, self.dest, lambda url, n: self.fail(f"fetched {url}"))

    def test_a_link_under_a_wanted_name_is_refused(self):
        entries = dict(GOOD)
        entries["package/LICENSE"] = ("symlink", "/etc/passwd")
        tgz = tarball(entries.items())
        fetch, _ = self.fetcher(tgz)
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            ua.update(VERSION, self.dest, fetch)
        self.assertFalse(self.dest.exists())

    def test_a_wanted_name_twice_is_refused(self):
        entries = list(GOOD.items()) + [("package/LICENSE", b"a second licence\n")]
        fetch, _ = self.fetcher(tarball(entries))
        with self.assertRaisesRegex(ValueError, "appears twice"):
            ua.update(VERSION, self.dest, fetch)

    def test_traversal_members_are_never_written(self):
        entries = list(GOOD.items()) + [("package/../escape.txt", b"outside\n"), ("../../escape.txt", b"outside\n")]
        fetch, _ = self.fetcher(tarball(entries))
        ua.update(VERSION, self.dest, fetch)
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*") if p.is_file()),
                         sorted(list(ua.FILES) + [ua.SOURCE]))

    def test_a_missing_member_is_refused(self):
        entries = {k: v for k, v in GOOD.items() if k != "package/rules/compact-rules.json"}
        fetch, _ = self.fetcher(tarball(entries.items()))
        with self.assertRaisesRegex(ValueError, "lacks package/rules/compact-rules.json"):
            ua.update(VERSION, self.dest, fetch)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        tgz = tarball(GOOD.items())
        meta = {"version": VERSION, "dist": {"tarball": TARBALL_URL, "integrity": integrity(tgz)}}
        ua.update(VERSION, self.tmp, lambda url, n: json.dumps(meta).encode() if url.endswith(VERSION) else tgz)

    def test_an_edited_byte_is_drift(self):
        path = self.tmp / "autoconsent.cjs.js"
        path.write_bytes(path.read_bytes().replace(b"{}", b"{x:1}"))
        self.assertEqual(ua.check(self.tmp), ["autoconsent.cjs.js differs from the sha256 in SOURCE.json"])

    def test_a_missing_or_extra_file_is_drift(self):
        (self.tmp / "LICENSE").unlink()
        (self.tmp / "notes.txt").write_text("x")
        self.assertEqual(ua.check(self.tmp), ["LICENSE is missing or not a plain file",
                                              "files that are not part of the vendored copy: notes.txt"])

    def test_the_committed_copy_matches_its_source_record(self):
        self.assertEqual(ua.check(), [])

    def test_notice_names_the_vendored_version_its_source_and_its_licence(self):
        source = json.loads((ua.DEST / ua.SOURCE).read_text())
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn(f"autoconsent version {source['version']}", notice)
        self.assertIn(source["gitHead"], notice)
        self.assertIn(source["source"], notice)
        self.assertIn("page/vendor/autoconsent/LICENSE", notice)
        self.assertTrue((ua.DEST / "LICENSE").read_text().startswith("Mozilla Public License Version 2.0"))


if __name__ == "__main__":
    unittest.main()
