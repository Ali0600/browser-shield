# browser-shield — agent contract

## Gate (run before every commit)

```bash
/usr/bin/python3 -I -m unittest discover -s tests -t . && node --test tests/shield.test.js
```

About 25 s; most of it is the every-code-point sweep in `tests/test_urls.py`. After changing a guard, also run
`/usr/bin/python3 -I scripts/mutation_check.py [mutant-id ...]` and add a mutant to `tests/mutants.json` for the new
guard: each must turn a named test red (a test that has never failed proves nothing).

## Rules

- **The hook runs under `/usr/bin/python3` (3.9) with `-I`, stdlib only.** No third-party imports, no syntax newer
  than 3.9 at runtime (`from __future__ import annotations` makes `X | None` hints safe). No `dataclasses`: the hook
  starts on every guarded call and import time is its cost (`python -X importtime`).
- **The hook is live in every session the moment a file here is saved** (`~/.claude/settings.json` points at this
  checkout). Run the gate before saving a change to `hooks/` or `browser_shield/`, and never leave either half-edited.
  Edit test files that contain lure-shaped strings (`curl … | sh`) with the Edit tool: the guard reads Bash command
  lines and would ask the user about a heredoc carrying them.
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
- **No downloaded list is committed**: `cache/` is git-ignored; `scripts/update_lists.py` fetches only the https
  URLs in `lists/sources.json`, and only with the user's go-ahead (each fetch is a download).
- Verifying a page-script change for real: open a monetized YouTube video in the Browser pane, run `browserShield()`,
  and read `adsSkipped`, the log and the video's `currentTime` and `muted` (README "Use").
