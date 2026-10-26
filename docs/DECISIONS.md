# Decisions

## Backlog: alternatives worth trying later

- **A permission rule that lets the pane's `javascript_tool` run unasked** (2026-10-26), only if auto mode's
  classifier keeps giving no verdict on the autorun mod's call. It would let every pane script run without a check,
  far wider than the mod needs, so it is the user's rule to write, not the mod's. Revisit hook: `permissions.allow`
  in `~/.claude/settings.json`; the mod needs no change.
- **uBlock Origin Lite in the user's own Chrome for Claude in Chrome sessions** (2026-10-26). Claude in Chrome opens
  ordinary tabs in the user's profile, so an MV3 blocker there filters them at the network layer, which no page script
  can. Revisit hook: nothing in this repo changes; the guard keeps judging Claude's own calls.
- **Frame grabs from a downloaded copy (yt-dlp + ffmpeg) instead of the watch page** (2026-10-26). No ads, exact
  frames, no pane. Each download needs the user's go-ahead and YouTube's terms forbid downloading. Revisit hook:
  `roblox-games/.claude/skills/scout/frame.js` would be replaced by a script that reads `-ss <t> -frames:v 1`.
- **`onFailure: "block"` on the hooks** (2026-10-26) once every Claude Code on this Mac is 2.1.295 or later (the
  desktop runs 2.1.295; the terminal CLI was 2.1.288, which may reject the key). Then a timeout or a crash outside
  the guard's own `try` also refuses the call. Revisit hook: the two hook entries in `~/.claude/settings.json`.
- **A weekly launchd job running `scripts/update_lists.py`** (2026-10-26), once the user has approved the downloads.
  Phishing domains live for days. Revisit hook: the script already keeps the last good copy and exits 1 on any
  failure; the job needs absolute paths and a pinned `HOME`.

## 2026-10-26: where Claude's ad and harm blocking can live

