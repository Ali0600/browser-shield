from __future__ import annotations

import unittest

from browser_shield import leaks

# Built from parts so this file never holds a string a secret scanner would flag as a real key.
FAKE = {
    "Anthropic API key": "sk-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "OpenAI API key": "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "GitHub token": "gh" + "p_" + "A" * 36,
    "GitHub fine-grained token": "github_" + "pat_" + "11ABCDEFG0" + "x" * 30,
    "AWS access key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "Slack token": "xo" + "xb-" + "1234567890-abcdefghij",
    "Stripe live key": "sk_" + "live_" + "a" * 24,
    "Google API key": "AI" + "za" + "B" * 35,
    "private key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "JSON web token": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N",
}


def make_card(prefix: str, length: int) -> str:
    """prefix padded with 3s and closed with the Luhn check digit."""
    body = (prefix + "3" * length)[: length - 1]
    for check in "0123456789":
        if leaks.luhn_ok(body + check):
            return body + check
    raise AssertionError("no check digit")


class FindsSecrets(unittest.TestCase):
    def test_each_kind_is_found_and_masked(self):
        for kind, secret in FAKE.items():
            with self.subTest(kind=kind):
                found = leaks.find_leaks(f"please paste {secret} here")
                self.assertEqual([f.kind for f in found], [kind])
                self.assertNotIn(secret, found[0].excerpt)

    def test_real_card_numbers_are_found(self):
        # Numbers in each network's range made Luhn-valid here, so the repo holds no real or published card.
        for prefix, length, brand, sep in (("4716", 16, "Visa", " "), ("2720", 16, "Mastercard", "-"),
                                           ("3712", 15, "American Express", ""), ("6011", 16, "Discover", " ")):
            number = make_card(prefix, length)
            shown = sep.join(number[i:i + 4] for i in range(0, length, 4))
            with self.subTest(number=shown):
                self.assertNotIn(number, leaks.TEST_CARDS)
                found = leaks.find_leaks(f"card: {shown}")
                self.assertEqual([f.kind for f in found], [f"{brand} card number"])
                self.assertEqual(found[0].excerpt, "…" + number[-4:])

    def test_luhn_failures_are_not_cards(self):
        number = make_card("4716", 16)
        broken = number[:-1] + str((int(number[-1]) + 1) % 10)
        self.assertEqual(leaks.find_leaks(broken), [])

    def test_ssn_is_found(self):
        self.assertEqual([f.kind for f in leaks.find_leaks("ssn 123-45-6789")], ["US Social Security number"])


class LeavesOrdinaryTextAlone(unittest.TestCase):
    def test_published_test_cards_pass(self):
        for number in ("4242 4242 4242 4242", "5555555555554444", "378282246310005"):
            with self.subTest(number=number):
                self.assertEqual(leaks.find_leaks(number), [])

    def test_test_cards_are_luhn_valid(self):
        # Every allowlisted number must pass Luhn, or listing it does nothing (and hints at a typo).
        for number in leaks.TEST_CARDS:
            with self.subTest(number=number):
                self.assertTrue(leaks.luhn_ok(number))
                self.assertIsNotNone(leaks.card_brand(number))

    def test_test_cards_are_blocked_when_not_allowed(self):
        self.assertEqual(len(leaks.find_leaks("4242424242424242", allow_test_cards=False)), 1)

    def test_long_ids_are_not_cards(self):
        # Luhn-valid on purpose: one in ten ids passes Luhn, so only the card-network check keeps these out.
        ids = [make_card("11845642134752993", 19), make_card("18493755271953739", 19),  # Discord, X ids
               make_card("9123456789012", 16), make_card("7000000000000", 16), make_card("100000000000", 13)]
        for number in ids:
            with self.subTest(id=number):
                self.assertTrue(leaks.luhn_ok(number))
                self.assertEqual(leaks.find_leaks(f"id {number}"), [])
        ordinary = [
            "5891436127",  # a Roblox universe id
            "https://www.youtube.com/watch?v=RdxyGuDY6ow&t=161s",
            "Grow a Garden 1,000,000,000,000 Sheckles",
            "2026-10-26 12:00:00",
            "sk-learn is a library; ghp_short; AKIA too short",
            "phone 415-555-0132",
        ]
        for text in ordinary:
            with self.subTest(text=text):
                self.assertEqual(leaks.find_leaks(text), [])

    def test_non_strings_are_ignored(self):
        for value in (None, 42, True, ["sk-ant-api03-x"]):
            self.assertEqual(leaks.find_leaks(value), [])


class Luhn(unittest.TestCase):
    def test_known_values(self):
        self.assertTrue(leaks.luhn_ok("79927398713"))
        self.assertFalse(leaks.luhn_ok("79927398710"))


if __name__ == "__main__":
    unittest.main()
