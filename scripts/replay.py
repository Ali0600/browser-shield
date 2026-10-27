"""Replay every tool call in this Mac's Claude Code transcripts through the guard, to count the real work it would stop.

    /usr/bin/python3 -I scripts/replay.py                       # non-allow verdicts of this checkout's policy, by rule
    /usr/bin/python3 -I scripts/replay.py --compare ~/.local/share/browser-shield/current
                                                                # every call whose verdict differs from that copy's
    options: --since YYYY-MM-DD   --examples N (default 3)   --root DIR (default ~/.claude/projects)
             --index FILE (default: the installed copy's downloaded index, else this checkout's)

Read-only. It prints counts, rule wording and at most N redacted, shortened examples per rule; it writes nothing.
Each call is judged as hooks/guard.py would judge it: the tool input as recorded, the session's working folder and
scratchpad as roots, no lists for a shell call, and the hook's shell fast path (a command without one of its words
is let through unjudged, so a non-allow verdict there is reported as a fast-path miss). Run it after any change to
what the guard refuses or asks about, and read every new hit before installing.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("HOME") or str(Path.home()))


class Guard:
    """One copy of browser-shield (its policy, lists and hook fast path), loaded under its own module name."""

    def __init__(self, root: Path, alias: str, index: Path):
        self.root = root
        pkg_dir = root / "browser_shield"
        # A copy loaded earlier under this name would be served from Python's module cache: forget it first.
        for name in [m for m in sys.modules if m == alias or m.startswith(alias + ".") or m == f"{alias}_guard"]:
            del sys.modules[name]
        spec = importlib.util.spec_from_file_location(alias, pkg_dir / "__init__.py",
                                                      submodule_search_locations=[str(pkg_dir)])
        if spec is None or spec.loader is None:
            raise SystemExit(f"no browser_shield package in {root}")
        package = importlib.util.module_from_spec(spec)
        sys.modules[alias] = package
        spec.loader.exec_module(package)
        self.policy = importlib.import_module(f"{alias}.policy")
        self.leaks = importlib.import_module(f"{alias}.leaks")
        # Every copy reads the same downloaded index, so a verdict that moves moved because of the code or the
        # curated lists, never because a download landed in between.
        self.lists = importlib.import_module(f"{alias}.lists").Lists(index=index)
        hook = importlib.util.spec_from_file_location(f"{alias}_guard", root / "hooks" / "guard.py")
        guard = importlib.util.module_from_spec(hook)
        hook.loader.exec_module(guard)
        # The hook's shell fast path, as that copy has it (older copies test the raw command only).
        self.fast = getattr(guard, "shell_words_in", None) or (lambda c: bool(guard.SHELL_WORDS.search(c)))

    def judge(self, call: dict) -> tuple[str, str, bool]:
        """(decision, rule, fast-path miss) for one call, as the hook decides it."""
        name, inp = call["name"], call["input"]
        browser = self.policy.short_name(name)[0]
        ctx = self.policy.Context(roots=(call["cwd"], call["scratchpad"]))
        try:
            # A shell call gets the lists too: the hook passes none and the policy loads them only to judge a URL
            # the command opens, so the verdict is the same, read against the shared index.
            v = self.policy.evaluate(name, inp, self.lists, ctx)
        except Exception as exc:  # the hook refuses a call it cannot judge
            return "deny", f"guard error {type(exc).__name__}", False
        rule = rule_of(v.reasons[0], self.leaks) if v.reasons else ""
        skipped = browser == "shell" and isinstance(inp, dict) and not self.fast(str(inp.get("command", "")))
        if skipped and v.decision != self.policy.ALLOW:
            return "allow", rule, True
        return v.decision, rule, False


def rule_of(reason: str, leaks) -> str:
    """A reason's wording with the call's specifics masked, so calls group by the rule that stopped them."""
    text = re.sub(r"^[^:]{0,80}?: ", "", leaks.redact(reason), count=1)
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "…", text)
    text = re.sub(r"\S*[./@:]\S*", "…", text)
    return re.sub(r"\s+", " ", text)[:90]


def snapshot_of(record: dict) -> Optional[str]:
    att = record.get("attachment")
    if record.get("type") == "attachment" and isinstance(att, dict) and att.get("type") == "environment":
        snap = att.get("snapshot")
        if isinstance(snap, dict) and isinstance(snap.get("scratchpadDirectory"), str):
            return snap["scratchpadDirectory"]
    return None


def read_records(path: Path, stats: Counter):
    """Each JSON object in a transcript, in order. A final line without its newline is a write in progress."""
    try:
        data = path.read_bytes()
    except OSError:
        stats["files unreadable"] += 1
        return
    chunks = data.split(b"\n")
    if chunks and chunks[-1]:
        stats["partial last lines"] += 1
    for chunk in chunks[:-1]:
        if not chunk.strip():
            continue
        stats["lines"] += 1
        try:
            record = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            stats["lines unreadable"] += 1
            continue
        if not isinstance(record, dict):
            stats["lines not an object"] += 1
            continue
        yield record


def first_snapshot(path: Path, cache: dict) -> Optional[str]:
    if path not in cache:
        cache[path] = None
        if path.is_file():
            for record in read_records(path, Counter()):
                snap = snapshot_of(record)
                if snap:
                    cache[path] = snap
                    break
    return cache[path]


def parent_transcript(path: Path, root: Path) -> Optional[Path]:
    """<project>/<session>.jsonl for a subagent's <project>/<session>/subagents/.../agent-x.jsonl."""
    parts = path.relative_to(root).parts
    if len(parts) > 3 and parts[2] == "subagents":
        return root / parts[0] / f"{parts[1]}.jsonl"
    return None


