# Decisions

## Backlog: alternatives worth trying later

- **autoconsent's heuristic for banners no rule knows** (2026-10-30), `heuristicMode: 'reject'`: it would cover the
  long tail, but reading it found it follows "Do not sell my info" links, answers age gates in other languages "no",
  presses "Ablehnen" on pay-or-OK walls whose subscribe wording it misses, and reads Dutch "continue and accept" as a
  refusal. Revisit hook: `heuristicMode` in `CONSENT_CONFIG` (`page/shield.js`) once upstream narrows those patterns;
  the backstop already cancels a script's click on a link off the page while the engine works.
- **autoconsent's generated rules** (2026-10-30): 446 of its 1,327 rules, one site each, made by a crawler and
  clicking positional CSS paths, which a redesign turns into clicks on something else. Revisit hook:
  `enableGeneratedRules` in `CONSENT_CONFIG`.
- **Cookie banners in Claude in Chrome** (2026-10-30): left out because Chrome is the user's own profile and the
  expansion (711 KB as JSON) is close to Chrome's 1 MiB native-messaging cap. Revisit hook: `consent = browser ==
  "pane"` in `policy.rewrite`; measure the cap first.
- **The autorun mod with the cookie engine** (2026-10-30): the mod reads the raw page script, so consent is off on
  its path. Revisit hook: `hooks/autorun.ts:273`, or have the mod send `browserShield()` and let the guard expand it.
- **A `verified` field from autoconsent's self-test** (2026-10-30): 994 rules carry read-only steps that check the
  stored choice (`doSelfTest()`). Revisit hook: after `autoconsentDone` in `onConsentMessage` (`page/shield.js`).

- **A permission rule that lets the pane's `javascript_tool` run unasked** (2026-10-26), the one way the autorun
  mod runs in auto mode, whose classifier refuses a call no request asked for (measured live). It would let every pane
  script run without auto mode's check (the guard still judges each one), far wider than the mod needs, so it is the
  user's rule to write, not the mod's. Revisit hook: `permissions.allow`
  in `~/.claude/settings.json`; the mod needs no change.
- **uBlock Origin Lite in the user's own Chrome for Claude in Chrome sessions** (2026-10-26). Claude in Chrome opens
  ordinary tabs in the user's profile, so an MV3 blocker there filters them at the network layer, which no page script
  can. Revisit hook: nothing in this repo changes; the guard keeps judging Claude's own calls.
- **Frame grabs from a downloaded copy (yt-dlp + ffmpeg) instead of the watch page** (2026-10-26). No ads, exact
  frames, no pane. Each download needs the user's go-ahead and YouTube's terms forbid downloading. Revisit hook:
  the frame-grab helper in the owner's game project (a copy is `tests/fixtures/frame.js`) would be replaced by a
  script that reads `-ss <t> -frames:v 1`.
- **Quote-split words in the lure rules** (2026-10-27): `cu''rl x | sh` runs `curl x | sh`, but `SHELL_LURES`
  match the raw text, so the lure goes unasked (the fast path now loads the policy for it). Normalising quotes for
  the lure rules would also ask about `echo "curl|bash"`, the false positive the 2026-10-26 replay removed. Revisit
  hook: `policy.check_shell`, judged on shlex segments as the opener rule is.

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
- **Wait the ads out** (the old rule of the agent that studies gameplay videos, "never block or speed them"): rejected at the user's request.
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
  own: chosen. The guard's PreToolUse, the user's rules and the classifier judge it like the model's call. Live, in
  auto mode, it was refused as well: the classifier "gave no verdict ... the request that produced this action did
  not ask for one". Auto mode approves only what some request asked for, and a call the mod raises on its own has
  none, so in auto mode the mod falls back to the guard's reminder and says why (measured: the model's own
  `browserShield()` then skipped a 6 s ad). A user rule allowing the pane's script tool: in the backlog.
- **Where the host is decided.** A regex test inside the page: rejected after the review (4 lenses, 2 refuters each).
  A page can replace `RegExp.prototype.test`, plant a fake `window.__browserShield`, and have its words relayed as a
  `browser-shield:` note. Chosen: the mod decides from the browser's own report lines, the page gate uses only
  `location.hostname`, `===` and string indexing, an answer counts only with the status's shape and `youtube: true`,
  and notes carry no page text.
- **Layout.** A `mod/` folder reading `../page/shield.js`: rejected, a linked or copied plugin folder resolves `..`
  elsewhere. A copy of the page script inside the mod: rejected, two copies drift. The repo root as the plugin, reading
  `${$.plugin.root}/page/shield.js`: chosen.
- **Whether it runs at all** (decided by the user, 2026-10-26). Leave it off: chosen. Agents keep running
  `browserShield()` themselves after the guard's reminder (one tool call per YouTube page, measured working). Allowing
  the pane's `javascript_tool` unasked so the mod runs in auto mode: deferred, see the backlog; the mod stays in the
  repo, tested, and loads nowhere unless named with `--plugin-dir` or `CLAUDE_CODE_PLUGIN_DIRS`.

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

## 2026-10-27: where the hooks run from: an installed copy

The hooks ran `hooks/guard.py` straight from the checkout, so a save was live in every session, and a half-saved
file crashed the hook, which Claude Code reads as a non-blocking error: the call went through unchecked.

- **A pinned copy, `~/.local/share/browser-shield/current`, made by `scripts/install.py`**: chosen (the user's pick,
  2026-10-27). It installs only a committed commit, runs the gate inside the copy, checks the copy against the
  commit's bytes, smoke-tests the copy's hook as Claude Code runs it, and moves `current` with one rename. An edit
  here reaches no session until it is installed.
- **Keep running from the checkout and rely on `onFailure: "block"`**: rejected as the only fix. A half-saved file
  would then refuse every guarded call in every session instead of letting them through: safe, but it stops all work
  until the file is fixed. Kept as a second layer for crashes of an installed copy (below).
- **The plugin's own hooks, installed with `/plugin install`**: deferred, see the backlog.

Where the installed copy keeps its state:

- **One `data/` folder that every release reaches through symlinks** (its `cache/` and the three
  `lists/*.local.txt`): chosen. The code finds every file where it always has, so the install needed no change to
  the live guard's code, and a download or log line survives the next install.
- **An environment variable naming the data folder**: rejected. Both hook entries, the weekly job and every hand
  run would have to set it, and one that forgot would quietly read an empty cache.
- **A copy of the data in each release**: rejected. A download landing in one release would vanish at the next.

## 2026-10-27: refreshing the threat lists

- **A weekly LaunchAgent running the installed copy's `scripts/update_lists.py`**: chosen (the user's go,
  2026-10-27: a local Mac job). Monday 11:00 local time; launchd starts a calendar job missed while the Mac slept at
  the next wake (`man 5 launchd.plist`). Absolute paths and a pinned `HOME`, `LANG`, `LC_ALL` and `PATH`; each run's
  output is stamped in `~/Library/Logs/browser-shield-update-lists.log`. A failed source keeps its last good copy and
  the run exits 1; the hook's 8-day warning is what a person sees if failures repeat.
- **Daily**: rejected for now. Weekly keeps the lists well inside the 8-day warning at a seventh of the downloads.
  Revisit hook: `WHEN` in `scripts/install.py` (a `StartCalendarInterval` without `Weekday` runs daily).
- **By hand when the hook warns**: rejected. The warning comes after 8 days, and phishing domains live for days.

## 2026-10-27: a guard that fails to run refuses the call

- **`"onFailure": "block"` on the PreToolUse hook entry**: chosen (the user's pick, 2026-10-27), once every Claude
  Code on the Mac read it (2.1.295 added it; the desktop runs 2.1.308, and the terminal CLI was updated from 2.1.288
  to 2.1.311 the same day). A guard that cannot start, crashes before its own error handling, or runs past its 10 s
  timeout now refuses the call, as exit code 2 does; before, Claude Code let the call through unchecked.
- **On PostToolUse too**: rejected. The tool has run by then, so there is nothing to refuse, and the guard already
  answers its own PostToolUse errors with a `systemMessage`.
- **Leave it off and rely on the installed copy's smoke test**: rejected as the only layer. The smoke test proves a
  copy at install time; a timeout under load or a removed interpreter happens later.
- The cost, accepted: a broken install refuses every guarded call (every Bash call among them) until it is fixed.
  Claude's Edit tool is not guarded, so the hook entries in `~/.claude/settings.json` can always be fixed.

## 2026-10-27: URLs that shell commands hand to a browser

A shell command could open any URL a navigate would have been refused: `open https://…` in the user's signed-in
default browser, or `$BROWSER https://…`, the desktop app's shim into the Browser pane.

- **Judge the URL each opener command is given, with a navigate's verdicts**: chosen. `policy.shell_targets` splits
  the line as sh would (`shlex` with sh's punctuation, `$IFS` read as a space), skips assignments, wrappers with
  their own value options (`env`, `sudo -u x`, `exec -a x`, `command -p`, `time -p`, `nohup`, …) and keywords that
  start a command (`if`, `then`, `{`), follows `sh -c` (also `-lc`), `eval`, `env -S` and AppleScript's
  `do shell script` up to `MAX_DEPTH` 3, and reads text a shell is fed (`bash <<< '…'`, `echo '…' | sh`,
  `cat <<'EOF' | sh`) and `echo URL | xargs open`. Openers read: `open` (operands, `-u`, `--url=`, and every word
  after `--args`, which the launched app receives and a browser opens), `xdg-open`, `python3 -m webbrowser` and
  `python3 -c '…webbrowser.open("…")'`, AppleScript's `open location` and JXA's `openLocation(…)`, and `$BROWSER`.
  Nested deeper than `MAX_DEPTH`, or too odd to split (an unclosed quote), a line is judged by its URLs that follow
  an opener word, quotes removed: unreadable is no way past.
- **The hook's fast path reads the command with quotes and backslashes removed too** (`guard.shell_words_in`): sh
  runs `o''pen` and `o\pen` as `open`, and those skipped the policy entirely.
- **How it was found**: a probe of 69 bypass candidates against the first cut let 29 through (`open -a Safari
  --args <url>`, `time -p` swallowing the next word, quote-split words, `bash <<<`, `cat <<'EOF' | sh` and more).
  After the rewrite 6 pass, all by design: three hold the opener or the URL in a variable, and `open
  doubleclick.net`, `open //doubleclick.net/x` and a scheme broken by a tab are file paths to `open`, not URLs.
  The review's first bypass hunter returned no findings without having tested a candidate, which is why the probe
  was written by hand and a second hunter sent with a quota.
- **System openers are judged on http(s) URLs only**: chosen. `open roblox://placeId=…`, files and app launches
  with arguments (`open -a RobloxStudio.app --args …`) are the user's own use of their Mac, as in Terminal. Rejected:
  every scheme, as for a navigate: `open roblox://` is deliberate work in another project and would be refused.
- **`$BROWSER` gets every rule a navigate does**, schemes included: it drives the Browser pane, which is the agent's.
- **A quoted here-document fed to a program other than a shell is data**: chosen after the replay. The first cut
  refused 8 real calls, every one a `cat > file <<'EOF'` or `python3 - <<'EOF'` whose text mentioned an opener and an
  ad URL. Its body is dropped before the split. A body fed to `sh`, `bash` or `zsh` runs line by line and an unquoted
  one runs its `$(…)`, so both are still judged.
- **A line sh could not split (an unclosed quote)**: an http(s) URL after an opener word on the same simple command
  is judged, so an unreadable line is no way past. Rejected: every URL in the line, which made prose with an
  apostrophe look like an opener.
- **Lists are read only when a command opens a URL**: FP-6 still holds for every other shell call.
- **Not covered, and accepted**: a program that opens a browser from its own code (a script file, `node -e`),
  and an opener or a URL held in a shell variable or command substitution (`open "$URL"`, `x=open; $x …`).
  Revisit hook: `policy._opened_by` and `policy.shell_targets`.
- Replay of 130,038 real calls against the installed copy (`scripts/replay.py --compare`): 0 verdicts change.

## 2026-10-27: Chrome's saved shortcuts

- **`shortcuts_execute` asks**: chosen. It runs a saved shortcut as an agent in Chrome's side panel, whose steps the
  hook never sees. A secret in its `command` is still refused. Rejected: deny, since a shortcut the user saved is
  theirs to run. The PreToolUse matcher gained the tool; the replay found no call to it in the transcripts.

## 2026-10-27: the replay is a script in the repo

- **`scripts/replay.py`**: chosen. The 2026-10-26 replay was a one-off whose extraction step was lost, so no later
  change could be measured. The script reads every transcript under `~/.claude/projects` read-only, counts each tool
  call once (its earliest copy; forks and resumed files copy calls), rebuilds each call's session folders from the
  transcript's own environment record, and judges it as the hook would, fast path included. `--compare <copy>`
  prints every call whose verdict moves between two copies, both directions, against one shared index.
- **Run it in the gate**: rejected. It reads the user's private transcripts and takes about two minutes; it is run
  after a change to what the guard refuses or asks about (CLAUDE.md), and its tests run on made-up transcripts.

## 2026-10-27: what the review of the install, the shell-opener rule and the replay changed

Four hunters (shell-opener bypasses, installer and data safety, false positives and cost, the replay) and a skeptic
per lens that reproduced each finding before it counted. Each finding below now has a test and a mutant.

- **A weekly run whose fetches all fail made the index look new** (medium): `compile_index` rewrote it, so the
  hook's 8-day warning and `--status` would never show failing updates. The index is now dated by its oldest
  successful download (`update_lists.main`).
- **Two bypasses of the opener rule**: a redirection before the URL (`open >/dev/null https://…`: the split ended
  the command at `>`) and `<<'EOF'` text inside quotes or a comment, which hid every later line. Redirections now
  keep the command going (their target and descriptor number dropped); here-documents count only outside quotes and
  comments, and one that never closes is read as commands.
- **False positives**: `open "http://localhost:$PORT"` was refused (the port unreadable), `$BROWSER url 2>/dev/null`
  asked about "2", and prose in an unquoted here-document was judged as commands. A port the shell fills in is
  judged as a port; an unquoted body fed to a program keeps only its `$(…)` and backtick lines.
- **Cost**: the word `open` sent 15% of all Bash calls through the policy (about 55 ms each), 90% of them Python's
  `open(`. The fast path skips `open(`.
- **The install smoke test passed a copy that refused every page** (its own crash counted as the ad host's deny):
  an ordinary page must now be let through. **An interrupted removal could leave a marked half-release** that a
  later install of that commit reused: removal renames to a dot-name first. **`--status` called the hooks fine with
  only the PostToolUse entry left**: it now needs a PreToolUse entry on Bash and browser calls with
  `"onFailure": "block"`. **The extension ask named `lists/extensions.local.txt`**: it names the file the list
  really is, and one stray byte in it no longer breaks every browser call.
- **The replay's `--compare` was untested against a different copy**, and two copies loaded in one process shared
  Python's module cache: both fixed.
- **Rejected: pin the hook to the Command Line Tools' python3** instead of `/usr/bin/python3` (which runs Xcode's):
  no developer folder is pinned on this Mac, so a removed Xcode falls back to the Command Line Tools [inferred], and
  a broken toolchain breaks git as well; the guard failing closed then is the accepted cost.
- **The first bypass hunter returned no findings without testing a candidate, and a second was stopped by Claude
  Code's safety check before it built any**: the bypass coverage is the hand-written probe of 69 candidates plus the
  review's two, not an agent sweep.

## 2026-10-27: the public repository's licence

- **MIT**: chosen (the user's pick, 2026-10-27). Short, permissive, the usual choice for a small stdlib tool. The
  downloaded lists keep their own licences (HaGeZi's is GPL-3.0): they are fetched by each user, never committed.
- **No licence, as the owner's other public repo has**: rejected. Readers could see the code but not reuse it.

## 2026-10-29: how other people install it: a plugin from the repo's own marketplace

The public repo needed an install that does not ask for Node, the `claude` CLI and a hand edit of `settings.json`
(`install.py` runs the whole gate first). The user picked the plugin route (2026-10-29), the backlog's idea.

- **Where the plugin lives.**
  - `plugins/browser-shield/`, real copies of the 15 runtime files made by `scripts/build_plugin.py`, a test failing
    on drift: chosen.
  - The same folder of symbolic links into the repo: rejected, measured. Claude Code dereferences links into the
    rest of the marketplace for a git install, but skips links that leave the plugin for `--plugin-dir` and local
    installs, and the plugin directory drops every link (`claude plugin validate`).
  - The repo root as the guard's plugin, the mod moved to a folder of its own: rejected. The mod reads
    `page/shield.js` from its own root (the 2026-10-26 layout fork), and every install would copy the tests and
    their lure-shaped fixtures.
  - One plugin carrying both the guard's hooks and the mod, the mod behind an option that is off by default:
    rejected. Every install would load an early-access module engine beside the security hook, and the user left
    the mod off.
- **Its name.** `claude-browser-shield`: refused by `claude plugin validate` (Claude Code keeps `claude-` plugin names
  for Anthropic's own). `browser-shield` in a marketplace named `claude-browser-shield`: chosen. The mod's plugin
  became `browser-shield-autorun` so the two never share a name.
- **Its state.** `${CLAUDE_PLUGIN_DATA}`, passed to the guard as `BROWSER_SHIELD_DATA`: chosen; it survives updates,
  and the copy in Claude Code's cache is replaced by each one. An empty value refuses guarded calls and names why,
  instead of writing into that copy.
- **The threat lists.**
  - An async SessionStart hook running `update_lists.py --if-stale` (missing or a week old, one run at a time under
    a lock): chosen. No step for the user, nothing at a session's start waits for it, and the hook's 8-day warning
    still shows a refresh that keeps failing.
  - A command the user runs by hand: rejected. Every installer would see "not installed" until they acted.
  - Every session start: rejected, 7 MB each time.
- **Releases.** An explicit `version` in the plugin's manifest, bumped to release: chosen; `main` has no CI, so a
  push must not reach installers by itself. The commit SHA as the version: rejected for that reason. Revisit hook:
  drop `version` from `plugins/browser-shield/.claude-plugin/plugin.json` once a CI run gates `main`.
- **Not done:** an icon (`.claude-plugin/icon.png`), which the plugin directory wants and takes only once, on the
  first submission.

## 2026-10-30: rejecting cookie banners with DuckDuckGo's autoconsent

The owner asked for what the Consent-O-Matic extension does (the Browser pane loads no extension) and picked a
maintained engine over our own rules (option A).

- **Our own rules for the 20 or so most common banner makers**: rejected by the owner: far fewer sites, and they go
  stale as sites change.
- **Consent-O-Matic's rules** (MIT): rejected: last updated 2026-02, and its rule language would need an engine
  ported to the page script.
- **autoconsent** (MPL-2.0; DuckDuckGo's browsers use it): chosen. 17.1.0 is vendored by
  `scripts/update_autoconsent.py` from the npm tarball, refused unless its sha512 matches the registry's integrity,
  keeping three files byte for byte (the hashes also matched jsdelivr's listing).
- **Its single-file build, `autoconsent.standalone.js`** (the one first named to the owner): rejected after reading
  it. Its fixed config presses Accept when that is the only button its heuristic recognises (`heuristicMode:
  'tier2'`), prehides YouTube's dialog backdrop on every page, leaves a page-callable opt-in
  (`window.autoconsentReceiveMessage`) and logs to the console. **The library build with our own start-up**:
  chosen: our config, no page globals, a console that prints nothing, the engine's methods wrapped on the instance.
- **What the engine may do.** Six readers read all 3,998 lines and two skeptics checked each concern; all 12
  concerns stood. Off: the heuristic, prehiding, the generated rules (backlog), and five rules: `sourcepoint-top`
  (hides walls, pay-or-OK ones included, storing no choice), `povr` (`history.back()`), `ee-consent-notice` (submits
  the Accept form after editing its fields), `Klaro` (its fallback toggles every purpose without reading it, then
  saves) and `Admiral` (clicks any button on the page whose text names a purpose). Kept and noted: TikTok's
  positional decline button, absolute xpaths that can click a same-labelled button outside the banner, re-clicks
  while a button stays visible. The engine's focus restore runs only when nothing else took focus meanwhile.
- **"Is this Accept?" for the backstop: our own list, or autoconsent's classifier.** Our own list: chosen, so the
  check does not depend on the code it checks. Exact whole labels, "…all" phrases only (38, eight languages), so the
  engine's own "Agree" or "OK" after unticking every box passes; the vendored rules' 34 exact click labels are a
  passing test case. The classifier (`heuristics.cjs.js`): rejected, the same supplier as the clicks it would judge.
- **Where it runs.** The top frame, in the Browser pane, after the shield is in place (a failure cannot leave the
  shield half installed), YouTube included: it replaces no built-in function and hides nothing before a decision.
  Claude in Chrome: left out (backlog). The autorun mod: left out (backlog).
- **Licence.** The vendored files stay under the MPL-2.0 (file-level copyleft) inside an MIT repo; `NOTICE` names
  the Source Code Form (MPL §3.2) and `plugin.json` says `MIT AND MPL-2.0`.
- **Size.** The expansion is about 650 KB. The transcript keeps the call as Claude wrote it (`browserShield()`,
  checked on this session's transcript), so Claude's context never carries it, and status reads use
  `window.browserShield()`, which the hook leaves as it is.
