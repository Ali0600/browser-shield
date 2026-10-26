"""The guard's verdict on one browser tool call: allow, ask the user, or deny, with the reasons.

Pure functions over the tool name and its input, so every rule is testable without Claude Code running.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import leaks, urls
from .lists import ROOT, Lists

SHIELD_JS = ROOT / "page" / "shield.js"
SHIELD_CALL = "browserShield()"  # javascript_tool text the guard swaps for the page script

DENY, ASK, ALLOW = "deny", "ask", "allow"
_RANK = {ALLOW: 0, ASK: 1, DENY: 2}

# Hosts the browser may always reach, even when a downloaded list names them (a false positive there would break the
# tool, not protect the user). Suffix match: "youtube.com" covers "www.youtube.com".
SCHEMES_OK = {"http", "https"}
DOWNLOAD_EXT = {
    "dmg", "pkg", "mpkg", "exe", "msi", "bat", "cmd", "scr", "ps1", "sh", "command", "tool", "scpt", "applescript",
    "workflow", "terminal", "jar", "deb", "rpm", "apk", "ipa", "iso", "img", "bin", "run", "appimage", "xpi", "crx",
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz",
}
# Ad-click and ad-redirect paths on hosts that are otherwise fine.
AD_PATHS = [
    (re.compile(r"(^|\.)google\.[a-z.]+$"), re.compile(r"^/(aclk|pagead/)")),
    (re.compile(r"(^|\.)youtube\.com$"), re.compile(r"^/pagead/")),
]
_JS_NAVIGATION = re.compile(
    r"""(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\s*\(|window\.open\s*\(|\.src\s*=)\s*(['"`])([^'"`\s]+)\1"""
)


@dataclass
class Verdict:
    decision: str = ALLOW
    reasons: list[str] = field(default_factory=list)
    updated_input: Optional[dict] = None

    def raise_to(self, decision: str, reason: str) -> None:
        if _RANK[decision] > _RANK[self.decision]:
            self.decision = decision
        self.reasons.append(reason)


def short_name(tool_name: str) -> tuple[str, str]:
    """(browser, tool) for a browser tool name, e.g. ("pane", "navigate"); ("web", "WebFetch"); ("", name) otherwise."""
    m = re.match(r"^mcp__(?:remote-devices__)?Claude_Browser__(.+)$", tool_name)
    if m:
        return "pane", m.group(1)
    m = re.match(r"^mcp__claude-in-chrome__(.+)$", tool_name)
    if m:
        return "chrome", m.group(1)
    if tool_name == "WebFetch":
        return "web", "WebFetch"
    return "", tool_name


def check_url(raw: Any, lists: Lists, where: str) -> Verdict:
    """Verdict on navigating to raw; where names the call for the reason text ("navigate", "batch item 2")."""
    v = Verdict()
    if not isinstance(raw, str) or not raw.strip():
        v.raise_to(DENY, f"{where}: no URL")
        return v
    if raw.strip() in ("back", "forward"):
        return v
    for leak in leaks.find_leaks(urls.preprocess(raw)):
        v.raise_to(DENY, f"{where}: the URL carries a {leak.kind} ({leak.excerpt}); secrets never go into a URL")
    for reading in urls.interpretations(raw):
        try:
            t = urls.parse(reading)
        except urls.UnreadableURL as exc:
            v.raise_to(DENY, f"{where}: a browser could read this URL differently from the guard ({exc}); "
                             "write it out plainly as https://host/path")
            continue
        _check_target(t, lists, where, v)
    return v


def _check_target(t: urls.Target, lists: Lists, where: str, v: Verdict) -> None:
    if t.scheme not in SCHEMES_OK:
        if t.url.lower() == "about:blank":
            return
        v.raise_to(DENY, f"{where}: {t.scheme}: URLs are not opened by the agent (only http and https)")
        return
    for host_rx, path_rx in AD_PATHS:
        if host_rx.search(t.host) and path_rx.search(t.path):
            v.raise_to(DENY, f"{where}: {t.host}{t.path[:40]} is an ad-click redirect")
            return
    hit = lists.blocked_by(t.host)
    if hit:
        domain, source = hit
        v.raise_to(DENY, f"{where}: {t.host} is on the {source} blocklist (as {domain})")
        return
    last = t.path.rsplit("/", 1)[-1].lower()
    ext = last.rsplit(".", 1)[-1] if "." in last else ""
    if ext in DOWNLOAD_EXT:
        v.raise_to(ASK, f"{where}: {last} is a download ({ext}); downloads need the user's go-ahead")
    if t.is_ip and not _private_ip(t.host):
        v.raise_to(ASK, f"{where}: {t.host} is a bare public IP address, a common sign of a malware host")


def _private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def check_text(text: Any, where: str, v: Verdict) -> None:
    """Deny text that would hand a secret or card number to a web page."""
    if not isinstance(text, str):
        return
    for leak in leaks.find_leaks(text):
        v.raise_to(DENY, f"{where}: would send a {leak.kind} ({leak.excerpt}) to the page")


def check_script(text: Any, lists: Lists, where: str, v: Verdict) -> None:
    """A script may not leak secrets, nor navigate or open a pop-up to a URL the guard would refuse."""
    if not isinstance(text, str):
        return
    check_text(text, where, v)
    for m in _JS_NAVIGATION.finditer(text):
        target = m.group(2)
        if target.startswith(("/", "#", "?", "${")):
            continue
        sub = check_url(target, lists, f"{where} (script navigates to {target[:60]})")
        if sub.decision != ALLOW:
            v.raise_to(sub.decision, "; ".join(sub.reasons))


def expand_shield(text: Any) -> Optional[str]:
    """The page script, when the agent's javascript_tool text is exactly the shield call; else None."""
    if isinstance(text, str) and text.strip().rstrip(";") == SHIELD_CALL and SHIELD_JS.exists():
        return SHIELD_JS.read_text(encoding="utf-8")
    return None


def evaluate(tool_name: str, tool_input: Any, lists: Optional[Lists] = None) -> Verdict:
    browser, tool = short_name(tool_name)
    v = Verdict()
    if not browser or not isinstance(tool_input, dict):
        return v
    lists = lists if lists is not None else Lists()
    if tool in ("navigate", "WebFetch") or (tool == "preview_start" and "url" in tool_input):
        sub = check_url(tool_input.get("url"), lists, tool)
        if sub.reasons:
            v.raise_to(sub.decision, "; ".join(sub.reasons))
    elif tool == "browser_batch":
        _check_batch(tool_input, lists, v)
    else:
        _check_action(tool, tool_input, lists, tool, v)
    return v


def _check_action(tool: str, inp: dict, lists: Lists, where: str, v: Verdict) -> None:
    if tool == "navigate":
        sub = check_url(inp.get("url"), lists, where)
        if sub.reasons:
            v.raise_to(sub.decision, "; ".join(sub.reasons))
    elif tool == "javascript_tool":
        check_script(inp.get("text"), lists, where, v)
    elif tool == "computer" and inp.get("action") == "type":
        check_text(inp.get("text"), where, v)
    elif tool == "form_input":
        check_text(inp.get("value"), where, v)


def _check_batch(inp: dict, lists: Lists, v: Verdict) -> None:
    actions = inp.get("actions")
    if not isinstance(actions, list):
        return
    for i, item in enumerate(actions):
        if not isinstance(item, dict) or not isinstance(item.get("input"), dict):
            continue
        _check_action(str(item.get("name", "")), item["input"], lists, f"batch item {i + 1} ({item.get('name')})", v)


def rewrite(tool_name: str, tool_input: Any) -> Optional[dict]:
    """The tool input with every `browserShield()` script swapped for the page script, or None when nothing changes."""
    browser, tool = short_name(tool_name)
    if browser not in ("pane", "chrome") or not isinstance(tool_input, dict):
        return None
    if tool == "javascript_tool":
        js = expand_shield(tool_input.get("text"))
        return {**tool_input, "text": js} if js else None
    if tool == "browser_batch" and isinstance(tool_input.get("actions"), list):
        changed = False
        actions = []
        for item in tool_input["actions"]:
            if isinstance(item, dict) and item.get("name") == "javascript_tool" and isinstance(item.get("input"), dict):
                js = expand_shield(item["input"].get("text"))
                if js:
                    item = {**item, "input": {**item["input"], "text": js}}
                    changed = True
            actions.append(item)
        return {**tool_input, "actions": actions} if changed else None
    return None


def needs_shield_reminder(tool_name: str, tool_input: Any) -> Optional[str]:
    """The YouTube host a navigation just loaded without the shield following it in the same batch, else None."""
    browser, tool = short_name(tool_name)
    if browser not in ("pane", "chrome") or not isinstance(tool_input, dict):
        return None
    urls_loaded: list[str] = []
    shield_after = False
    if tool in ("navigate", "preview_start"):
        urls_loaded = [tool_input.get("url")] if isinstance(tool_input.get("url"), str) else []
    elif tool == "browser_batch" and isinstance(tool_input.get("actions"), list):
        for item in tool_input["actions"]:
            if not isinstance(item, dict) or not isinstance(item.get("input"), dict):
                continue
            if item.get("name") == "navigate" and isinstance(item["input"].get("url"), str):
                urls_loaded.append(item["input"]["url"])
                shield_after = False
            elif item.get("name") == "javascript_tool" and expand_shield(item["input"].get("text")) is not None:
                shield_after = True
    if not urls_loaded or shield_after:
        return None
    try:
        host = urls.parse(urls.interpretations(urls_loaded[-1])[0]).host
    except urls.UnreadableURL:
        return None
    return host if re.search(r"(^|\.)youtube(-nocookie)?\.com$", host) else None
