"""Build plugins/browser-shield/, the plugin people install with /plugin, from this repo's own files.

    /usr/bin/python3 -I scripts/build_plugin.py          # copy the guard's runtime files into the plugin folder
    /usr/bin/python3 -I scripts/build_plugin.py --check  # exit 1, naming each file, if the copy has drifted

Claude Code installs a plugin by copying its folder into its own cache, and the plugin directory drops symbolic
links, so the plugin holds real copies of the files the guard runs. The plugin's own files (its manifest and hook
config) are written by hand; everything else in the folder is a copy, and the gate's check fails on any drift.
Only files git tracks are copied, so the personal lists (lists/*.local.txt) never reach the plugin.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "browser-shield"
RUNTIME = ("browser_shield", "page", "lists", "hooks/guard.py", "scripts/update_lists.py", "LICENSE", "NOTICE")
OWN = (".claude-plugin/plugin.json", "hooks/hooks.json")
# What a working tree holds beside the tracked runtime files: never copied, never expected in the plugin.
UNTRACKED = ("__pycache__", ".DS_Store")


def untracked(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in UNTRACKED for p in parts) or rel.endswith((".pyc", ".local.txt"))


def source_files(root: Path = ROOT) -> list[str]:
    """The runtime files in root, as relative paths: what the plugin must hold a copy of. Works without git, so the
    check also runs in an installed release, which is a git archive."""
    found = []
    for name in RUNTIME:
        path = root / name
        if path.is_file():
            found.append(name)
        elif path.is_dir():
            found += [p.relative_to(root).as_posix() for p in path.rglob("*") if p.is_file()]
        else:
            raise FileNotFoundError(f"{name}: a runtime file the plugin needs is missing")
    return sorted(f for f in found if not untracked(f))


def plugin_files(plugin: Path = PLUGIN) -> list[str]:
    if not plugin.is_dir():
        return []
    return sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob("*")
                  if (p.is_file() or p.is_symlink()) and not untracked(p.relative_to(plugin).as_posix()))


def check(root: Path = ROOT, plugin: Path = PLUGIN) -> list[str]:
    """Every way the plugin folder differs from what a build would make, one line each; empty when it matches."""
    problems = []
    wanted = source_files(root)
    have = plugin_files(plugin)
    for rel in OWN:
        if not (plugin / rel).is_file():
            problems.append(f"{rel}: the plugin's own file is missing")
    for rel in wanted:
        copy = plugin / rel
        if copy.is_symlink():
            problems.append(f"{rel}: a symbolic link in the plugin (the plugin directory drops links)")
        elif not copy.is_file():
            problems.append(f"{rel}: missing from the plugin")
        elif copy.read_bytes() != (root / rel).read_bytes():
            problems.append(f"{rel}: the plugin's copy differs from the source")
    for rel in have:
        if rel not in wanted and rel not in OWN:
            problems.append(f"{rel}: in the plugin but not a runtime file (left from an earlier build?)")
    return problems


def tracked(root: Path = ROOT) -> set[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *RUNTIME], check=True,
                         capture_output=True).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


def build(root: Path = ROOT, plugin: Path = PLUGIN) -> list[str]:
    """Copy the tracked runtime files into the plugin and remove stale copies; returns what changed."""
    wanted = source_files(root)
    git_files = tracked(root)
    stray = [rel for rel in wanted if rel not in git_files]
    if stray:
        # A file the check expects but git does not track (a new personal or generated file): copying it could
        # publish something private, so stop and name it.
        raise SystemExit("refusing to copy files git does not track: " + ", ".join(stray))
    changed = []
    for rel in wanted:
        source, copy = root / rel, plugin / rel
        data = source.read_bytes()
        if copy.is_symlink() or not copy.is_file() or copy.read_bytes() != data:
            if copy.is_symlink():
                copy.unlink()
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(data)
            changed.append(f"copied {rel}")
    for rel in plugin_files(plugin):
        if rel not in wanted and rel not in OWN:
            (plugin / rel).unlink()
            changed.append(f"removed {rel}")
    return changed


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        problems = check()
        for line in problems:
            print(f"DRIFT {line}")
        if problems:
            print("run: /usr/bin/python3 -I scripts/build_plugin.py")
        return 1 if problems else 0
    if argv:
        print(__doc__.split("\n\n")[1])
        return 2
    changed = build()
    print("\n".join(changed) or "the plugin is up to date")
    problems = check()
    for line in problems:
        print(f"DRIFT {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
