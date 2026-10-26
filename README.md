# browser-shield

Claude's own ad blocker and harm guard for the browsers it drives: the Claude desktop app's built-in Browser pane
and Claude in Chrome. The pane cannot load extensions and has no ad-blocking setting (an open request:
[anthropics/claude-code#104030](https://github.com/anthropics/claude-code/issues/104030)), so a YouTube pre-roll
used to stall every visual review until it ran out. browser-shield works from the two places Claude can act:

- **A guard hook** (`hooks/guard.py`) that Claude Code runs before and after Claude's tool calls. It refuses
  navigations to ad, malware and phishing hosts, `javascript:`/`data:`/app-launching URLs, URLs a browser would read
  differently from the guard, and any URL, keystroke, form value or script that carries an API key, private key, card
  number or a secret-named environment value. It asks you first before downloads, files outside the session's
  folders, the local network and bare public IPs, payment and account pages, file uploads, and shell commands shaped
  like a "paste this into Terminal" lure (`curl … | sh`, quarantine removal, `installer -pkg`). After a call it says
  so when a page redirected onto a blocked host or its text addresses AI agents ("ignore previous instructions").
- **A page script** (`page/shield.js`) Claude runs once per page load. On YouTube it skips each ad the moment it
  starts and stands down at YouTube's ad-blocker notice; on other sites it cancels clicks, navigations and form posts
  to ad-click, app-launch and download targets, refuses pop-ups nobody clicked for, answers dialogs, denies
  permission, clipboard and fullscreen requests, drops requests to ad networks, and hides ad slots (Fandom's
  included). Claude calls it with the text `browserShield()`; the hook swaps in the file.

## Install

```bash
cd ~/projects/automation/browser-shield && /usr/bin/python3 -I -m unittest discover -s tests -t . && node --test tests/shield.test.js
```

The hooks live in `~/.claude/settings.json` (user scope, so every project's sessions get them):

```json
"PreToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch|javascript_tool|computer|form_input|file_upload|upload_image|gif_creator)$|^WebFetch$|^Bash$|^mcp__terminal__run_in_terminal$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/projects/automation/browser-shield/hooks/guard.py", "timeout": 10}]
}],
"PostToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch|get_page_text|read_page|find|javascript_tool)$|^WebFetch$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/projects/automation/browser-shield/hooks/guard.py", "timeout": 10}]
}]
```

Claude Code picks up an edit to that file in running sessions. To switch the shield off, delete those two entries
(a backup from before they were added is `~/.claude/settings.json.bak-2026-10-26-browser-shield`). Cost, measured:
about 84 ms on each shell command (most return before the policy loads) and 125 ms on each guarded browser call.

The malware and phishing lists (`lists/sources.json`: HaGeZi TIF mini, malware-filter's URLhaus hosts, Phishing
Army, Peter Lowe's ad servers; about 7 MB) are downloaded into `cache/` (git-ignored) and compiled into one SQLite
index. Until then only the curated lists in `lists/` are checked, and the hook says so once per session:

```bash
/usr/bin/python3 -I ~/projects/automation/browser-shield/scripts/update_lists.py
```

A failed or too-small download keeps the last good copy and exits 1; the hook warns once the lists are 8 days old.
Personal additions go in git-ignored `lists/*.local.txt`: `block.local.txt`, `allow.local.txt`, and
`extensions.local.txt` (ids of your own Chrome extensions Claude may open).

## Use

In any session, after opening a page:

```text
javascript_tool  text: browserShield()
```

It returns the shield's status (`adsSkipped`, `clicksBlocked`, `popupsBlocked`, `enforcement`, `playability`,
`stalled`, a short log). After a YouTube page loads without it, the hook adds a note saying so. `enforcement: true`
means YouTube is showing its ad-blocker notice: the shield stands down there, gives the video its sound back, and
never hides the notice (the hook also refuses any script that would). Local dev hosts (`localhost`, `127.x`,
`*.localhost`, `*.test`) are left alone so the app under test behaves as built.

Everything the guard refuses or asks about is logged to `cache/guard.log` (one JSON line each, secrets redacted).

## How it works

```text
Claude ──tool call──▶ PreToolUse: hooks/guard.py ──deny / ask / rewrite / allow──▶ browser, shell, WebFetch
                      │  browser_shield/policy.py   which tool, which fields, what the session's folders are
                      │  browser_shield/urls.py     the host a browser would really reach (WHATWG + UTS 46)
                      │  browser_shield/lists.py    curated lists + cache/blocklist.sqlite, suffix lookup
                      │  browser_shield/leaks.py    API keys, private keys, cards, secret env values; redaction
                      └─ PostToolUse: blocked landing, missing page shield, text addressing AI agents
page/shield.js ── runs inside the page: YouTube ad skipper, click / navigation / pop-up / dialog / permission guards
```

- **Where a URL really goes.** `urls.parse` follows the WHATWG URL Standard rather than `urllib.parse`: it strips
  tabs and newlines anywhere, reads `\` as `/`, takes the host after the last `@`, decodes `%65`, reads `0x7f.1` or
  `3232235777` as IPv4, and maps Unicode hosts with the browser's own UTS 46 table (`browser_shield/uts46.txt`,
  generated from Node's ICU by `scripts/gen_uts46.js`; Python's idna codec is Unicode 3.2 and keeps characters
  browsers drop). It also reads input the way the navigate tool does: `localhost:3000` is a host, `/x` and `~/x` are
  files. Tests compare it with Node's `URL` on 70 hostile URLs and on a host containing each of the 1.1 million code
  points. Anything it cannot read is refused.
- **YouTube ads.** YouTube still inserts most ads client-side (`getStatsForNerds().debug_info` begins `USDAI`): each
  ad is its own clip in the same `<video>` while `#movie_player` carries `ad-showing`. The script mutes the ad, seeks
  the clip to its end, and gives the sound back after; the player returns to the video where it left off. It never
  seeks a clip as long as the video or when the length is unknown, and under server-stitched ads (`SSAP, AD`) it
  uses the player's own `seekTo`, only where that is measured to be right (a pre-roll). On YouTube it replaces no
  built-in function and leaves YouTube's own requests alone (YouTube watches for both).
- **Lists.** `lists/ad-hosts.txt` (curated; `page/shield.js` carries the same list and the same ad-click paths and
  link wrappers, pinned by tests) beats `lists/allow.txt`, which beats the downloaded lists: our own list keeps
  `adservice.google.com` blocked under an allowed `google.com`, and a downloaded false positive cannot cut Claude
  off from YouTube or GitHub. Link wrappers (`youtube.com/redirect?q=`, `google.com/url?q=`) are judged by where
  they lead.
- **Context.** WebFetch only reads (no session, nothing saved), so downloads and payment pages do not ask there;
  files inside the session's project folder and scratchpad open without asking.
- **Failure.** An error while judging a call refuses that call and names the error (a parser crash must not be a
  way past the guard); unreadable hook input from Claude Code itself, or a PostToolUse error, is let through with a
  warning. A damaged list index falls back to the curated lists and says so.

Not covered: a URL a shell tool hands to the pane through the desktop app's `$BROWSER` shim skips the MCP tools and
so the hook; Chrome's `shortcuts_execute` runs an agent the hook never sees; secrets the agent builds at run time
inside a script are invisible to a text check; a look-alike domain (Cyrillic `е`) is a different domain, caught only
if a list names it; whether the Mac's hooks run for cloud sessions (`mcp__remote-devices__…`) is unknown. The page
script runs in the page's own world after the page's code, so a hostile page can undo it: it stops accidents and
ads, and the hook is the boundary.

## Layout

| Path | What |
|---|---|
| `hooks/guard.py` | The hook entry point Claude Code runs |
| `browser_shield/` | URL parsing (with `uts46.txt`), lists, leak patterns and redaction, the per-tool policy |
| `page/shield.js` | The page script |
| `lists/` | Curated lists, `sources.json` for the downloads; `*.local.txt` for personal additions (git-ignored) |
| `scripts/update_lists.py` | Downloads the threat lists and compiles `cache/blocklist.sqlite` |
| `scripts/gen_uts46.js` | Regenerates the UTS 46 table from Node (`--check` compares) |
| `scripts/mutation_check.py` | Applies each bug in `tests/mutants.json` to a copy and requires a named test to fail |
| `tests/` | Python `unittest` suite and `shield.test.js` (`node --test`) |
| `docs/` | `DECISIONS.md`, `learnings.md` |

## Experience Gained

- Wrote a Claude Code PreToolUse/PostToolUse hook in stdlib Python that judges 12 browser, shell and fetch tools
  with deny, ask or rewrite decisions; verified live in the desktop app, at 84 ms a shell call after cutting import
  time from 166 ms.
- Replayed 122,697 real tool calls from 2,816 transcripts through the policy, found 40 false positives on real
  work, and fixed them down to one, without dropping a correct catch.
- Built a WHATWG and UTS 46 URL host parser in Python, differential-tested against Node's parser on all 1.1 million
  Unicode code points; the sweep closed 251 characters that let a browser reach a host the guard misread.
- Shipped an in-page YouTube ad skipper measured live on 3 videos: pre-rolls up to 135 s and ad pods of up to three
  ads cleared in under 2 seconds each, with the viewer's sound restored.
- Ran an adversarial review (5 hunters, independent verifiers) and fixed, documented or refuted each of its 44
  findings; covered the code with 112 Python and 29 Node tests and a mutation harness of 104 deliberate bugs, each
  required to turn a named test red.
- Designed a secret-exfiltration guard: 14 vendor token patterns, a linear-time JWT scan, Luhn plus card-network
  checks with 47 published test cards allowed, and redaction of every log line and refusal message.
