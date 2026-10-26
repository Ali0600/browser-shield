from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_shield import policy
from browser_shield.lists import Lists, compile_index

NAV = "mcp__Claude_Browser__navigate"
CLOUD_NAV = "mcp__remote-devices__Claude_Browser__navigate"
CHROME_NAV = "mcp__claude-in-chrome__navigate"
JS = "mcp__Claude_Browser__javascript_tool"
BATCH = "mcp__Claude_Browser__browser_batch"
KEY = "sk-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def lists_with(block: list[str] = (), allow: list[str] = ("youtube.com", "google.com"), index: list[str] = ()) -> Lists:
    """A Lists over a temporary folder: curated ad hosts, allow list, and optionally a compiled index."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "ad-hosts.txt").write_text("\n".join(["doubleclick.net", "adservice.google.com", *block]) + "\n")
    (tmp / "allow.txt").write_text("\n".join(allow) + "\n")
    idx = tmp / "index.sqlite"
    if index:
        (tmp / "feed.txt").write_text("\n".join(index) + "\n")
        compile_index([("test-feed", tmp / "feed.txt")], idx)
    return Lists(lists_dir=tmp, index=idx)


def decide(tool: str, tool_input: dict, lists: Lists = None) -> policy.Verdict:
    return policy.evaluate(tool, tool_input, lists or lists_with())


class Navigation(unittest.TestCase):
    def test_ordinary_pages_pass(self):
        for url in ("https://www.youtube.com/watch?v=RdxyGuDY6ow", "growagarden.fandom.com/wiki/Seeds",
                    "https://en.wikipedia.org/wiki/Roblox", "back", "forward", "about:blank",
                    "http://localhost:5173/", "http://127.0.0.1:8000/x"):
            with self.subTest(url=url):
                v = decide(NAV, {"url": url})
                self.assertEqual(v.decision, policy.ALLOW, v.reasons)

    def test_every_browser_prefix_is_guarded(self):
        for tool in (NAV, CLOUD_NAV, CHROME_NAV, "WebFetch"):
            with self.subTest(tool=tool):
                self.assertEqual(decide(tool, {"url": "https://ad.doubleclick.net/x"}).decision, policy.DENY)

    def test_other_tools_are_not_judged(self):
        self.assertEqual(decide("Bash", {"command": f"echo {KEY}"}).decision, policy.ALLOW)
        self.assertEqual(decide("mcp__Claude_Browser__get_page_text", {"url": "javascript:x"}).decision, policy.ALLOW)

    def test_blocklisted_hosts_and_their_subdomains_are_denied(self):
        for url in ("https://doubleclick.net/", "https://AD.DoubleClick.NET./x", "https://good.com@ad.doubleclick.net/",
                    "https:\\\\ad.doubleclick.net", "ad.doubleclick.net/pagead", "https://%61d.doubleclick.net/"):
            with self.subTest(url=url):
                v = decide(NAV, {"url": url})
                self.assertEqual(v.decision, policy.DENY, v.reasons)
                self.assertIn("blocklist", " ".join(v.reasons))

    def test_label_boundary(self):
        self.assertEqual(decide(NAV, {"url": "https://notdoubleclick.net/"}).decision, policy.ALLOW)

    def test_curated_block_beats_the_allow_list(self):
        # google.com is allowed, adservice.google.com is ours to block.
        self.assertEqual(decide(NAV, {"url": "https://adservice.google.com/x"}).decision, policy.DENY)
        self.assertEqual(decide(NAV, {"url": "https://www.google.com/search?q=x"}).decision, policy.ALLOW)

    def test_allow_list_beats_a_downloaded_list(self):
        lists = lists_with(index=["www.youtube.com", "malware.example"])
        self.assertEqual(decide(NAV, {"url": "https://www.youtube.com/"}, lists).decision, policy.ALLOW)
        v = decide(NAV, {"url": "https://cdn.malware.example/a"}, lists)
        self.assertEqual(v.decision, policy.DENY)
        self.assertIn("test-feed", " ".join(v.reasons))

    def test_ad_click_paths(self):
        for url in ("https://www.googleadservices.com/pagead/aclk?sa=L", "https://www.google.com/aclk?sa=l&ai=x",
                    "https://www.youtube.com/pagead/paralleladview?x=1"):
            with self.subTest(url=url):
                self.assertEqual(decide(NAV, {"url": url}, lists_with(index=["googleadservices.com"])).decision,
                                 policy.DENY)

    def test_dangerous_schemes(self):
        for url in ("javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html,<script>x</script>",
                    "file:///etc/passwd", "zoommtg://zoom.us/join", "itms-services://?action=download-manifest",
                    "vbscript:x", "blob:https://evil.com/uuid", "view-source:https://x.com", "chrome://settings"):
            with self.subTest(url=url):
                v = decide(NAV, {"url": url})
                self.assertEqual(v.decision, policy.DENY, v.reasons)

    def test_unreadable_urls_fail_closed(self):
        for url in ("https://ev%00il.com/", "https://evil.com:99999/", "http://1.2.3.4.5/", "", None, 42):
            with self.subTest(url=url):
                self.assertEqual(decide(NAV, {"url": url}).decision, policy.DENY)

    def test_downloads_ask_the_user(self):
        for url in ("https://example.org/setup.dmg", "https://example.org/tool.pkg?x=1", "https://ex.org/a.ZIP#frag",
                    "https://github.com/x/y/releases/download/v1/app.dmg"):
            with self.subTest(url=url):
                v = decide(NAV, {"url": url})
                self.assertEqual(v.decision, policy.ASK, v.reasons)
                self.assertIn("download", " ".join(v.reasons))

    def test_bare_public_ips_ask_private_ones_pass(self):
        self.assertEqual(decide(NAV, {"url": "http://45.33.32.156/"}).decision, policy.ASK)
        self.assertEqual(decide(NAV, {"url": "http://3232235777/"}).decision, policy.ALLOW)  # 192.168.1.1
        self.assertEqual(decide(NAV, {"url": "http://[::1]:3000/"}).decision, policy.ALLOW)

    def test_secrets_in_urls_are_denied(self):
        v = decide(NAV, {"url": f"https://collect.example/?k={KEY}"})
        self.assertEqual(v.decision, policy.DENY)
        self.assertNotIn(KEY, " ".join(v.reasons))

    def test_preview_start_by_name_is_not_a_url(self):
        self.assertEqual(decide("mcp__Claude_Browser__preview_start", {"name": "dev"}).decision, policy.ALLOW)
        self.assertEqual(decide("mcp__Claude_Browser__preview_start", {"url": "javascript:x"}).decision, policy.DENY)


class TypingAndScripts(unittest.TestCase):
    def test_typing_a_secret_is_denied(self):
        v = decide("mcp__Claude_Browser__computer", {"action": "type", "text": f"key: {KEY}"})
        self.assertEqual(v.decision, policy.DENY)
        self.assertEqual(decide("mcp__Claude_Browser__computer", {"action": "type", "text": "roblox simulator"}).decision,
                         policy.ALLOW)
        self.assertEqual(decide("mcp__Claude_Browser__computer", {"action": "screenshot"}).decision, policy.ALLOW)

    def test_form_input_secret_is_denied(self):
        self.assertEqual(decide("mcp__claude-in-chrome__form_input", {"ref": "r1", "value": KEY}).decision, policy.DENY)
        self.assertEqual(decide("mcp__Claude_Browser__form_input", {"ref": "r1", "value": True}).decision, policy.ALLOW)

    def test_script_navigation_to_a_bad_host_is_denied(self):
        for text in ("location.href = 'https://ad.doubleclick.net/x'", 'window.open("javascript:alert(1)")',
                     "location.assign(`https://doubleclick.net`)"):
            with self.subTest(text=text):
                self.assertEqual(decide(JS, {"action": "javascript_exec", "text": text}).decision, policy.DENY)

    def test_ordinary_scripts_pass(self):
        frame_js = (Path(__file__).resolve().parent / "fixtures" / "frame.js").read_text()
        for text in ("document.title", "location.href = '/watch?v=x'", frame_js, policy.SHIELD_JS.read_text()):
            with self.subTest(text=text[:40]):
                self.assertEqual(decide(JS, {"action": "javascript_exec", "text": text}).decision, policy.ALLOW)

    def test_script_secret_is_denied(self):
        v = decide(JS, {"action": "javascript_exec", "text": f"document.querySelector('input').value = '{KEY}'"})
        self.assertEqual(v.decision, policy.DENY)


class Batches(unittest.TestCase):
    def test_one_bad_item_denies_the_batch_and_names_it(self):
        actions = [{"name": "navigate", "input": {"url": "https://www.youtube.com/"}},
                   {"name": "computer", "input": {"action": "screenshot"}},
                   {"name": "navigate", "input": {"url": "https://doubleclick.net/"}}]
        v = decide(BATCH, {"actions": actions})
        self.assertEqual(v.decision, policy.DENY)
        self.assertIn("batch item 3", " ".join(v.reasons))

    def test_deny_outranks_ask(self):
        actions = [{"name": "navigate", "input": {"url": "https://example.org/a.dmg"}},
                   {"name": "computer", "input": {"action": "type", "text": KEY}}]
        self.assertEqual(decide(BATCH, {"actions": actions}).decision, policy.DENY)

    def test_malformed_batches_do_not_crash(self):
        for inp in ({"actions": None}, {"actions": [None, 3, {"name": "navigate"}, {"input": []}]}, {}):
            with self.subTest(inp=inp):
                self.assertEqual(decide(BATCH, inp).decision, policy.ALLOW)


class Rewrite(unittest.TestCase):
    def test_shield_call_expands_to_the_page_script(self):
        script = policy.SHIELD_JS.read_text()
        for text in ("browserShield()", "  browserShield();\n"):
            with self.subTest(text=text):
                out = policy.rewrite(JS, {"action": "javascript_exec", "text": text, "tabId": "t1"})
                self.assertEqual(out, {"action": "javascript_exec", "text": script, "tabId": "t1"})

    def test_other_scripts_are_untouched(self):
        for text in ("browserShield().status", "x = browserShield()", "document.title"):
            self.assertIsNone(policy.rewrite(JS, {"action": "javascript_exec", "text": text}))

    def test_batch_items_expand_and_others_stay(self):
        actions = [{"name": "navigate", "input": {"url": "https://www.youtube.com/watch?v=x"}},
                   {"name": "javascript_tool", "input": {"action": "javascript_exec", "text": "browserShield()"}},
                   {"name": "javascript_tool", "input": {"action": "javascript_exec", "text": "1+1"}}]
        out = policy.rewrite(BATCH, {"actions": actions})
        self.assertEqual(out["actions"][0], actions[0])
        self.assertEqual(out["actions"][1]["input"]["text"], policy.SHIELD_JS.read_text())
        self.assertEqual(out["actions"][2], actions[2])
        self.assertEqual(actions[1]["input"]["text"], "browserShield()", "the caller's input must not be mutated")

    def test_only_browser_tools_are_rewritten(self):
        self.assertIsNone(policy.rewrite("Bash", {"text": "browserShield()"}))


class Reminder(unittest.TestCase):
    def test_youtube_without_the_shield_gets_a_reminder(self):
        self.assertEqual(policy.needs_shield_reminder(NAV, {"url": "https://www.youtube.com/watch?v=x"}),
                         "www.youtube.com")
        self.assertEqual(policy.needs_shield_reminder("mcp__Claude_Browser__preview_start",
                                                      {"url": "youtube.com/watch?v=x"}), "youtube.com")

    def test_no_reminder_elsewhere_or_when_the_batch_runs_the_shield(self):
        self.assertIsNone(policy.needs_shield_reminder(NAV, {"url": "https://en.wikipedia.org/"}))
        self.assertIsNone(policy.needs_shield_reminder(NAV, {"url": "https://notyoutube.com/"}))
        actions = [{"name": "navigate", "input": {"url": "https://www.youtube.com/watch?v=x"}},
                   {"name": "javascript_tool", "input": {"action": "javascript_exec", "text": "browserShield()"}}]
        self.assertIsNone(policy.needs_shield_reminder(BATCH, {"actions": actions}))

    def test_a_navigation_after_the_shield_needs_it_again(self):
        actions = [{"name": "javascript_tool", "input": {"action": "javascript_exec", "text": "browserShield()"}},
                   {"name": "navigate", "input": {"url": "https://www.youtube.com/watch?v=y"}}]
        self.assertEqual(policy.needs_shield_reminder(BATCH, {"actions": actions}), "www.youtube.com")


if __name__ == "__main__":
    unittest.main()
