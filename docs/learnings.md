# Learnings

### A PreToolUse hook can rewrite a tool call without approving it

`hookSpecificOutput.updatedInput` replaces the whole input of the call; returned without a `permissionDecision`, the
call still goes through the normal permission flow.

**Why it came up:** the docs only describe `updatedInput` together with `"allow"` (which would skip the user's prompt)
or `"ask"`. A live run on Claude Code 2.1.295 showed the rewrite of `browserShield()` into the 13 KB page script
applied on its own, for a single `javascript_tool` call and for an item inside `browser_batch`. The PostToolUse
event then carried the rewritten input, which broke the first version of the "shield not active" reminder.

**Takeaway:** a rewriting hook should return the rewrite alone, and every later stage must recognise both forms of
the input.

### A guard that fails open on its own crash is a bypass

If a security check lets the action through whenever it throws, every input that makes it throw walks past it.

**Why it came up:** `urllib.parse.urlsplit("https://x]@evil.com/")` raises `ValueError`, while Chromium happily
navigates to `evil.com`; Claude Code treats a hook that exits non-zero as a "non-blocking error". The guard now
refuses a call it failed to judge, and turns every parser exception into an "unreadable URL" refusal.

**Takeaway:** fail closed per decision, and keep "fail open" for inputs no attacker controls.

### Test a parser against the implementation whose behaviour you are guarding

A differential test runs the same inputs through your parser and a reference one and fails on any disagreement.

**Why it came up:** the guard must find the host a browser will really reach. Node's `URL` follows the same WHATWG
standard as Chromium, so `tests/test_urls.py` parses 70 hostile URLs with both: it caught a host ending in two
full-width dots that came out as `evil.com.` and slipped past a suffix lookup, and a lone surrogate that crashed
the parser.

**Takeaway:** when correctness means "agrees with system X", make X the oracle in the test, and list each deliberate
disagreement by name.

### The consumer's parser beats the standard's

What the navigate tool does with an input matters more than what the URL standard says about it.

**Why it came up:** to a browser, `localhost:3000` has the scheme `localhost`; the navigate tool opens
`https://localhost:3000`. It also opens `/etc/passwd` and `~/.ssh/id_rsa` as local files, which a standard-only guard
read as a host called `~`. Probing the tool itself found both.

**Takeaway:** model the program that acts on the input, measured, not the specification it nominally follows.

### Client-side and server-stitched video ads need opposite skips

YouTube either plays an ad as its own clip in the `<video>` (client-side) or stitches it into the content stream
(server-side, "SSAP").

**Why it came up:** seeking the `<video>` to its end skips a client-side ad in under a second, but under SSAP the
same seek jumps to the end of the whole video. The player's `getStatsForNerds().debug_info` says which: `USDAI, ...`
(measured) or `SSAP, AD, ...` (uBlock Origin's switch).

**Takeaway:** before acting on media state, read the player's own description of what is playing.

### A hook's latency is mostly the interpreter starting

A command hook pays for a fresh interpreter on every matched tool call.

**Why it came up:** the guard took 166 ms a call; bare `python3 -I` was 51 ms and importing `dataclasses` (which
imports `inspect`) cost about 25 ms. Replacing dataclasses with namedtuples, importing `sqlite3` lazily and answering
ordinary shell commands before loading the policy brought it to 84 ms for Bash and 125 ms for browser calls.

**Takeaway:** profile a hook with `python -X importtime` and give the common case an exit before the imports.

### Replay real history to measure a guard's false positives

A guard's cost is the legitimate work it stops, and the best sample of legitimate work is what was actually done.

**Why it came up:** replaying 122,697 tool calls from 2,816 Claude Code transcripts through the policy found 40
false positives on real work (Claude opening its own HTML previews, a WebFetch of a `.sh` file, a test string
containing `curl|bash`) that no hand-written test had imagined; after the fixes the same replay stops one.

**Takeaway:** before shipping a filter, run it over the real traffic it will see and read every hit by rule.

### A live guard also judges the tools that test it

Once a hook is installed for every session, the reviewers and test scripts working on it are inside its scope.

**Why it came up:** reviewer agents probing shell lures put `curl … | sh` on their own Bash command lines, and the
live guard asked the user about each one, which would have stalled them; the review was restarted with the probes
in files. Later, Bash heredocs that edited the lure tests tripped it the same way.

**Takeaway:** keep attack strings in files (or build them from parts) whenever the thing under test is watching
the command line.

### A hook that wraps a tool call may add notes, never drop one a lower hook added

In Claude Code's mod API a `tool.call` hook can rewrite the call and append to the result's `context`, but the
engine refuses (and skips) a hook whose answer leaves out a context entry a hook beneath attached.

**Why it came up:** the autorun mod first tried to start the page shield after `navigate` and strip the guard's
"shield is not active" reminder from the same result; `claude plugin test` reported "returned a context without an
entry a hook below attached". The classic `PostToolUse` chain puts hooks modules above the settings hooks and lets
them answer a changed copy, so the mod moved there.

**Takeaway:** to correct what an earlier layer said, hook the layer where you sit above it, not the one that only
sees its output.

### A plugin's own tool call is still judged by the user's permission mode

A mod's `$.mcp.call` is documented as needing no prompt, but in auto mode the call went to the auto-mode classifier
anyway, which answered "gave no verdict" and refused it; the edit that would have added a `tool.check` hook letting
the mod approve its own call was refused by auto mode as an auto-mode bypass. Sent as an ordinary tool call
(`$.tool.call`), the classifier refused it again, saying the request that produced it "did not ask for one".

**Why it came up:** two live YouTube navigations in the Browser pane, 2026-10-26, Claude Code 2.1.308 desktop.

**Takeaway:** an extension acts with the user's permissions, not its own, and auto mode approves only what a
request asked for: an action no one asked for needs a rule the user writes.

### One TypeScript file can feed a plugin engine and `node --test`

Node 22's `--experimental-strip-types` imports a `.ts` file by deleting its type annotations, as long as it uses
only erasable syntax (no `enum`, `namespace` or parameter properties) and its type imports are `import type`.

**Why it came up:** `hooks/autorun.ts` is loaded by Claude Code as a mod and also imported by
`tests/autorun.test.mjs`, so its pure parts (which tab, which host, which note) are tested in Node, and against the
real guard, without the engine; only the wiring needs `claude plugin test`.

**Takeaway:** export a hook module's decisions as pure functions and test them where the rest of the project's tests
run.

### Run a tool everyone depends on from a pinned release, swapped in by one rename

A deployment step between "saved" and "live": copy a tested commit into its own folder, then point one symlink at it
with `os.replace`, which is atomic, so a reader sees the old release or the new one and never half of either.

**Why it came up:** Claude Code ran the guard straight from this checkout, so every save was live in every session,
and a half-saved file crashed the hook, which Claude Code reads as a non-blocking error: calls went through
unchecked. `scripts/install.py` now gates each commit inside its own copy, smoke-tests the copy's hook as Claude Code
runs it, and only then swaps `current`; state that must outlive a release (downloads, the log, personal lists) lives
in one `data/` folder each release reaches through symlinks.

**Takeaway:** when other processes run your code while you edit it, give them a pinned copy and a one-rename swap.

### A here-document's body is data unless a shell reads it

`cat > f <<'EOF'` writes its body and `python3 - <<'EOF'` feeds it to a program: neither runs the lines as shell
commands. Only a body fed to `sh`, `bash` or `zsh` runs line by line, and only an unquoted one expands `$(…)`.

**Why it came up:** the first cut of the shell-opener rule split every line of a command, here-document bodies
included, and the replay showed it would have refused 8 real calls, every one a file or a script being written
whose text mentioned `open` near an ad URL. Dropping quoted bodies fed to non-shells took it to 0 of 130,038.

**Takeaway:** when you read a shell line for what it will run, set aside quoted here-document bodies unless a shell
is the program reading them.

### A mutant written as exact source text goes stale when you edit its line

A mutation harness that applies `old -> new` replacements depends on `old` existing verbatim; edit the guarded line
and the mutant no longer applies, so the test it was meant to prove is back to unproven.

**Why it came up:** adding opener words to the hook's fast-path regex and rewording a comment broke two existing
mutants (`guard-fast-path-everything`, `rf-shell-reads-lists`). A check that every `old` still occurs exactly once,
run before writing the mutant list, caught both before the next mutation run would have.

**Takeaway:** after editing a line a mutant targets, re-check every mutant's pattern in the same change.

### launchd runs a calendar job missed while the Mac slept; cron skips it

A `StartCalendarInterval` job whose time passes while the Mac is asleep starts at the next wake, and several missed
times coalesce into one run (`man 5 launchd.plist`); cron simply skips the missed time.

**Why it came up:** the weekly threat-list refresh is set for Monday 11:00 on a laptop that is often closed then. As
a LaunchAgent it still runs once that week, at the first wake; started by `launchctl kickstart`, it ran in the same
bare environment the schedule gives it (pinned `HOME`, `LANG`, `PATH`) and exited 0.

**Takeaway:** on a Mac, schedule periodic maintenance with a LaunchAgent, and test it with `launchctl kickstart`.

### A shell redirection does not end the command

`open >/dev/null https://x` runs `open https://x`: sh takes the redirection and its target out of the word list and
passes every other word as an argument, wherever the redirection sits (`>/dev/null open https://x` too).

**Why it came up:** the first shell-opener parser split commands at every `<` and `>` as if they were `;`, so the URL
after a redirection became a separate "command" and was never judged, and the `2` of `2>/dev/null` was read as a
target. A review's skeptic found both from one root cause.

**Takeaway:** when you split a shell line into commands, treat a redirection as part of the command: drop its
operator, its target and a descriptor number before it, and keep reading.

### Python's module cache serves the first copy loaded under a name

`importlib.import_module("pkg.policy")` returns whatever `sys.modules` already holds for that name, even when you
meant to load a different copy of `pkg` from another folder.

**Why it came up:** `scripts/replay.py --compare` loads a second copy of the guard under its own module name. A test
that loaded a deliberately broken copy under that name poisoned the next test's run: comparing the repo with itself
reported 3 moves, judged by the broken copy's cached policy.

**Takeaway:** before loading a package from a path under a fixed alias, delete that alias and its submodules from
`sys.modules`.

### A Claude Code plugin is installed as a copy, so ship real files

Installing a plugin copies its folder into Claude Code's cache; a link out of the folder is followed for a git
marketplace, skipped for a local or `--plugin-dir` install, and dropped by the plugin directory. Anything the plugin
writes belongs in `${CLAUDE_PLUGIN_DATA}`, since each update replaces the copy.

**Why it came up:** the first layout for the public plugin linked the guard's code into `plugins/browser-shield/`;
`claude plugin validate` said the directory would drop every link, and the docs said a local install skips them.
The fix was a generated copy with a drift test, and a test that installs it the way Claude Code does (a copy, a
separate data folder, the placeholders filled in) before trusting it.

**Takeaway:** give a plugin real files built from the source by a script, fail the gate on drift, and test the
installed shape, not the checkout.

### A vendored tool's shipped defaults can contradict the reason you picked it

A library is chosen for what it can do; the build you download decides what it does, through settings fixed inside
it.

**Why it came up:** DuckDuckGo's autoconsent was picked to reject cookie banners. Its single-file build, the obvious
one to inject into a page, starts itself with `heuristicMode: 'tier2'`, documented as "will click the accept
button" when that is the only button it recognises, and it also exposes a page-callable opt-in. Reading the build
before vendoring it found this; the library build from the same package, started with our own config, does not.

**Takeaway:** before vendoring a build, read the config it starts itself with and every entry point it leaves
behind, and pin the values you rely on with tests, not with the reputation of the project.
