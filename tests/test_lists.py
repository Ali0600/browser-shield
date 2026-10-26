from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from browser_shield import lists, policy


class ParseLine(unittest.TestCase):
    def test_formats(self):
        cases = {
            "0.0.0.0 evil.example": "evil.example",
            "127.0.0.1\tEVIL.example.  # comment": "evil.example",
            ":: ip6.evil.example": "ip6.evil.example",
            "||ads.evil.example^": "ads.evil.example",
            "||ads.evil.example^$third-party": "ads.evil.example",
            "*.wild.example": "wild.example",
            "plain.example": "plain.example",
            "under_score.example": "under_score.example",
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(lists.parse_line(line), expected)

    def test_rejects(self):
        for line in ("", "# comment", "! adblock comment", "0.0.0.0 localhost", "0.0.0.0 0.0.0.0", "localhost",
                     "1.2.3.4", "||evil.example/path^", "example.com/path", "two words", "-bad.example",
                     "a" * 64 + ".example", "nodot", "evil.example##.ad-banner"):
            with self.subTest(line=line):
                self.assertIsNone(lists.parse_line(line))


class Index(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("0.0.0.0 bad.example\n0.0.0.0 dup.example\n")
        (self.tmp / "b.txt").write_text("||dup.example^\n||other.example^\n# x\n")
        self.index = self.tmp / "cache" / "blocklist.sqlite"

    def test_counts_and_first_list_wins_duplicates(self):
        counts = lists.compile_index([("a", self.tmp / "a.txt"), ("b", self.tmp / "b.txt")], self.index)
        self.assertEqual(counts, {"a": 2, "b": 1})
        con = sqlite3.connect(self.index)
        self.assertEqual(dict(con.execute("SELECT d, src FROM domains")),
                         {"bad.example": "a", "dup.example": "a", "other.example": "b"})

    def test_lookup_by_suffix_through_the_index(self):
        lists.compile_index([("a", self.tmp / "a.txt")], self.index)
        (self.tmp / "allow.txt").write_text("")
        found = lists.Lists(lists_dir=self.tmp, index=self.index)
        self.assertEqual(found.blocked_by("x.y.bad.example"), ("bad.example", "a"))
        self.assertIsNone(found.blocked_by("notbad.example"))

    def test_rebuild_replaces_the_old_index_whole(self):
        lists.compile_index([("a", self.tmp / "a.txt")], self.index)
        lists.compile_index([("b", self.tmp / "b.txt")], self.index)
        con = sqlite3.connect(self.index)
        self.assertEqual(sorted(d for (d,) in con.execute("SELECT d FROM domains")), ["dup.example", "other.example"])
        self.assertFalse(self.index.with_suffix(".tmp").exists())


class CuratedLists(unittest.TestCase):
    def test_page_script_carries_the_same_ad_hosts(self):
        # Two copies of one list drift; this pins them together.
        text = (lists.LISTS_DIR / "ad-hosts.txt").read_text()
        from_file = sorted(lists.parse_line(l) for l in text.splitlines() if lists.parse_line(l))
        js = policy.SHIELD_JS.read_text()
        block = re.search(r"const AD_HOSTS = \[(.*?)\];", js, re.S)
        self.assertIsNotNone(block, "AD_HOSTS not found in page/shield.js")
        from_js = sorted(re.findall(r"'([^']+)'", block.group(1)))
        self.assertGreater(len(from_file), 10)
        self.assertEqual(from_js, from_file)

    def test_allow_list_names_no_ad_host(self):
        allow = lists.read_list(lists.LISTS_DIR / "allow.txt")
        ads = lists.read_list(lists.LISTS_DIR / "ad-hosts.txt")
        self.assertIn("youtube.com", allow)
        self.assertEqual(allow & ads, set())

    def test_youtube_needs_are_never_on_the_curated_block_list(self):
        real = lists.Lists()
        for host in ("www.youtube.com", "i.ytimg.com", "rr1---sn-abc.googlevideo.com", "youtubei.googleapis.com",
                     "yt3.ggpht.com", "www.google.com", "www.gstatic.com", "accounts.google.com"):
            with self.subTest(host=host):
                self.assertIsNone(real.blocked_by(host))


if __name__ == "__main__":
    unittest.main()
