# browser-shield — agent contract

## Gate (run before every commit)

```bash
/usr/bin/python3 -I -m unittest discover -s tests -t . && node --test tests/shield.test.js
```

## Rules

- **The hook runs under `/usr/bin/python3` (3.9) with `-I`, stdlib only.** No third-party imports, no syntax newer
  than 3.9 at runtime (`from __future__ import annotations` makes `X | None` hints safe).
- **The hook is live in every session the moment a file here is saved** (`~/.claude/settings.json` points at this
  checkout). Run the gate before saving a change to `hooks/` or `browser_shield/`, and never leave either half-edited.
- **A guard bug must be loud, never locking.** `hooks/guard.py` catches everything and answers with a
  `systemMessage`; keep imports inside `main()`'s `try`. An unreadable URL is still refused (`UnreadableURL` → deny).
- **A rewrite never carries `permissionDecision`.** `updatedInput` alone applies (verified live 2026-10-26, Claude
  Code 2.1.295 desktop); adding `"allow"` would skip the user's permission prompt.
- **`lists/ad-hosts.txt` and `AD_HOSTS` in `page/shield.js` are one list in two places**; `tests/test_lists.py` pins
  them. Edit both.
- **`page/shield.js` must leave local dev hosts alone** and must never hide or dismiss YouTube's enforcement notice.
- **No downloaded list is committed**: `cache/` is git-ignored; `scripts/update_lists.py` fetches only the URLs in
  `lists/sources.json`.
- Verifying a page-script change for real: open a monetized YouTube video in the Browser pane, run `browserShield()`,
  and read `adsSkipped` and the video's `currentTime` (README "Use").
