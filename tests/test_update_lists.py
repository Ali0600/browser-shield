"""scripts/update_lists.py with a stub fetcher: nothing here touches the network."""

from __future__ import annotations

import fcntl
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
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

    def test_a_run_whose_fetches_fail_leaves_the_index_looking_old(self):
        # The hook warns when the index is 8 days old; a weekly run that rebuilds it from old copies must not
        # reset that clock, or failing updates are never seen outside the job's log.
        import os
        import time

        self.run_update({"https://a.example/list.txt": FEED, "https://b.example/list.txt": FEED})
        old = time.time() - 20 * 86400
        for name in ("feed-a", "feed-b"):
            os.utime(self.cache / f"{name}.txt", (old, old))
        code, _ = self.run_update({"https://a.example/list.txt": OSError("network is unreachable"),
                                   "https://b.example/list.txt": FEED})
        self.assertEqual(code, 1)
        self.assertAlmostEqual(self.index.stat().st_mtime, old, delta=5, msg="dated by feed-a's last good download")
        code, _ = self.run_update({"https://a.example/list.txt": FEED, "https://b.example/list.txt": FEED})
        self.assertEqual(code, 0)
        self.assertGreater(self.index.stat().st_mtime, time.time() - 60, "a fully good run makes it fresh")

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

    def test_if_stale_runs_only_for_missing_or_week_old_lists(self):
        bodies = {"https://a.example/list.txt": FEED, "https://b.example/list.txt": FEED}
        fetched = []

        def fetcher(url, max_bytes):
            fetched.append(url)
            return bodies[url].encode()

        def run():
            fetched.clear()
            with redirect_stdout(io.StringIO()) as out:
                code = update_lists.main(["--if-stale"], fetcher, self.sources, self.cache, self.index)
            return code, out.getvalue()

        code, out = run()  # no index yet
        self.assertEqual((code, len(fetched)), (0, 2), out)
        self.assertIn("missing or a week old", out)
        old = time.time() - 6.9 * 86400
        os.utime(self.index, (old, old))
        self.assertEqual(run(), (0, ""), "a six-day-old index is left alone, silently")
        self.assertEqual(fetched, [])
        old = time.time() - 7.1 * 86400
        os.utime(self.index, (old, old))
        code, out = run()
        self.assertEqual((code, len(fetched)), (0, 2), out)

    def test_a_second_run_leaves_while_another_holds_the_lock(self):
        self.cache.mkdir(parents=True)
        with open(self.cache / ".update.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, out = self.run_update({})  # any fetch would raise KeyError
        self.assertEqual(code, 0, out)
        self.assertIn("another list update is running", out)
        self.assertFalse(self.index.exists())


if __name__ == "__main__":
    unittest.main()
