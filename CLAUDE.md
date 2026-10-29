# browser-shield — agent contract

## Gate (run before every commit)

```bash
/usr/bin/python3 -I -m unittest discover -s tests -t . && node --test tests/shield.test.js && node --experimental-strip-types --no-warnings --test tests/autorun.test.mjs && claude plugin test .
```

About 30 s; most of it is the every-code-point sweep in `tests/test_urls.py`. The last two are the autorun mod
(`hooks/autorun.ts`): its pure parts under Node, its wiring under the engine (`claude plugin validate .` too after
changing the manifest or what it hooks). After changing a guard, also run
`/usr/bin/python3 -I scripts/mutation_check.py [mutant-id ...]` and add a mutant to `tests/mutants.json` for the new
guard: each must turn a named test red (a test that has never failed proves nothing). A mutant's `old` is exact source
text: an edit to a line a mutant targets breaks it, so after editing a guarded line, check every mutant still applies
(each `old` occurs exactly once; `mutation_check.py` refuses one that does not).

After a change to what the guard refuses or asks about, replay this Mac's real tool calls against the installed copy
and read every call that moves, both directions, before installing (about 2 minutes, read-only; its output quotes
redacted, shortened tool inputs, so it stays in the terminal):

```bash
/usr/bin/python3 -I scripts/replay.py --compare ~/.local/share/browser-shield/current
```

## Install (after the commit)

```bash
/usr/bin/python3 -I scripts/install.py
```

Claude Code's hooks run the installed copy, `~/.local/share/browser-shield/current`, never this checkout. The install
refuses uncommitted work, unpacks HEAD into a new release, runs the gate there, checks the copy is the commit byte for
byte, smoke-tests its hook as Claude Code runs it, and only then moves `current` (one rename). `--status` shows what
is installed and what the hooks and the weekly job run; `--schedule` / `--unschedule` load or remove the weekly list
update (a LaunchAgent, `local.browser-shield.update-lists`). Install after every commit that changes what sessions run
(`hooks/`, `browser_shield/`, `page/`, `lists/`, `scripts/update_lists.py`), and say so: until then nothing changed.

## Rules

- **The repo is public** (github.com/Ali0600/claude-browser-shield): no personal paths, transcript text, real secrets or
  private project names in a commit or a test fixture. Test secrets are built from parts (`tests/test_leaks.py`);
  `scripts/replay.py`'s output stays in the terminal. Commits use the GitHub noreply address.
- **The hook runs under `/usr/bin/python3` (3.9) with `-I`, stdlib only.** No third-party imports, no syntax newer
  than 3.9 at runtime (`from __future__ import annotations` makes `X | None` hints safe). No `dataclasses`: the hook
  starts on every guarded call and import time is its cost (`python -X importtime`).
- **Sessions run the installed copy** (`~/.claude/settings.json` points its two hook entries at
  `~/.local/share/browser-shield/current/hooks/guard.py`), so an edit here is safe to leave half-done until it is
  committed and installed. Edit test files that contain lure-shaped strings (`curl … | sh`) with the Edit tool: the
  installed guard still reads every Bash command line and would ask the user about a heredoc carrying them.
- **The installed copy's state lives in `~/.local/share/browser-shield/data/`**, which each release reaches through
  its `cache/` and `lists/*.local.txt` symlinks: the downloaded lists and index, `guard.log`, the session markers, and
  the user's personal lists in `data/local/`. This checkout's `cache/` is the development copy; `install.py` copies it
  into `data/` once and never overwrites what `data/` already holds.
- **Fail closed per call, loud otherwise.** An error while judging a call refuses that call and names the
  (redacted) error; never restore fail-open there, since a parser crash would become a way past the guard.
  Unreadable hook input that is not a PreToolUse event, and PostToolUse errors, answer with a `systemMessage`.
- **Every reason and log line goes through `leaks.redact`**: reasons quote URLs, paths and scripts the agent wrote.
- **A rewrite never carries `"allow"`.** `updatedInput` alone applies (verified live 2026-10-26, Claude Code 2.1.295
  desktop); with an ask it rides along so the user approves the call as it will run.
- **One list in two places is pinned by a test**: `lists/ad-hosts.txt` and `AD_HOSTS`, and `policy.AD_PATHS` /
  `policy.REDIRECTORS` and their copies in `page/shield.js` (`tests/test_lists.py`). Edit both.
- **`browser_shield/uts46.txt` is generated**: `node scripts/gen_uts46.js`; `--check` (run by the tests) fails when
  Node's ICU disagrees with the committed table. Never edit it by hand.
- **`page/shield.js` must leave local dev hosts alone, replace no built-in function on YouTube, and never hide or
  dismiss YouTube's enforcement notice.** Bump `VERSION` when its behaviour changes (an older copy on a page stands
  down for a newer one).
- **The repo root is also a Claude Code plugin** (`.claude-plugin/plugin.json`, `hooks/hooks.json` naming the mod
  `hooks/autorun.ts`). The mod is TypeScript with erasable syntax only (Node strips its types for the unit tests: no
  `enum`, `namespace` or parameter properties; `import type` only). It never makes a permission decision: its browser
  call goes through `$.tool.call`, so the guard, the user's rules and auto mode judge it like the model's own.
- **No downloaded list is committed**: `cache/` is git-ignored; `scripts/update_lists.py` fetches only the https
  URLs in `lists/sources.json`. The weekly LaunchAgent runs it from the installed copy (the user's go, 2026-10-27);
  any other run, or a new source, still needs the user's go-ahead (each fetch is a download).
- Verifying a page-script change for real: open a monetized YouTube video in the Browser pane, run `browserShield()`,
  and read `adsSkipped`, the log and the video's `currentTime` and `muted` (README "Use").
