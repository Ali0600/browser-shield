# Security

browser-shield guards the calls an AI agent (Claude Code) makes to the browsers, shells and fetch tools it drives.

- **The hook is the boundary.** `hooks/guard.py` sees each call before it runs and refuses or asks about it; when it
  cannot judge a call it refuses it, and with `"onFailure": "block"` a hook that fails to run refuses it too.
- **The page script is best effort.** `page/shield.js` runs inside the page after the page's own code, so a hostile
  page can undo it. It skips ads and stops accidental clicks; it is not a defence against a page.
- **What it does not cover** is listed in the README ("Not covered"): for example a program that opens a browser by
  itself, a URL held in a shell variable, or secrets assembled at run time inside a script.

## Reporting a bypass

A bypass is a call the guard should refuse or ask about (by the rules in the README and `docs/DECISIONS.md`) that it
lets through, or a way to make it misread a URL. Please do not open a public issue for one: contact
[@Ali0600](https://github.com/Ali0600) on GitHub first, with the tool name and input that got through.
