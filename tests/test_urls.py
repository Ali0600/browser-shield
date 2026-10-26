"""urls.parse must find the host a browser would navigate to. The oracle is Node's URL class (the WHATWG parser that
browsers follow): every vector in url_vectors.json is parsed by both, and they must agree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from browser_shield import urls

HERE = Path(__file__).resolve().parent
VECTORS = json.loads((HERE / "url_vectors.json").read_text(encoding="utf-8"))
# Inputs Node accepts and we refuse on purpose (fail closed): an empty label is not a host any list could name.
WE_REFUSE = {"https://a..b/", "https://evil.com\uff0e\uff0e/"}
# Inputs Node refuses and we read as a host anyway (none since the UTS 46 table; keep the mechanism for the next one).
WE_READ_MORE: dict = {}

NODE_SCRIPT = """
const v = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(v.map(s => { try { const u = new URL(s); return [u.protocol.slice(0, -1), u.hostname]; }
  catch (e) { return null; } })));
"""


def node_parse(vectors: list[str]) -> list:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is needed: it is the WHATWG URL oracle for this test")
    out = subprocess.run([node, "-e", NODE_SCRIPT], input=json.dumps(vectors), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class AgreesWithWhatwg(unittest.TestCase):
    def test_vectors_match_node(self):
        oracle = node_parse(VECTORS)
        self.assertEqual(len(oracle), len(VECTORS))
        compared = 0
        for raw, expected in zip(VECTORS, oracle):
            with self.subTest(url=raw):
                try:
                    t = urls.parse(raw)
                    mine = [t.scheme, t.host]
                except urls.UnreadableURL:
                    mine = None
                if raw in WE_REFUSE:
                    self.assertIsNone(mine)
                    self.assertIsNotNone(expected, "listed as refused-on-purpose but Node refuses it too")
                    continue
                if raw in WE_READ_MORE:
                    self.assertIsNone(expected, "listed as read-more but Node reads it too")
                    self.assertEqual(mine, WE_READ_MORE[raw])
                    continue
                if expected is not None:
                    expected = [expected[0], expected[1].rstrip(".")]  # one trailing dot is the same host
                self.assertEqual(mine, expected)
                compared += 1
        self.assertGreaterEqual(compared, 55)


SWEEP_SCRIPT = """
const out = {};
for (let cp = 0x80; cp <= 0x10ffff; cp++) {
  if (cp >= 0xd800 && cp <= 0xdfff) continue;
  try { out[cp] = new URL('https://x' + String.fromCodePoint(cp) + 'y.example/').hostname; } catch { out[cp] = null; }
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipIf(os.environ.get("BROWSER_SHIELD_SKIP_SWEEP") == "1", "mutation run of a file the sweep cannot see")
class EveryCodePoint(unittest.TestCase):
    def test_hosts_with_any_code_point_match_node(self):
        # The browser maps, drops or refuses each non-ASCII code point (UTS 46). A character the guard read
        # differently would be a way to reach a listed host unchecked: x<U+180F>y.example *is* xy.example.
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is the oracle for this test")
        out = subprocess.run([node, "-e", SWEEP_SCRIPT], capture_output=True, text=True, check=True, timeout=120)
        oracle = json.loads(out.stdout)
        self.assertGreater(len(oracle), 1_000_000)
        wrong, refused_more, read_more = [], 0, 0
        for cp_text, expected in oracle.items():
            try:
                mine = urls.parse(f"https://x{chr(int(cp_text))}y.example/").host
            except urls.UnreadableURL:
                mine = None
            if mine == expected:
                continue
            if mine is None:
                refused_more += 1  # the guard refuses a host the browser reaches: safe, but it costs the user
            elif expected is None:
                read_more += 1  # the browser refuses (bidi rule next to Latin letters): it goes nowhere
            else:
                wrong.append((f"U+{int(cp_text):04X}", expected, mine))  # two different hosts: a way past the guard
        self.assertEqual(wrong[:10], [], f"{len(wrong)} code points read as a different host than a browser's")
        self.assertLess(refused_more, 50, "the guard refuses many hosts browsers accept")
        self.assertLess(read_more, 5000, "far more refusals in the browser than the bidi rule explains")

    def test_committed_table_is_what_node_produces(self):
        node = shutil.which("node")
        gen = HERE.parent / "scripts" / "gen_uts46.js"
        run = subprocess.run([node, str(gen), "--check"], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)


class Readings(unittest.TestCase):
    def test_missing_scheme_defaults_to_https(self):
        # As the navigate tool did on 2026-10-26: example.com, //example.com/x, example.com:443/a all opened https.
        self.assertEqual(urls.interpretations("evil.com/x"), ["https://evil.com/x"])
        self.assertEqual(urls.interpretations("//evil.com"), ["https://evil.com"])
        self.assertEqual(urls.interpretations("\u00a0\ufeff evil.com\u2028"), ["https://evil.com"])

    def test_host_port_is_a_host_not_a_scheme(self):
        self.assertEqual(urls.interpretations("evil.com:8080/x"), ["https://evil.com:8080/x"])
        self.assertEqual(urls.interpretations("localhost:3000"), ["https://localhost:3000"])

    def test_credentials_or_an_app_scheme_are_read_both_ways(self):
        self.assertEqual(urls.interpretations("user:pass@example.com/up"), ["user:pass@example.com/up",
                                                                          "https://user:pass@example.com/up"])
        self.assertEqual(urls.interpretations("mailto:a@b.com"), ["mailto:a@b.com"])
        self.assertEqual(urls.interpretations("zoommtg:join?x=1"), ["zoommtg:join?x=1"])

    def test_local_paths(self):
        for raw in ("/etc/passwd", "~/.ssh/id_rsa", "./notes.html", "../x", "\\\\server\\share", " /tmp/x"):
            with self.subTest(raw=raw):
                self.assertTrue(urls.is_local_path(raw))
        for raw in ("//evil.com", "evil.com", "https://x.com/", "file:///etc/passwd"):
            with self.subTest(raw=raw):
                self.assertFalse(urls.is_local_path(raw))

    def test_userinfo_and_file_shares_are_recorded(self):
        self.assertTrue(urls.parse("https://good.com@evil.com/").userinfo)
        self.assertFalse(urls.parse("https://evil.com/@good.com").userinfo)
        self.assertEqual(urls.parse("file://server/share/x").file_host, "server")
        self.assertEqual(urls.parse("file:///etc/passwd").file_host, "")
        self.assertEqual(urls.parse("file://localhost/etc/passwd").file_host, "")

    def test_oversized_and_control_characters_are_unreadable(self):
        for raw in ("https://x.com/" + "a" * 9000, "https://x.com/a\x01b", "https://ev\ud800il.com/"):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(urls.UnreadableURL):
                    urls.parse(raw)

    def test_javascript_scheme_survives_whitespace_and_case(self):
        for raw in ("JaVaScRiPt:alert(1)", "  javascript:alert(1)", "java\tscript:alert(1)", "\x01javascript:x"):
            with self.subTest(raw=raw):
                readings = urls.interpretations(raw)
                self.assertEqual([urls.parse(r).scheme for r in readings], ["javascript"])

    def test_suffixes_stop_at_label_boundaries(self):
        self.assertEqual(urls.host_suffixes("a.b.evil.com"), ["a.b.evil.com", "b.evil.com", "evil.com", "com"])
        self.assertNotIn("evil.com", urls.host_suffixes("notevil.com"))
        self.assertEqual(urls.host_suffixes("192.168.1.1"), ["192.168.1.1"])


if __name__ == "__main__":
    unittest.main()