def calls_in(path: Path, root: Path, stats: Counter, parents: dict) -> list:
    """Every tool call in one transcript, with the scratchpad its session had at that point."""
    found, snaps = [], []  # snaps: (position, scratchpad)
    for pos, record in enumerate(read_records(path, stats)):
        snap = snapshot_of(record)
        if snap:
            snaps.append((pos, snap))
        if record.get("type") != "assistant":
            continue
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            stats["tool calls"] += 1
            if not isinstance(block.get("name"), str) or not isinstance(block.get("id"), str):
                stats["tool calls unreadable"] += 1
                continue
            found.append({"pos": pos, "id": block["id"], "name": block["name"], "input": block.get("input"),
                          "cwd": record.get("cwd") if isinstance(record.get("cwd"), str) else None,
                          "session": record.get("sessionId"), "ts": str(record.get("timestamp", ""))})
    parent = parent_transcript(path, root)
    fallback = snaps[0][1] if snaps else (first_snapshot(parent, parents) if parent else None)
    for call in found:
        earlier = [s for p, s in snaps if p <= call["pos"]]
        if earlier:
            call["scratchpad"], call["from"] = earlier[-1], "snapshot"
        elif fallback:
            call["scratchpad"], call["from"] = fallback, "first or parent snapshot"
        else:
            proj = path.relative_to(root).parts[0]
            call["scratchpad"] = f"/private/tmp/claude-{os.getuid()}/{proj}/{call['session']}/scratchpad"
            call["from"] = "path template"
    return found


def collect(root: Path, since: str, stats: Counter) -> list:
    """The unique calls under root (a call copied into a fork or a resumed file counts once: its earliest copy)."""
    parents: dict = {}
    by_id: dict = {}
    for path in sorted(root.rglob("*.jsonl")):
        stats["files"] += 1
        for call in calls_in(path, root, stats, parents):
            kept = by_id.get(call["id"])
            if kept is None or call["ts"] < kept["ts"]:
                by_id[call["id"]] = call
    stats["unique tool calls"] = len(by_id)
    # Dated by its earliest copy: a call a fork copied later is still as old as the original.
    return sorted((c for c in by_id.values() if c["ts"][:10] >= since), key=lambda c: c["ts"])


def example(call: dict, leaks) -> str:
    text = leaks.redact(json.dumps(call["input"], ensure_ascii=False))
    return f"{call['ts'][:10]} {call['name'].split('__')[-1]}: {text[:140]}"


def main(argv: list[str]) -> int:
    installed = HOME / ".local" / "share" / "browser-shield" / "data" / "blocklist.sqlite"
    args = {"--since": "", "--examples": "3", "--root": str(HOME / ".claude" / "projects"), "--compare": "",
            "--index": str(installed if installed.exists() else REPO / "cache" / "blocklist.sqlite")}
    it = iter(argv)
    for arg in it:
        if arg not in args:
            print(__doc__.split("\n\n")[1])
            return 2
        args[arg] = next(it, "")
    root, limit = Path(args["--root"]).expanduser(), int(args["--examples"])
    stats: Counter = Counter()
    calls = collect(root, args["--since"], stats)
    index = Path(args["--index"]).expanduser()
    this = Guard(REPO, "bs_this", index)
    other = Guard(Path(args["--compare"]).expanduser(), "bs_other", index) if args["--compare"] else None
    judged: Counter = Counter()
    sources: Counter = Counter()
    buckets: dict = defaultdict(list)
    for call in calls:
        if not this.policy.short_name(call["name"])[0]:
            continue
        judged[call["name"].split("__")[-1] if call["name"].startswith("mcp__") else call["name"]] += 1
        sources[call["from"]] += 1
        decision, rule, miss = this.judge(call)
        if miss:
            buckets[("FAST-PATH MISS", rule)].append(call)
        if other is None:
            if decision != "allow":
                buckets[(decision, rule)].append(call)
            continue
        before, before_rule, _ = other.judge(call)
        if (before, before_rule) != (decision, rule):
            buckets[(f"{before} -> {decision}", rule or before_rule)].append(call)
    print(f"root {root}" + (f", calls since {args['--since']}" if args["--since"] else "")
          + f"; index {index}" + ("" if index.exists() else " (missing: curated lists only)"))
    print("read: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    print(f"judged: {sum(judged.values())} calls (" + ", ".join(f"{k} {v}" for k, v in judged.most_common()) + ")")
    print("scratchpad from: " + ", ".join(f"{k} {v}" for k, v in sources.most_common()))
    title = f"verdicts that differ from {other.root}" if other else "non-allow verdicts"
    print(f"{title}: {sum(len(v) for v in buckets.values())}")
    for (decision, rule), rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{len(rows)}] {decision} | {rule}")
        for call in rows[:limit]:
            print(f"    {example(call, this.leaks)}")
    if stats["files unreadable"] or not judged:
        print("\nFAILED " + ("some transcripts could not be read" if stats["files unreadable"] else "no call judged"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
