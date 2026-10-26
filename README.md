# browser-shield

Claude's own ad blocker and harm guard for the browsers it drives: the Claude desktop app's built-in Browser pane
and Claude in Chrome. The pane cannot load extensions and has no ad-blocking setting (an open request:
[anthropics/claude-code#104030](https://github.com/anthropics/claude-code/issues/104030)), so a YouTube pre-roll
used to stall every visual review until it ran out. browser-shield works from the two places Claude can act:

- **A guard hook** (`hooks/guard.py`) that Claude Code runs before and after every browser tool call. It refuses
  navigations to ad, malware and phishing hosts, `javascript:`/`data:`/app-launching URLs, URLs a browser would
  read differently from the guard, and any URL, keystroke, form value or script that carries an API key, private key
  or card number. Downloads and bare public IP addresses go to you as a permission prompt.
- **A page script** (`page/shield.js`) Claude runs once per page load. On YouTube it skips each ad the moment it
  starts; on any site it cancels clicks on ad, app-launch and download links, refuses cross-origin pop-ups, answers
  `alert`/`confirm`/`prompt` and "Leave site?" without a dialog, denies notification, location and camera requests,
  and drops requests to ad networks. Claude calls it with the text `browserShield()`; the hook swaps in the file.

## Install

```bash
cd ~/projects/automation/browser-shield && /usr/bin/python3 -I -m unittest discover -s tests -t . && node --test tests/shield.test.js
```

The hooks live in `~/.claude/settings.json` (user scope, so every project's sessions get them):

```json
"PreToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch|javascript_tool|computer|form_input)$|^WebFetch$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/projects/automation/browser-shield/hooks/guard.py", "timeout": 10}]
}],
"PostToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch)$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/projects/automation/browser-shield/hooks/guard.py", "timeout": 10}]
}]
```

Claude Code picks up an edit to that file in running sessions. To switch the shield off, delete those two entries
(a backup of the file from before they were added is `~/.claude/settings.json.bak-2026-10-26-browser-shield`).

The malware and phishing lists are downloaded separately into `cache/` (git-ignored) and compiled into one SQLite
index; until then only the curated lists in `lists/` are checked, and the hook says so once per session:

```bash
/usr/bin/python3 -I ~/projects/automation/browser-shield/scripts/update_lists.py
```

## Use

In any session, after opening a page:

```text
javascript_tool  text: browserShield()
```

It returns the shield's status (`adsSkipped`, `clicksBlocked`, `popupsBlocked`, `enforcement`, a short log). After a
YouTube page loads without it, the hook adds a note saying so. `enforcement: true` means YouTube is showing its
ad-blocker notice: the shield stands down there and never hides the notice. Local dev hosts (`localhost`, `127.x`,
`*.localhost`, `*.test`) are left alone so the app under test behaves as built.

Everything the guard refuses or asks about is logged to `cache/guard.log` (one JSON line each, secrets masked).

## How it works

```text
Claude ──tool call──▶ PreToolUse: hooks/guard.py ──deny / ask / rewrite / allow──▶ Browser pane or Chrome
                      │  browser_shield/policy.py   which tool, which fields
                      │  browser_shield/urls.py     the host a browser would really reach (WHATWG URL rules)
                      │  browser_shield/lists.py    curated lists + cache/blocklist.sqlite, suffix lookup
                      │  browser_shield/leaks.py    API keys, private keys, Luhn-valid card numbers
                      └─ PostToolUse: "the page shield is not active on this YouTube page yet"
page/shield.js ── runs inside the page: YouTube ad skipper, click / pop-up / dialog / permission / request guards
```

- **Where a URL really goes.** `urls.parse` follows the WHATWG URL Standard rather than `urllib.parse`: it strips
  tabs and newlines anywhere, reads `\` as `/`, takes the host after the last `@`, decodes `%65`, maps full-width
  dots and letters, and reads `0x7f.1` or `3232235777` as IPv4. A test parses every vector in
  `tests/url_vectors.json` with Node's `URL` class too and requires the same host. Anything it cannot read is refused.
- **YouTube ads.** YouTube still schedules ads in the page's player response (`adPlacements`, `playerAds`,
  `adSlots`) and plays each as its own clip in the same `<video>` while `#movie_player` carries `ad-showing`. The
  script watches that class and the video's events, mutes the ad, presses Skip when offered and seeks the ad clip to
  its end; the player then returns to the video where it left off. It leaves YouTube's own network requests alone.
- **Lists.** `lists/ad-hosts.txt` (curated; `page/shield.js` carries the same list, pinned by a test) and the
  downloaded threat lists, compiled by `scripts/update_lists.py`. `lists/allow.txt` overrides only the downloaded
  lists, so a false positive there can never cut Claude off from YouTube or GitHub, while our own ad list still
  blocks `adservice.google.com` under an allowed `google.com`.
- **Failure.** If the guard itself breaks it lets the call through and says so in a warning, rather than lock the
  browser; an unreadable URL is refused.

Not covered: a URL a shell tool hands to the pane through the desktop app's `$BROWSER` shim skips the MCP tools and
so the hook; clicks the page script cannot see (inside cross-origin ad iframes) are left to the pane's own pop-up
blocker and site-approval prompts.

## Layout

| Path | What |
|---|---|
| `hooks/guard.py` | The hook entry point Claude Code runs |
| `browser_shield/` | URL parsing, lists, leak patterns, the per-tool policy |
| `page/shield.js` | The page script |
| `lists/` | Curated lists; `*.local.txt` for personal additions (git-ignored) |
| `scripts/update_lists.py` | Downloads the threat lists and compiles `cache/blocklist.sqlite` |
| `tests/` | Python `unittest` suite and `shield.test.js` (`node --test`) |
| `docs/` | `DECISIONS.md`, `learnings.md` |

## Experience Gained

- Wrote a Claude Code PreToolUse/PostToolUse hook in stdlib Python that inspects 6 browser tool types plus WebFetch
  and returns deny, ask or rewrite decisions; verified live in the desktop app, where it refused an ad host and
  rewrote a 1-line call into the full page script.
- Built a WHATWG-conformant URL host parser in Python and differential-tested it against Node's URL implementation
  across 33 bypass vectors (userinfo, backslashes, percent-encoding, full-width dots, hex/octal/integer IPv4).
- Shipped an in-page YouTube ad skipper measured live: a 135-second pre-roll and a 15 s + 30 s mid-roll pod cleared
  in under 2 seconds each, with the viewer's mute state restored.
- Designed a secret-exfiltration guard: 15 vendor token patterns plus Luhn and card-network checks, with published
  payment test cards allowed so local checkout testing keeps working.
- Compiled hosts, plain and AdBlock-format blocklists into a SQLite index for constant-time suffix lookups from a
  short-lived hook process.
- Covered it with 60 Python and 10 Node tests, each guard mutation-tested (sabotaged, seen red, restored).
