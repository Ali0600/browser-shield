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

## The plugin (what other people install)

`plugins/browser-shield/` is the plugin the marketplace (`.claude-plugin/marketplace.json`) lists. Its manifest and
`hooks/hooks.json` are written by hand; everything else in it is a copy of the runtime files. After editing any of
those (the same list as above), rebuild the copy in the same commit; the gate's `tests/test_plugin.py` fails on drift:

```bash
/usr/bin/python3 -I scripts/build_plugin.py
```

- **People get a change only when `version` in `plugins/browser-shield/.claude-plugin/plugin.json` goes up**: bump it
  (semver) in the commit that releases, after the gate, and never set a version in `marketplace.json` too (the
  manifest's wins silently). A push to `main` without a bump reaches nobody who has it installed.
- **The plugin is `browser-shield`, never `claude-…`**: Claude Code refuses third-party plugin names starting with
  `claude-`. The marketplace is `claude-browser-shield`, so the install id is `browser-shield@claude-browser-shield`.
- **No symbolic links in the plugin folder**: the plugin directory drops them, and Claude Code skips links that leave
  the plugin for a local install. `build_plugin.py` copies only files git tracks (never `*.local.txt`).
- **Its hooks pass `${CLAUDE_PLUGIN_DATA}` as `BROWSER_SHIELD_DATA`**, where the lists, `guard.log`, the markers and
  `local/` live (`lists.data_dirs`). An empty or relative value refuses guarded calls with the reason. Its matchers
  equal the README's settings JSON (pinned by a test); `claude plugin validate plugins/browser-shield` after changing
  its manifest or hooks.

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
- **Cookie banners are rejected, never accepted.** `CONSENT_CONFIG` in `page/shield.js` (`autoAction: 'optOut'`,
  `heuristicMode: 'off'`, `enablePrehide: false`, `enableGeneratedRules: false`, five `disabledCmps`) is pinned by
  `tests/shield.test.js` and by mutants; loosening any value needs the owner's go and a reason in docs/DECISIONS.md.
  `ACCEPT_ALL` (the backstop's labels) is in the page script and in the test, pinned to each other. The two slots
  (`/*@autoconsent-engine@*/ null`, then `/*@autoconsent-rules@*/ null`) stay after the local-host gate, each once;
  `policy.expand_shield` fills them, for the Browser pane only (`policy.rewrite`), and refuses a script without them.
- **`page/vendor/autoconsent/` is vendored, never edited** (MPL-2.0, byte for byte from npm; `SOURCE.json` holds each
  file's sha256 and a test compares). Update only with `/usr/bin/python3 -I scripts/update_autoconsent.py VERSION`
  after the owner's go (a download), then read the new engine before it ships, as on 2026-10-30 (docs/DECISIONS.md),
  update `NOTICE`, and run the gate and the full mutation check: the page script wraps three of its methods
  (`_start`, `doOptOut`, `restoreFocus`) and a test requires every `disabledCmps` name to still exist.
- **The repo root is also a Claude Code plugin, `browser-shield-autorun`** (`.claude-plugin/plugin.json`,
  `hooks/hooks.json` naming the mod `hooks/autorun.ts`), loaded only with `--plugin-dir` and not in the marketplace
  (the user left it off, 2026-10-26). The mod is TypeScript with erasable syntax only (Node strips its types for the unit tests: no
  `enum`, `namespace` or parameter properties; `import type` only). It never makes a permission decision: its browser
  call goes through `$.tool.call`, so the guard, the user's rules and auto mode judge it like the model's own.
- **No downloaded list is committed**: `cache/` is git-ignored; `scripts/update_lists.py` fetches only the https
  URLs in `lists/sources.json`. The weekly LaunchAgent runs it from the installed copy (the user's go, 2026-10-27),
  and the plugin's session-start hook runs it on installers' machines (`--if-stale`: a week old or missing); any
  other run here, or a new source, still needs the user's go-ahead (each fetch is a download). The one downloaded
  thing committed is `page/vendor/autoconsent/` (code, read before it went in; rule above).
- Verifying a page-script change for real: open a monetized YouTube video in the Browser pane, run `browserShield()`,
  and read `adsSkipped`, the log and the video's `currentTime` and `muted` (README "Use").
