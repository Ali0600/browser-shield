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
