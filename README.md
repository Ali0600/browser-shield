# claude-browser-shield

An ad blocker and safety guard for the browsers Claude drives: the Claude desktop app's built-in Browser pane (its
in-app browser), Claude in Chrome, and WebFetch. It skips YouTube ads, keeps Claude off ad, malware and phishing
sites, and asks you first before a download, a payment page or a "paste this into Terminal" lure (`curl … | sh`).
Unofficial: not made by or affiliated with Anthropic.

**This is for you if:**

- ads, pop-ups or YouTube pre-rolls get in the way when Claude browses for you;
- you looked for an ad blocker, or a way to add uBlock Origin, for Claude Code's Browser pane and found none (the
  pane cannot load extensions and has no ad-blocking setting; the request is open as
  [anthropics/claude-code#104030](https://github.com/anthropics/claude-code/issues/104030));
- you want Claude to stop, or to ask you first, before it opens a phishing page, types an API key into a site, or
  pipes a script from the web into a shell.

It acts only when Claude drives the browser. While you click around the Browser pane yourself, nothing runs and ads
play as usual: Claude Code runs hooks only around Claude's own tool calls, a plugin gets no signal when you browse,
and the pane cannot load extensions. To have ads skipped on a YouTube tab you opened, ask Claude to start the shield
on it ([Use](#use)).

It runs on macOS with Claude Code 2.1.295 or later, in the desktop app or the CLI. [Install](#install) it yourself,
or [have Claude install it](#have-claude-install-it). It works from the two places Claude can act:

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
- **An autorun mod** (`hooks/autorun.ts`, optional, off by default): a Claude Code plugin hook that starts the page
  script by itself after the pane or Chrome lands on YouTube, as a tool call of its own that the guard, your permission rules and auto
  mode judge like any other.

## Install

### As a Claude Code plugin

You need macOS and Apple's `/usr/bin/python3` (3.9 or later; `xcode-select --install` adds it with the Command Line
Tools). In Claude Code, type:

```text
/plugin marketplace add Ali0600/claude-browser-shield
/plugin install browser-shield@claude-browser-shield
/reload-plugins
```

From a terminal, the first two are `claude plugin marketplace add Ali0600/claude-browser-shield` and
`claude plugin install browser-shield@claude-browser-shield`. The plugin is named `browser-shield`: Claude Code keeps
plugin names that start with `claude-` for Anthropic's own. To check it works, ask Claude to open
`https://adnxs.com/` (an ad server) in its browser: the guard refuses the call before anything loads.

The plugin runs the same guard with the same hooks as the settings below, plus one when a session starts that
downloads the threat lists in the background when they are missing or a week old (about 7 MB, from the sources in
`lists/sources.json`). The lists, the guard's log and your personal lists (in its `local/` folder) live in
`~/.claude/plugins/data/browser-shield-claude-browser-shield/`, which survives updates. A new version reaches you
with `claude plugin marketplace update claude-browser-shield` and `claude plugin update
browser-shield@claude-browser-shield` (then restart Claude Code). `/plugin disable browser-shield@claude-browser-shield`
switches it off; it still works if a broken install is refusing every guarded call, since it is not a tool call. Use
the plugin or the install below, not both, or every call is judged twice.

### From a checkout

This is how the repo's own Mac runs it: each commit is gated again before sessions get it, and a LaunchAgent refreshes
the lists. You need macOS, Apple's `/usr/bin/python3` (3.9 or later, from the Xcode Command Line Tools), Node 22 and
the `claude` CLI on your `PATH` or at `~/.local/bin/claude`: the installer runs the project's tests before it
installs anything. Then:

```bash
git clone https://github.com/Ali0600/claude-browser-shield.git
cd claude-browser-shield
/usr/bin/python3 -I scripts/install.py
/usr/bin/python3 -I scripts/install.py --schedule
```

Claude Code runs an installed copy, never the checkout, so an edit in progress reaches no session. `install.py`
unpacks the current commit into `~/.local/share/browser-shield/releases/<commit>/` (installed paths keep the short
name) and runs the whole gate there. It checks the copy is the commit byte for byte and smoke-tests the copy's hook
the way Claude Code runs it (an ad host must be denied, a plain `ls` let through). Only then does it point `current`
at the copy, with one rename, so a hook never starts on half a copy. The three newest releases stay for a rollback.
`--schedule` loads a LaunchAgent (`local.browser-shield.update-lists`) that refreshes the threat lists every Monday at 11:00, or
at the next wake if the Mac was asleep (log: `~/Library/Logs/browser-shield-update-lists.log`). `--status` shows
what is installed, the lists' age, and what the hooks and the job run, and exits 0 only once the hook entries below
run the installed guard; `--unschedule` removes the job.

The hooks live in `~/.claude/settings.json` (user scope, so every project's sessions get them):

```json
"PreToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch|javascript_tool|computer|form_input|file_upload|upload_image|gif_creator|shortcuts_execute)$|^WebFetch$|^Bash$|^mcp__terminal__run_in_terminal$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/.local/share/browser-shield/current/hooks/guard.py", "timeout": 10, "onFailure": "block"}]
}],
"PostToolUse": [{
  "matcher": "^mcp__((remote-devices__)?Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch|get_page_text|read_page|find|javascript_tool)$|^WebFetch$",
  "hooks": [{"type": "command", "command": "/usr/bin/python3 -I $HOME/.local/share/browser-shield/current/hooks/guard.py", "timeout": 10}]
}]
```

Claude Code picks up an edit to that file in running sessions. `"onFailure": "block"` (Claude Code 2.1.295 or
later) makes a guard that crashes or runs past its 10 s refuse the call instead of letting it through unchecked;
the guard's own errors already refuse the call they hit, so this covers the interpreter failing to start, a crash
before the guard's own handling, and a timeout. It is on PreToolUse only: a PostToolUse failure has nothing left to
refuse. The price: a broken install refuses every Bash and browser call until it is fixed. `install.py` smoke-tests
each copy before sessions get it, and the hook entries can still be edited by hand, since Claude's Edit tool is not
guarded. To switch the shield off, delete those two entries.
Cost, measured: about 84 ms on each shell command (most return before the policy loads) and 125 ms on each guarded
browser call.

The malware and phishing lists (`lists/sources.json`: HaGeZi TIF mini, malware-filter's URLhaus hosts, Phishing
Army, Peter Lowe's ad servers; about 7 MB) are downloaded into `~/.local/share/browser-shield/data/` and compiled
into one SQLite index. Until then only the curated lists in `lists/` are checked, and the hook says so once per
session. The weekly job runs the update; to run it by hand:

```bash
/usr/bin/python3 -I ~/.local/share/browser-shield/current/scripts/update_lists.py
```

A failed or too-small download keeps the last good copy and exits 1; the hook warns once the lists are 8 days old.
Personal additions go in `~/.local/share/browser-shield/data/local/`: `block.local.txt`, `allow.local.txt`, and
`extensions.local.txt` (ids of your own Chrome extensions Claude may open). In a development checkout, the same
files in `lists/` (git-ignored) and `cache/` are used instead.

### Have Claude install it

Paste this into a Claude Code session on your Mac:

```text
Install the browser-shield plugin from https://github.com/Ali0600/claude-browser-shield, following the "As a
Claude Code plugin" part of its README: run `claude plugin marketplace add Ali0600/claude-browser-shield` and
`claude plugin install browser-shield@claude-browser-shield`, showing me each command before you run it. If the
claude command is not available, tell me which /plugin commands to type instead. Then tell me to run /reload-plugins.
```

After `/reload-plugins`, a navigation to an ad host (`https://adnxs.com/`) is refused: that is the check it works.

### The autorun mod

The repo root is also a Claude Code plugin, `browser-shield-autorun` (`.claude-plugin/plugin.json`,
`hooks/hooks.json`), which the marketplace does not list. Load it for one session with `claude --plugin-dir /path/to/claude-browser-shield`, or for every session the desktop app starts by
naming the folder in `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`. It needs the guard
hooks above (it replaces their reminder). In auto mode it cannot run: the classifier refuses a call no request asked
for (measured), so the mod keeps the guard's reminder and says why. It runs where your permission rules allow the
pane's `javascript_tool` (`docs/DECISIONS.md`).

## Use

In any session, after opening a page:

```text
javascript_tool  text: browserShield()
```

It returns the shield's status (`adsSkipped`, `clicksBlocked`, `popupsBlocked`, `enforcement`, `playability`,
`stalled`, a short log). After a YouTube page loads without it, the hook adds a note saying so; with the autorun mod
loaded, the mod runs it instead and the note says it started (or why it could not). `enforcement: true`
means YouTube is showing its ad-blocker notice: the shield stands down there, gives the video its sound back, and
never hides the notice (the hook also refuses any script that would). Local dev hosts (`localhost`, `127.x`,
`*.localhost`, `*.test`) are left alone so the app under test behaves as built.

On a tab you opened yourself, ask in the chat: "run browserShield() on my YouTube tab". It keeps skipping ads as you
go from video to video in that tab; a reload or a new tab needs another run. A paused ad stays paused: the shield
moves it to its last tenth of a second, and it ends when the video plays again.

Everything the guard refuses or asks about is logged to `guard.log` (one JSON line each, secrets redacted), in the
plugin's data folder or, for the install from a checkout, `~/.local/share/browser-shield/data/`.

## How it works

```text
Claude ──tool call──▶ PreToolUse: hooks/guard.py ──deny / ask / rewrite / allow──▶ browser, shell, WebFetch
                      │  browser_shield/policy.py   which tool, which fields, what the session's folders are
                      │  browser_shield/urls.py     the host a browser would really reach (WHATWG + UTS 46)
                      │  browser_shield/lists.py    curated lists + cache/blocklist.sqlite, suffix lookup
                      │  browser_shield/leaks.py    API keys, private keys, cards, secret env values; redaction
                      └─ PostToolUse: blocked landing, missing page shield, text addressing AI agents
                         ▲ hooks/autorun.ts (mod, above the guard): YouTube landing → runs the page script
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
- **Autorun.** The mod reads only the browser's own report lines (`navigated to …`, `- Executed on tabId: …`), never
  page titles or page text, follows a batch tab by tab, and decides the host itself. The page script goes in behind a
  hostname check built from `location.hostname`, `===` and string indexing, which a page cannot patch. A page's
  answer counts only if it has the shield status's shape and says YouTube, and no page text reaches the note.
- **Failure.** An error while judging a call refuses that call and names the error (a parser crash must not be a
  way past the guard); unreadable hook input from Claude Code itself, or a PostToolUse error, is let through with a
  warning. A damaged list index falls back to the curated lists and says so.

Shell commands that open a browser are judged like a navigate: `open` (a URL after `--args` too, which a browser
app opens), `xdg-open`, Python's `webbrowser`, AppleScript's `open location` and JXA's `openLocation`, and the
desktop app's `$BROWSER` shim (which opens the Browser pane). They are found after `;`, `&&`, `|`, wrappers (`env`,
`sudo`, `exec`, `time`, …), inside `sh -c`, `eval`, `env -S` and `do shell script`, in text fed to a shell
(`bash <<< …`, `echo … | sh`), in `echo URL | xargs open`, and with their words split by quotes (`o''pen`). The
system openers are judged on http(s) URLs only, so `open roblox://…`, files and app launches stay the user's
business; `$BROWSER` gets every rule a navigate does. A quoted here-document fed to anything but a shell is data.
Chrome's `shortcuts_execute`, which runs a saved shortcut as an agent the hook never sees, asks first.

Not covered: a click (`computer`) that navigates onto YouTube is followed by neither the reminder nor the mod; a
program that opens a browser from its own code (a script file, `node -e`) is not read, nor is an opener or URL held
in a shell variable (`open "$URL"`); a lure whose words are split by quotes (`cu''rl … | sh`) is not asked about;
secrets the agent builds at run time inside a script are invisible to a text check; a look-alike domain (Cyrillic `е`) is a different domain, caught only if a list names
it; whether the Mac's hooks run for cloud sessions (`mcp__remote-devices__…`) is unknown. The page script runs in
the page's own world after the page's code, so a hostile page can undo it: it stops accidents and ads, and the hook
is the boundary.

## Layout

| Path | What |
|---|---|
| `hooks/guard.py` | The hook entry point Claude Code runs |
| `plugins/browser-shield/` | The plugin people install: manifest, hooks, and a copy of the runtime files (`scripts/build_plugin.py`) |
| `.claude-plugin/marketplace.json` | The marketplace that lists it (`/plugin marketplace add Ali0600/claude-browser-shield`) |
| `hooks/autorun.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json` | The autorun mod and its plugin manifest |
| `browser_shield/` | URL parsing (with `uts46.txt`), lists, leak patterns and redaction, the per-tool policy |
| `page/shield.js` | The page script |
| `lists/` | Curated lists, `sources.json` for the downloads; `*.local.txt` for personal additions (git-ignored) |
| `scripts/install.py` | Gates a commit in a copy and installs it for the hooks; loads the weekly list update |
| `scripts/update_lists.py` | Downloads the threat lists and compiles `blocklist.sqlite` (`--if-stale`: only when a week old) |
| `scripts/build_plugin.py` | Copies the runtime files into `plugins/browser-shield/` (`--check` reports drift) |
| `scripts/gen_uts46.js` | Regenerates the UTS 46 table from Node (`--check` compares) |
| `scripts/mutation_check.py` | Applies each bug in `tests/mutants.json` to a copy and requires a named test to fail |
| `tests/` | Python `unittest` suite, `shield.test.js` and `autorun.test.mjs` (`node --test`), `autorun.plugin.test.ts` (`claude plugin test .`) |
| `docs/` | `DECISIONS.md`, `learnings.md` |

## Experience Gained

- Wrote a Claude Code PreToolUse/PostToolUse hook in stdlib Python that judges 13 browser, shell and fetch tools
  with deny, ask or rewrite decisions; verified live in the desktop app, at 84 ms a shell call after cutting import
  time from 166 ms.
- Built a replay tool that runs every tool call in this Mac's Claude Code transcripts (130,589 unique calls from
  3,084 files, de-duplicated across forks) through two versions of the guard and lists each verdict that moves;
  replays cut 40 false positives on real work to one and showed the new shell-opener rule moves none.
- Built a WHATWG and UTS 46 URL host parser in Python, differential-tested against Node's parser on all 1.1 million
  Unicode code points; the sweep closed 251 characters that let a browser reach a host the guard misread.
- Shipped an in-page YouTube ad skipper measured live on 3 videos: pre-rolls up to 135 s and ad pods of up to three
  ads cleared in under 2 seconds each, with the viewer's sound restored.
- Ran three adversarial reviews (5 hunters; 4 lenses with 2 refuters per finding; 4 hunters with a skeptic each)
  and fixed, documented or refuted every finding, among them a forged-status path that let a page write into
  Claude's context and a redirection that hid URLs from the shell rule; covered the code with 181 Python, 47 Node and
  10 plugin-engine tests and a mutation harness of 204 deliberate bugs, each required to turn a named test red.
- Designed a secret-exfiltration guard: 14 vendor token patterns, a linear-time JWT scan, Luhn plus card-network
  checks with 47 published test cards allowed, and redaction of every log line and refusal message.
- Shipped it two ways: as a Claude Code plugin from the repo's own marketplace (a generated copy of 15 runtime
  files behind a drift check, tested the way Claude Code installs it: a cache copy, a separate data folder,
  placeholders filled in), and on its own Mac like a service, each commit gated again inside a pinned release and
  swapped in by one atomic rename, with a launchd job refreshing 298,397 threat-list domains weekly; both fail
  closed when the guard cannot run.

## Licence

MIT (`LICENSE`). `browser_shield/uts46.txt` is generated from Unicode data; its notice is in `NOTICE`.
