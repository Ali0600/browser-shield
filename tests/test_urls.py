"""urls.parse must find the host a browser would navigate to. The oracle is Node's URL class (the WHATWG parser that
browsers follow): every vector in url_vectors.json is parsed by both, and they must agree."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from browser_shield import urls

HERE = Path(__file__).resolve().parent
VECTORS = json.loads((HERE / "url_vectors.json").read_text(encoding="utf-8"))
# Inputs Node accepts and we refuse on purpose (fail closed): an empty label is not a host any list could name.
WE_REFUSE = {"https://a..b/"}

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
                if expected is not None:
                    expected = [expected[0], expected[1].rstrip(".")]  # one trailing dot is the same host
                self.assertEqual(mine, expected)
                compared += 1
        self.assertGreaterEqual(compared, 30)


class Readings(unittest.TestCase):
    def test_missing_scheme_defaults_to_https(self):
        self.assertEqual(urls.interpretations("evil.com/x"), ["https://evil.com/x"])
        self.assertEqual(urls.interpretations("//evil.com"), ["https:////evil.com"])
        self.assertEqual(urls.parse(urls.interpretations("//evil.com")[0]).host, "evil.com")

    def test_host_port_is_read_both_ways(self):
        self.assertEqual(urls.interpretations("evil.com:8080/x"), ["evil.com:8080/x", "https://evil.com:8080/x"])

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
