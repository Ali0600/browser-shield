"""Claude Code hook: browser-shield's guard on the agent's own browser tool calls.

PreToolUse  -> deny or ask on a harmful call, swap `browserShield()` for the page script, or stay silent (allow).
PostToolUse -> after a YouTube page loads without the shield, tell the agent to run it.

Reads the hook event as JSON on stdin and answers in Claude Code's hook JSON on stdout. Python 3.9 stdlib only.
If the guard itself breaks it lets the call through and says so in a systemMessage, so a bug here is loud but never
locks the browser.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The log and the once-per-session markers; tests point this elsewhere so they never write the live log.
STATE_DIR = Path(os.environ.get("BROWSER_SHIELD_STATE_DIR") or ROOT / "cache")
LOG = STATE_DIR / "guard.log"
LOG_LIMIT = 1_000_000


def log(entry: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > LOG_LIMIT:
            LOG.replace(LOG.with_suffix(".log.1"))
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}) + "\n")
    except OSError:
        pass


def once_per_session(session_id: str, key: str) -> bool:
    """True the first time key is seen in this session (a marker file), so a standing warning is shown once."""
    marker = STATE_DIR / "sessions" / f"{session_id or 'unknown'}.{key}"
    if marker.exists():
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    return True


def pre_tool_use(event: dict) -> dict:
    from browser_shield import policy
    from browser_shield.lists import Lists

    tool, tool_input = event.get("tool_name", ""), event.get("tool_input")
    lists = Lists()
    verdict = policy.evaluate(tool, tool_input, lists)
    out: dict = {}
    if verdict.decision != policy.ALLOW:
        reason = "browser-shield: " + " | ".join(verdict.reasons)
        log({"event": "PreToolUse", "tool": tool, "decision": verdict.decision, "reasons": verdict.reasons})
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict.decision,
            "permissionDecisionReason": reason,
        }
        return out
    updated = policy.rewrite(tool, tool_input)
    if updated is not None:
        log({"event": "PreToolUse", "tool": tool, "decision": "rewrite", "reasons": ["browserShield() expanded"]})
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "updatedInput": updated}
    if lists.index is None and policy.short_name(tool)[1] in ("navigate", "preview_start", "browser_batch", "WebFetch"):
        if once_per_session(event.get("session_id", ""), "no-index"):
            out["systemMessage"] = (
                "browser-shield: the malware and phishing lists are not installed, so only the curated lists are "
                "checked. Run: python3 ~/projects/automation/browser-shield/scripts/update_lists.py"
            )
    return out


def post_tool_use(event: dict) -> dict:
    from browser_shield import policy

    host = policy.needs_shield_reminder(event.get("tool_name", ""), event.get("tool_input"))
    if not host:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"browser-shield: the page shield is not active on {host} yet, so ads on this page play until it "
                "is. It is activated by a javascript_tool call whose text is exactly `browserShield()` (the guard "
                "hook swaps in the page script); the call returns the shield's status, and `enforcement: true` in "
                "that status means YouTube is showing its ad-blocker notice, which the user should hear about."
            ),
        }
    }


def main() -> int:
    try:
        sys.path.insert(0, str(ROOT))
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        name = event.get("hook_event_name")
        out = pre_tool_use(event) if name == "PreToolUse" else post_tool_use(event) if name == "PostToolUse" else {}
    except Exception as exc:  # the guard must never take the browser down with it
        log({"event": "error", "error": repr(exc)})
        out = {"systemMessage": f"browser-shield guard failed ({exc!r}); this call was not checked"}
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
