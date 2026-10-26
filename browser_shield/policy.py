"""The guard's verdict on one tool call: allow, ask the user, or deny, with the reasons.

Pure functions over the tool name and its input, so every rule is testable without Claude Code running.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, unquote

from . import leaks, urls
from .lists import ROOT, Lists

SHIELD_JS = ROOT / "page" / "shield.js"
SHIELD_CALL = "browserShield()"  # javascript_tool text the guard swaps for the page script

DENY, ASK, ALLOW = "deny", "ask", "allow"
_RANK = {ALLOW: 0, ASK: 1, DENY: 2}

SCHEMES_OK = {"http", "https"}
# Tools that run a shell command line: Claude Code's Bash, and the desktop app's Terminal panel (the user's own
# login shell, outside any sandbox).
SHELL_TOOLS = {"Bash", "mcp__terminal__run_in_terminal"}
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
# Pages where one click spends money, grants an app access to an account, or changes how the account is secured.
ASK_PATHS = [
    (re.compile(r"^accounts\.google\.com$"), re.compile(r"^/(o/oauth2|signin/oauth)")),
    (re.compile(r"^myaccount\.google\.com$"), re.compile(r"^/(security|signinoptions|permissions)")),
    (re.compile(r"^github\.com$"), re.compile(r"^/(login/oauth/authorize|settings/(tokens|keys|security|applications))")),
    (re.compile(r"(^|\.)roblox\.com$"), re.compile(r"^/(upgrades|premium|giftcards|redeem|transactions)", re.I)),
]
# Link wrappers whose real destination sits in a query parameter (a YouTube description link goes through
# youtube.com/redirect?q=...). The destination is judged too.
REDIRECTORS = [
    (re.compile(r"(^|\.)google\.[a-z.]+$"), re.compile(r"^/url$"), ("q", "url")),
    (re.compile(r"(^|\.)youtube\.com$"), re.compile(r"^/redirect$"), ("q",)),
    (re.compile(r"^l[m]?\.facebook\.com$"), re.compile(r"^/l\.php$"), ("u",)),
    (re.compile(r"^l\.instagram\.com$"), re.compile(r"^/$"), ("u",)),
    (re.compile(r"^out\.reddit\.com$"), re.compile(r"^/"), ("url",)),
    (re.compile(r"^steamcommunity\.com$"), re.compile(r"^/linkfilter/?$"), ("url", "u")),
]
_JS_SINK = re.compile(
    r"""(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\s*\(|window\.open\s*\(|\.src\s*=|fetch\s*\("""
    r"""|sendBeacon\s*\(|new\s+WebSocket\s*\(|new\s+EventSource\s*\(|import\s*\()\s*(['"`])([^'"`\s]+)\1"""
)
# Shell commands a "paste this into Terminal" lure (ClickFix) ends in. Each one asks the user; none is refused.
SHELL_LURES = [
    # The shell name must end the command (or take options): "curl|bash" quoted inside an echo or a test string is
    # text, not a pipeline.
    (re.compile(r"\b(curl|wget)\b[^|;&\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b(?=\s*(?:$|[;&|)<>\n]|-))"),
     "pipes a download straight into a shell"),
    (re.compile(r"\b(ba|z)?sh\s+-c\s+[\"']?\$\(\s*(curl|wget)\b"), "runs a downloaded script"),
    (re.compile(r"\b(ba|z)?sh\s+-c\s+[\"'][^\"'\n]*\b(curl|wget)\b[^\"'\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"),
     "runs a downloaded script"),
    (re.compile(r"\bbase64\s+(-d|-D|--decode)\b[^\n]*\|\s*(ba|z)?sh\b(?=\s*(?:$|[;&|)<>\n]|-))"),
     "decodes and runs a hidden script"),
    (re.compile(r"\b(curl|wget)\b[^\n]*(&&|;|\|\|)\s*(sudo\s+)?((ba|z|da|k)?sh\s+[^-\s]|chmod\s+\+?[0-7]*x)"),
     "downloads a script and then runs it"),
    (re.compile(r"\b((ba|z|da|k)?sh|source|\.)\s+<\(\s*(curl|wget)\b|\beval\s+[\"']?\$\(\s*(curl|wget)\b"),
     "runs a downloaded script"),
    (re.compile(r"\bxattr\s+(-[a-z]*d[a-z]*\s+com\.apple\.quarantine|-[a-z]*c[a-z]*\b)"), "removes macOS's download quarantine"),
    (re.compile(r"\bspctl\s+--master-disable\b"), "turns Gatekeeper off"),
    (re.compile(r"\binstaller\s+-pkg\b"), "installs a package"),
    (re.compile(r"\bhdiutil\s+attach\b"), "mounts a disk image"),
]
# A script that hides or dismisses YouTube's ad-blocker notice is evasion, not ad blocking: the shield stops there.
_EVASION = re.compile(
    r"(ytd-enforcement-message-view-model|tp-yt-iron-overlay-backdrop|enforcement-message|#dismiss-button)"
    r"[^;]{0,120}?(\.(remove|click|replaceWith)\s*\(|removeChild|\.style\b|\.hidden\b|display\s*:\s*none)",
    re.I | re.S,
)
INJECTION_PHRASES = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+instructions"
    r"|if\s+you\s+are\s+an?\s+(ai|llm|language\s+model|assistant|agent|bot)\b"
    r"|\[\s*system(\s+override)?\s*\]|<\|im_start\|>|\bnew\s+instructions\s*:"
    r"|\b(ai|llm)\s+agents?\s+(must|should)\s+(now\s+)?(run|open|paste|click|send|type|download)",
    re.I,
)


class Verdict:
    __slots__ = ("decision", "reasons")

    def __init__(self, decision: str = ALLOW, reasons: Optional[list] = None):
        self.decision = decision
        self.reasons = list(reasons or [])

    def raise_to(self, decision: str, reason: str) -> None:
        if _RANK[decision] > _RANK[self.decision]:
            self.decision = decision
        self.reasons.append(reason)

    def absorb(self, other: "Verdict") -> None:
        if other.reasons:
            self.raise_to(other.decision, "; ".join(other.reasons))


class Context:
    """What the call's surroundings change: WebFetch only reads (no session, nothing saved, no app launched), and the
    session's own folders (project and scratchpad) hold files Claude may open in the browser without asking."""

    __slots__ = ("fetch_only", "roots")

    def __init__(self, fetch_only: bool = False, roots: tuple = ()):
        self.fetch_only = fetch_only
        self.roots = tuple(os.path.realpath(r) for r in roots if isinstance(r, str) and r)

    def with_fetch(self, fetch_only: bool) -> "Context":
        c = Context(fetch_only)
        c.roots = self.roots
        return c

    def inside_roots(self, path: str) -> bool:
        real = os.path.realpath(path)
        return any(real == r or real.startswith(r.rstrip("/") + "/") for r in self.roots)


NO_CONTEXT = Context()
# GitHub and GitLab pages that show a file (repository views, not the file itself).
SOURCE_VIEWS = [
    (re.compile(r"^(www\.)?github\.com$"), re.compile(r"^/[^/]+/[^/]+(/(blob|tree|blame|commits?|pull|issues)/.*)?/?$")),
    (re.compile(r"^(www\.)?gitlab\.com$"), re.compile(r"/-/(blob|tree|blame)/")),
]


def short_name(tool_name: str) -> tuple[str, str]:
    """(browser, tool): ("pane", "navigate"), ("chrome", ...), ("web", "WebFetch"), ("shell", <name>), ("", name)."""
    m = re.match(r"^mcp__(?:remote-devices__)?Claude_Browser__(.+)$", tool_name)
    if m:
        return "pane", m.group(1)
    m = re.match(r"^mcp__claude-in-chrome__(.+)$", tool_name)
    if m:
        return "chrome", m.group(1)
    if tool_name == "WebFetch":
        return "web", "WebFetch"
    if tool_name in SHELL_TOOLS:
        return "shell", tool_name
    return "", tool_name


def _leak_verdict(found: list[leaks.Leak], where: str, soft: bool, v: Verdict, what: str) -> None:
    for leak in found:
        decision = ASK if soft and leak.kind in leaks.SOFT_IN_URLS else DENY
        v.raise_to(decision, f"{where}: {what} a {leak.kind} ({leak.excerpt})")


def check_url(raw: Any, lists: Lists, where: str, depth: int = 0, ctx: Context = NO_CONTEXT) -> Verdict:
    """Verdict on navigating to raw; where names the call for the reason text ("navigate", "batch item 2")."""
    v = Verdict()
    if not isinstance(raw, str) or not raw.strip():
        v.raise_to(DENY, f"{where}: no URL")
        return v
    if raw.strip() in ("back", "forward"):
        return v
    clean = urls.preprocess(raw)
    if urls.is_local_path(clean):
        if clean.startswith("\\"):
            v.raise_to(DENY, f"{where}: {clean[:60]} is a remote file share; the agent does not open those")
        else:
            _check_local_file(clean, where, v, ctx)
        return v
    found = leaks.find_leaks(clean)
    for extra in leaks.find_leaks(unquote(clean)):  # a %-encoded secret is still a secret
        if extra not in found:
            found.append(extra)
    _leak_verdict(found, where, True, v, "the URL carries")
    for reading in urls.interpretations(raw):
        try:
            t = urls.parse(reading)
        except urls.UnreadableURL as exc:
            v.raise_to(DENY, f"{where}: a browser could read this URL differently from the guard ({exc}); "
                             "write it out plainly as https://host/path")
            continue
        _check_target(t, lists, where, v, depth, ctx)
    return v


def _check_local_file(path: str, where: str, v: Verdict, ctx: Context) -> None:
    """A file on this Mac: fine inside the session's own folders, the user's call anywhere else."""
    if path.lower().startswith("file:"):
        path = unquote(re.sub(r"^file:(//(localhost)?)?", "", path, flags=re.I))
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path.startswith("~"):
        path = os.path.expanduser(path)
    elif not path.startswith("/") and ctx.roots:
        path = os.path.join(ctx.roots[0], path)
    if path.startswith("/") and ctx.inside_roots(path):
        return
    v.raise_to(ASK, f"{where}: {path[:80]} is a file on this Mac outside this session's folders; opening it in the "
                    "browser is the user's call")


def _check_target(t: urls.Target, lists: Lists, where: str, v: Verdict, depth: int, ctx: Context = NO_CONTEXT) -> None:
    if t.scheme == "file":
        if t.file_host:
            v.raise_to(DENY, f"{where}: file://{t.file_host}/ is a remote file share; the agent does not open those")
        else:
            _check_local_file(t.url, where, v, ctx)
        return
    if t.scheme in ("chrome-extension", "chrome"):
        m = re.match(r"^chrome-extension://([a-p]{32})(?:[/?#]|$)", t.url, re.I)
        if m and m.group(1).lower() in lists.extensions:
            return  # the user's own extension, listed in lists/extensions.local.txt
        v.raise_to(ASK, f"{where}: {t.url[:60]} is a browser or extension page (it can change settings or approve "
                        "what an extension does); add your own extensions' ids to lists/extensions.local.txt")
        return
    if t.scheme not in SCHEMES_OK:
        if t.url.lower() == "about:blank":
            return
        v.raise_to(DENY, f"{where}: {t.scheme}: URLs are not opened by the agent (only http and https)")
        return
    if t.userinfo:
        v.raise_to(ASK, f"{where}: the URL puts a user name before the host ({t.host}), a phishing disguise; "
                        "the user decides")
    for host_rx, path_rx in AD_PATHS:
        if host_rx.search(t.host) and path_rx.search(t.path):
            v.raise_to(DENY, f"{where}: {t.host}{t.path[:40]} is an ad-click redirect")
            return
    hit = lists.blocked_by(t.host)
    if hit:
        domain, source = hit
        v.raise_to(DENY, f"{where}: {t.host} is on the {source} blocklist (as {domain})")
        return
    if not ctx.fetch_only:  # WebFetch has no session to pay from and saves nothing
        asked = lists.asked_by(t.host)
        if asked:
            v.raise_to(ASK, f"{where}: {t.host} is a payment or account page ({asked[1]}); the user decides")
        for host_rx, path_rx in ASK_PATHS:
            if host_rx.search(t.host) and path_rx.search(t.path):
                v.raise_to(ASK, f"{where}: {t.host}{t.path[:40]} grants access, changes security or spends money")
        last = t.path.rsplit("/", 1)[-1].lower()
        ext = last.rsplit(".", 1)[-1] if "." in last else ""
        if ext in DOWNLOAD_EXT and not any(h.search(t.host) and pth.search(t.path) for h, pth in SOURCE_VIEWS):
            v.raise_to(ASK, f"{where}: {last} is a download ({ext}); downloads need the user's go-ahead")
    _check_network(t.host, where, v)
    if depth < 2:
        for target in redirect_targets(t):
            v.absorb(check_url(target, lists, f"{where} (link wrapper to {target[:60]})", depth + 1, ctx))


def _check_network(host: str, where: str, v: Verdict) -> None:
    if host.endswith(".local"):
        v.raise_to(ASK, f"{where}: {host} is a device on the local network")
        return
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    mapped = getattr(ip, "ipv4_mapped", None)
    ip = mapped or ip
    if ip.is_loopback:
        return  # the user's own dev servers
    if ip.is_private or ip.is_link_local:
        v.raise_to(ASK, f"{where}: {host} is a device on the local network (a router or NAS admin page)")
    else:
        v.raise_to(ASK, f"{where}: {host} is a bare public IP address, a common sign of a malware host")


def redirect_targets(t: urls.Target) -> list[str]:
    """The destinations a link wrapper (google.com/url, youtube.com/redirect, ...) sends the browser on to."""
    out = []
    for host_rx, path_rx, params in REDIRECTORS:
        if host_rx.search(t.host) and path_rx.search(t.path):
            query = parse_qs(t.query)
            for p in params:
                out.extend(q for q in query.get(p, []) if q.strip())
    return out


def check_text(text: Any, where: str, v: Verdict) -> None:
    """Deny text that would hand a secret or card number to a web page (a number value is text to a form too)."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        text = str(text)
    if isinstance(text, str):
        _leak_verdict(leaks.find_leaks(text), where, False, v, "would send the page")


def check_script(text: Any, lists: Lists, where: str, v: Verdict) -> None:
    """A script may not leak secrets, nor navigate, open a pop-up or send a request to a URL the guard would refuse."""
    if not isinstance(text, str):
        return
    _leak_verdict(leaks.find_leaks(text), where, True, v, "the script carries")
    if _EVASION.search(text):
        v.raise_to(DENY, f"{where}: the script would hide or dismiss YouTube's ad-blocker notice; when YouTube shows "
                         "it, the shield stands down and the user hears about it instead")
    for m in _JS_SINK.finditer(text):
        target = m.group(2)
        if target.startswith(("/", "#", "?", "${", ".")) or target in ("about:blank",):
            continue
        target = re.sub(r"^ws(s?):", r"http\1:", target, flags=re.I)  # a WebSocket host is judged like a web host
        sub = check_url(target, lists, f"{where} (script sends the page to {target[:60]})")
        v.absorb(sub)


def check_shell(command: Any, where: str, v: Verdict) -> None:
    if not isinstance(command, str):
        return
    for rx, what in SHELL_LURES:
        if rx.search(command):
            v.raise_to(ASK, f"{where}: this command {what}, the last step of a 'paste this into Terminal' lure; "
                            "the user decides")


def is_shield(text: Any) -> bool:
    """The shield call, or the page script it expands to: PostToolUse sees the input after the guard's rewrite."""
    return isinstance(text, str) and (
        text.strip().rstrip(";") == SHIELD_CALL or text.lstrip().startswith("// browser-shield page script")
    )


def expand_shield(text: Any) -> Optional[str]:
    """The page script, when the agent's javascript_tool text is exactly the shield call; else None."""
    if isinstance(text, str) and text.strip().rstrip(";") == SHIELD_CALL and SHIELD_JS.exists():
        return SHIELD_JS.read_text(encoding="utf-8")
    return None


def evaluate(tool_name: str, tool_input: Any, lists: Optional[Lists] = None, ctx: Context = NO_CONTEXT) -> Verdict:
    browser, tool = short_name(tool_name)
    v = Verdict()
    if not browser or not isinstance(tool_input, dict):
        return v
    if browser == "shell":
        check_shell(tool_input.get("command"), tool, v)
        return v
    lists = lists if lists is not None else Lists()
    if tool == "browser_batch":
        _check_batch(tool_input, lists, v, ctx)
    elif tool == "preview_start" and "url" not in tool_input:
        # A server from .claude/launch.json by name; a configuration may carry a url of its own, judged here.
        for url in launch_urls(tool_input.get("name"), ctx):
            v.absorb(check_url(url, lists, f"preview_start ({tool_input.get('name')} in .claude/launch.json)", 0, ctx))
    else:
        _check_action(tool, tool_input, lists, tool, v, ctx)
    return v


def _check_action(tool: str, inp: dict, lists: Lists, where: str, v: Verdict, ctx: Context = NO_CONTEXT) -> None:
    if tool in ("navigate", "preview_start", "WebFetch"):
        v.absorb(check_url(inp.get("url"), lists, where, 0, ctx.with_fetch(tool == "WebFetch")))
    elif tool == "javascript_tool":
        check_script(inp.get("text"), lists, where, v)
    elif tool == "computer" and inp.get("action") in ("type", "key"):
        text = inp.get("text")
        check_text(text, where, v)
        if inp.get("action") == "key" and isinstance(text, str):
            check_text(text.replace(" ", ""), where, v)  # a secret pressed one key at a time
    elif tool == "form_input":
        check_text(inp.get("value"), where, v)
    elif tool in ("file_upload", "upload_image"):
        v.raise_to(ASK, f"{where}: uploads a file from this Mac to a web page; the user decides")
    elif tool == "gif_creator" and (inp.get("download") or inp.get("coordinate")):
        v.raise_to(ASK, f"{where}: exports a screen recording (an upload to the page or a download); the user decides")


def launch_urls(name: Any, ctx: Context) -> list:
    """The url of the named configuration in the session's .claude/launch.json, if it has one."""
    out = []
    for root in ctx.roots[:1]:
        path = os.path.join(root, ".claude", "launch.json")
        try:
            with open(path, encoding="utf-8") as fh:
                configs = json.load(fh).get("configurations", [])
        except (OSError, ValueError, AttributeError):
            continue
        for c in configs if isinstance(configs, list) else []:
            if isinstance(c, dict) and c.get("name") == name and isinstance(c.get("url"), str):
                out.append(c["url"])
    return out


def _check_batch(inp: dict, lists: Lists, v: Verdict, ctx: Context = NO_CONTEXT) -> None:
    actions = inp.get("actions")
    if not isinstance(actions, list):
        return
    for i, item in enumerate(actions):
        if not isinstance(item, dict) or not isinstance(item.get("input"), dict):
            continue
        name = str(item.get("name", ""))
        name = short_name(name)[1] if name.startswith("mcp__") else name  # an item named with the full tool name
        _check_action(name, item["input"], lists, f"batch item {i + 1} ({item.get('name')})", v, ctx)


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
            elif item.get("name") == "javascript_tool" and is_shield(item["input"].get("text")):
                shield_after = True
    if not urls_loaded or shield_after:
        return None
    try:
        host = urls.parse(urls.interpretations(urls_loaded[-1])[0]).host
    except urls.UnreadableURL:
        return None
    return host if re.search(r"(^|\.)youtube(-nocookie)?\.com$", host) else None


def _response_text(tool_response: Any) -> str:
    """Every string in a tool response, joined: MCP responses are content-block lists whose shape is undocumented."""
    if isinstance(tool_response, str):
        return tool_response
    parts: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for val in x.values():
                walk(val)
        elif isinstance(x, list):
            for val in x:
                walk(val)

    walk(tool_response)
    return "\n".join(parts) if parts else json.dumps(tool_response)


def landed_on_blocked(tool_name: str, tool_response: Any, lists: Lists) -> Optional[str]:
    """After a navigation: the blocklisted host the page ended up on (a redirect), or None."""
    browser, tool = short_name(tool_name)
    if browser not in ("pane", "chrome") or tool not in ("navigate", "browser_batch", "preview_start"):
        return None
    text = _response_text(tool_response)
    for raw in re.findall(r"(?m)^(?:\[navigate\] )?navigated to (\S+)", text):
        try:
            t = urls.parse(raw)
        except urls.UnreadableURL:
            continue
        hit = lists.blocked_by(t.host) if t.scheme in SCHEMES_OK else None
        if hit:
            return f"{t.host} (on the {hit[1]} blocklist)"
    return None


def injection_phrase(tool_name: str, tool_response: Any) -> Optional[str]:
    """A phrase in page text addressed to an AI agent ("ignore previous instructions"), or None."""
    browser, tool = short_name(tool_name)
    if browser not in ("pane", "chrome", "web") or tool not in (
        "get_page_text", "read_page", "find", "javascript_tool", "WebFetch", "browser_batch"
    ):
        return None
    m = INJECTION_PHRASES.search(_response_text(tool_response))
    return " ".join(m.group(0).split())[:80] if m else None