The Browser pane is Electron with no extension support and no ad-blocking setting (feature request
anthropics/claude-code#104030); the only per-host block list is for third-party-provider deployments.

- **A PreToolUse/PostToolUse hook on Claude's own tool calls plus a page script Claude runs after each load**: chosen.
  The hook is the security boundary (it sees every URL, keystroke, form value and script before it runs); the page
  script handles what only the page can see (ads playing, clicks, pop-ups, dialogs).
- **A hosts file, DNS filter or system proxy**: rejected: it changes the Mac's network settings for every app, which
  Claude must not do, and it cannot remove YouTube ads (they come from YouTube's own hosts).
- **An `mcp_tool` hook calling `javascript_tool` after `navigate`**: rejected: the pane is a host (SDK) server that
  the desktop lists in `deniedMcpServers`; an `mcp_tool` hook needs a "configured" server.
- **A mod**: built later, see "how the page script starts by itself". **uBlock Origin Lite in Chrome**, **yt-dlp
  frame grabs**: deferred, see the backlog.

## 2026-10-26: how the page script deals with YouTube's in-stream ads

Measured in the pane: YouTube inserted ads client-side (`getStatsForNerds().debug_info` begins `USDAI`), each ad its
own clip in the shared `<video>` while `#movie_player` has `ad-showing`.

- **Mute the ad and seek the clip to its end; under server-stitched ads (`SSAP, AD`) use the player's
  `seekTo(getProgressState().duration)`; never seek a clip as long as the video**: chosen. Measured: a 135 s
  pre-roll, a 15 s + 30 s mid-roll pod and two more pre-roll pods each cleared in under 2 s.
- **Prune `adPlacements`/`adSlots` from player responses or wrap `fetch`/`JSON.parse`** (uBlock Origin's approach):
  rejected: a script injected after load is too late for the first video, YouTube captures native functions in a
  sandboxed iframe and reports tampering (`onAbnormalityDetected`), and a 2026-10-26 uAssets report ties prototype
  changes to shadowbanned comments. On YouTube the script replaces no built-in function at all.
- **Wait the ads out** (the scout agent's old rule, "never block or speed them"): rejected at the user's request.
- **Open `youtube-nocookie.com/embed/<id>` instead**: rejected: embeds still carry ads, and a top-level navigation
  sends no Referer, which YouTube answers with Error 153.
- **On the ad-blocker notice or the "Experiencing interruptions?" link the script stands down for good and never
  hides them**; the guard refuses any script that would. Evading YouTube's detection is out of scope.

## 2026-10-26: what the guard refuses, what it asks about

- **Deny** what is harmful whatever the context: ad, malware and phishing hosts; schemes other than http(s) (an app
  launch, `javascript:`, `data:`); URLs a browser would read differently from the guard; secrets and card numbers in
  URLs, keystrokes, form values and scripts; remote file shares; scripts that hide YouTube's notice.
- **Ask** where the user may well want it: downloads, local files, the local network and bare public IPs, payment
  and account pages, credentials before the host, file uploads, and shell commands shaped like a "paste this into
  Terminal" lure. Claude's own rules already need the user's go-ahead for downloads and payments; "ask" turns that
  into a prompt the user cannot miss.
- **Rejected: deny everything risky.** Downloads, local previews and `curl | sh` installers are legitimate often
  enough that a refusal would push work around the guard.

## 2026-10-26: what happens when the guard itself fails

- **An error while judging a known call refuses that call and names the error**: chosen. Failing open would make
  any input that crashes the parser (`https://x]@evil.com/` crashes `urllib.parse`) a way past the guard.
- **Unreadable hook input lets the call through with a `systemMessage`**: no web page controls Claude Code's own
  JSON, so there is nothing to defend against there, and refusing would lock every browser call on a host bug.
- **`onFailure: "block"`**: deferred (backlog): not every Claude Code on this Mac supports it.

## 2026-10-26: which lists, and who wins

- **Curated `lists/ad-hosts.txt` beats `lists/allow.txt`, which beats the downloaded lists.** Our own ad list keeps
  `adservice.google.com` blocked under an allowed `google.com`; a false positive in a downloaded list can never cut
  Claude off from YouTube, GitHub or Roblox.
- **Downloaded: HaGeZi TIF mini, malware-filter's URLhaus hosts, Phishing Army, Peter Lowe**: chosen. Small
  (about 7 MB together), refreshed daily or faster, none lists a host YouTube needs.
- **URLhaus's own host file**: rejected: it now answers 410 and needs an account key.
- **HaGeZi Pro, OISD, AdGuard DNS for ad-click hosts**: rejected: they allow `googleadservices.com` on purpose
  (Google Shopping links), which is exactly the redirect an ad click goes through.
- **Never committed**: the lists are GPL or unlicensed; they are fetched into the git-ignored `cache/`.

## 2026-10-26: how the page script reaches the page

- **`browserShield()` swapped for the file by the PreToolUse hook (`updatedInput`, no permission decision), plus a
  PostToolUse note after a YouTube page loads without it**: chosen. Verified live: the rewrite applies to single
  calls and to `browser_batch` items without auto-approving anything.
- **The agent pastes the 13 KB file into each call**: rejected: a long transcription invites a silent copy error,
  and the file would drift between copies.

## 2026-10-26: how the page script starts by itself (the autorun mod)

The one step the guard could only remind about was running `browserShield()` after each YouTube load. A Claude Code
mod (`hooks/autorun.ts`) now does it. Four forks, each measured in `claude plugin test` or live in the desktop app
(Claude Code 2.1.308):

- **Where it hooks.** Wrapping `tool.call` on `navigate`: rejected. The engine refuses a hook whose answer leaves
  out a context entry a hook beneath attached, so it could not take the guard's reminder back. Teaching the guard to
  stay quiet when the mod is loaded: rejected, the guard's process cannot tell which sessions loaded it.
  `classic.PostToolUse`: chosen. Hooks modules sit above the settings hooks there, so the mod reads the guard's
  answer and edits it.
- **How it reaches `javascript_tool`.** `$.mcp.call` straight to the browser's server: rejected. In auto mode it was
  refused on two live navigations ("The server-side auto mode classifier gave no verdict"). A `tool.check` hook
  approving only the mod's own call with its own script: rejected, blocked by the auto-mode classifier as an
  auto-mode bypass, rightly: widening what runs unasked is the user's call. `$.tool.call`, a tool call of the mod's
  own: chosen. The guard's PreToolUse, the user's rules and the classifier judge it like the model's call. Live
  result pending (it loads in a session only after a turn ends). A user rule allowing the pane's script tool: in the
  backlog.
- **Where the host is decided.** A regex test inside the page: rejected after the review (4 lenses, 2 refuters each).
  A page can replace `RegExp.prototype.test`, plant a fake `window.__browserShield`, and have its words relayed as a
  `browser-shield:` note. Chosen: the mod decides from the browser's own report lines, the page gate uses only
  `location.hostname`, `===` and string indexing, an answer counts only with the status's shape and `youtube: true`,
  and notes carry no page text.
- **Layout.** A `mod/` folder reading `../page/shield.js`: rejected, a linked or copied plugin folder resolves `..`
  elsewhere. A copy of the page script inside the mod: rejected, two copies drift. The repo root as the plugin, reading
  `${$.plugin.root}/page/shield.js`: chosen.

## 2026-10-26: Unicode hosts after the code-point sweep

A sweep over all 1.1 million code points found 251 that a browser maps to plain ASCII (variation selectors,
U+180F, U+2064, U+1E9E) while Python's idna codec (Unicode 3.2 tables) kept them: `x<U+180F>y.example` *is*
`xy.example` to the browser, so a listed host could be reached unchecked.

- **A UTS 46 table generated from Node's own parser (ICU 78.2, Unicode 17), committed, with a test that regenerates
  it and one that sweeps every code point against Node**: chosen. 89 KB, loaded only for non-ASCII hosts.
- **Unicode's `IdnaMappingTable.txt`**: rejected: a download needing the user's go-ahead, and Node is the oracle
  the tests already trust, so the table cannot disagree with the test that checks it.
- **Refuse or ask on every non-ASCII host**: rejected: it would stop every internationalised domain to protect
  against a mapping we can compute exactly.

## 2026-10-26: what the replay of 122,697 real tool calls changed

- **Files inside the session's project folder and scratchpad open without asking** (from the hook input's `cwd` and
  `scratchpad_dir`, symlinks resolved); anything else on the Mac still asks. 27 of the 40 false positives were
  Claude opening its own generated HTML.
- **WebFetch skips the download and payment-page asks**: it returns text to the model, holds no session and saves
  nothing; blocklists, schemes and secrets still apply.
- **Chrome extension pages ask instead of being refused; ids in git-ignored `lists/extensions.local.txt` open**
  (the user's own extension under test). Rejected: keep refusing them (the verifier found Claude in Chrome does not
  open them anyway, but the user's own extension testing ran through them).
- **Shell lures need the shell to end the command (or take options)**: `echo "curl|bash"` and test strings are text.
  Revisit hook: `policy.SHELL_LURES`; a quoted lure inside a heredoc still asks (rare, and the safe direction).
- **The page script treats a site's own pages named like scripts as pages** (`github.com/x/acme.sh`), and only
  installers and executables as downloads from the same site; another site's archives and scripts stay downloads.
  Rejected: same-origin `blob:` downloads allowed (the verifier noted that is how pages smuggle a payload).
