"""scripts/update_lists.py with a stub fetcher: nothing here touches the network."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update_lists  # noqa: E402

FEED = "\n".join(f"0.0.0.0 bad{i}.example" for i in range(30)) + "\n"


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sources = self.tmp / "sources.json"
        self.sources.write_text(json.dumps([
            {"name": "feed-a", "url": "https://a.example/list.txt", "min_entries": 20},
            {"name": "feed-b", "url": "https://b.example/list.txt", "min_entries": 20},
        ]))
        self.cache = self.tmp / "cache" / "lists"
        self.index = self.tmp / "cache" / "blocklist.sqlite"

    def run_update(self, bodies: dict, argv=()):
        def fetcher(url, max_bytes):
            body = bodies[url]
            if isinstance(body, Exception):
                raise body
            return body.encode()

        out = io.StringIO()
        with redirect_stdout(out):
            code = update_lists.main(list(argv), fetcher, self.sources, self.cache, self.index)
        return code, out.getvalue()

    def domains(self):
        return dict(sqlite3.connect(self.index).execute("SELECT d, src FROM domains"))

    def test_good_fetches_compile(self):
        code, out = self.run_update({"https://a.example/list.txt": FEED,
                                     "https://b.example/list.txt": FEED.replace("bad", "worse")})
        self.assertEqual(code, 0, out)
        d = self.domains()
        self.assertEqual(len(d), 60)
        self.assertEqual(d["bad3.example"], "feed-a")
        state = json.loads((self.cache / "state.json").read_text())
        self.assertEqual(state["counts"], {"feed-a": 30, "feed-b": 30})

    def test_a_failed_or_tiny_fetch_keeps_the_last_good_copy_and_fails_loudly(self):
        self.run_update({"https://a.example/list.txt": FEED, "https://b.example/list.txt": FEED})
        for bad in (OSError("connection reset"), "", "0.0.0.0 only-one.example\n"):
            with self.subTest(bad=repr(bad)[:30]):
                code, out = self.run_update({"https://a.example/list.txt": bad, "https://b.example/list.txt": FEED})
                self.assertEqual(code, 1)
                self.assertIn("FAILED feed-a", out)
                self.assertIn("kept previous: True", out)
                self.assertEqual(self.domains()["bad3.example"], "feed-a", "the old copy must still be compiled")

    def test_compile_only_does_not_fetch(self):
        self.run_update({"https://a.example/list.txt": FEED, "https://b.example/list.txt": FEED})
        code, out = self.run_update({}, argv=["--compile"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("ok     feed", out)

    def test_nothing_cached_fails(self):
        code, out = self.run_update({}, argv=["--compile"])
        self.assertEqual(code, 1)
        self.assertIn("nothing", out)

    def test_only_https_sources(self):
        with self.assertRaises(ValueError):
            update_lists.fetch("http://a.example/list.txt", 10)


if __name__ == "__main__":
    unittest.main()
