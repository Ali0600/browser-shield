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
the mod approve its own call was refused by auto mode as an auto-mode bypass.

**Why it came up:** two live YouTube navigations in the Browser pane, 2026-10-26, Claude Code 2.1.308 desktop.

**Takeaway:** an extension acts with the user's permissions, not its own: route its actions through the normal
tool path, and leave any widening of what runs unasked to a rule the user writes.

### One TypeScript file can feed a plugin engine and `node --test`

Node 22's `--experimental-strip-types` imports a `.ts` file by deleting its type annotations, as long as it uses
only erasable syntax (no `enum`, `namespace` or parameter properties) and its type imports are `import type`.

**Why it came up:** `hooks/autorun.ts` is loaded by Claude Code as a mod and also imported by
`tests/autorun.test.mjs`, so its pure parts (which tab, which host, which note) are tested in Node, and against the
real guard, without the engine; only the wiring needs `claude plugin test`.

**Takeaway:** export a hook module's decisions as pure functions and test them where the rest of the project's tests
run.
