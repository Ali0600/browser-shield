"""plugins/browser-shield, the plugin people install with /plugin: its copy of the guard, its hook config, and its
hooks run the way Claude Code runs an installed plugin (a copy in a cache folder, a separate data folder)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import build_plugin  # noqa: E402
from browser_shield import lists  # noqa: E402

PLUGIN = ROOT / "plugins" / "browser-shield"
HOOKS = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]


def first_ad_host() -> str:
    return sorted(lists.read_list(ROOT / "lists" / "ad-hosts.txt"))[0]


def command(event: str) -> str:
    (entry,) = HOOKS[event]
    (hook,) = entry["hooks"]
    return hook["command"]


class Installed(unittest.TestCase):
    """A copy laid out as Claude Code installs one: the plugin in a cache folder, its data folder elsewhere."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "plugins" / "cache" / "claude-browser-shield" / "browser-shield" / "0.1.0"
        shutil.copytree(PLUGIN, self.root)
        self.data = self.tmp / "plugins" / "data" / "browser-shield-claude-browser-shield"
        self.data.mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()

    def run_hook(self, event: str, payload: dict, data=None) -> subprocess.CompletedProcess:
        data = str(self.data) if data is None else data
        # Claude Code fills the placeholders in and also exports them; the hook sees nothing else of its env. The
        # proxy is a closed port: a hook that downloads (a broken freshness check) fails at once, off the network.
        cmd = command(event).replace("${CLAUDE_PLUGIN_ROOT}", str(self.root)).replace("${CLAUDE_PLUGIN_DATA}", data)
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
               "CLAUDE_PLUGIN_ROOT": str(self.root), "CLAUDE_PLUGIN_DATA": data,
               "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
        return subprocess.run(["/bin/sh", "-c", cmd], input=json.dumps(payload), capture_output=True, text=True,
                              env=env, timeout=60)

    def pre(self, tool: str, tool_input: dict, data=None) -> dict:
        run = self.run_hook("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "plugin-test",
                                           "cwd": str(self.tmp), "tool_name": tool, "tool_input": tool_input}, data)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout) if run.stdout.strip() else {}

    def files_in_root(self) -> set:
        return {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}

    def test_the_copy_guards_and_keeps_its_state_in_the_data_folder(self):
        before = self.files_in_root()
        host = first_ad_host()
        out = self.pre("mcp__Claude_Browser__navigate", {"url": f"https://{host}/"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", out)
        self.assertEqual(self.pre("Bash", {"command": "ls"}), {}, "a plain shell call must pass silently")
        # The user's own block list and the downloaded index are read from the data folder.
        (self.data / "local").mkdir()
        (self.data / "local" / "block.local.txt").write_text("mine.example\n")
        feed = self.tmp / "feed.txt"
        feed.write_text("0.0.0.0 listed.example\n")
        lists.compile_index([("feed-x", feed)], self.data / "blocklist.sqlite")
        for target, named in (("mine.example", "block.local.txt"), ("listed.example", "feed-x")):
            with self.subTest(target=target):
                out = self.pre("mcp__Claude_Browser__navigate", {"url": f"https://{target}/"})
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", out)
                self.assertIn(named, out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertTrue((self.data / "guard.log").is_file(), "the log belongs in the data folder")
        self.assertEqual(self.files_in_root(), before, "nothing but bytecode may be written into the plugin's copy")

    def test_a_missing_index_is_reported_with_the_plugin_s_own_update(self):
        out = self.pre("mcp__Claude_Browser__navigate", {"url": "https://example.com/"})
        self.assertNotIn("permissionDecision", out.get("hookSpecificOutput", {}), out)
        message = out["systemMessage"]
        self.assertIn("when a session starts", message)
        self.assertIn(str(self.data / "update.log"), message)
        self.assertIn(f"BROWSER_SHIELD_DATA={self.data}", message, "the command shown must update this data folder")

    def test_without_a_data_folder_browser_calls_are_refused_and_shell_calls_still_run(self):
        out = self.pre("mcp__Claude_Browser__navigate", {"url": "https://example.com/"}, data="")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", out)
        self.assertIn("BROWSER_SHIELD_DATA", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.pre("Bash", {"command": "ls"}, data=""), {})

    def test_the_session_start_refresh_leaves_fresh_lists_alone_without_the_network(self):
        feed = self.tmp / "feed.txt"
        feed.write_text("0.0.0.0 listed.example\n")
        index = self.data / "blocklist.sqlite"
        lists.compile_index([("feed-x", feed)], index)
        stamp = time.time() - 3600
        os.utime(index, (stamp, stamp))
        run = self.run_hook("SessionStart", {"hook_event_name": "SessionStart", "session_id": "plugin-test"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.data / "update.log").read_text(), "", "a fresh index means no run and no log line")
        self.assertEqual(index.stat().st_mtime, stamp)


class Config(unittest.TestCase):
    def test_the_hooks_fail_closed_where_a_call_can_still_be_refused(self):
        (pre,) = HOOKS["PreToolUse"][0]["hooks"]
        (post,) = HOOKS["PostToolUse"][0]["hooks"]
        (start,) = HOOKS["SessionStart"][0]["hooks"]
        self.assertEqual(pre.get("onFailure"), "block")
        self.assertNotIn("onFailure", post, "a PostToolUse failure has nothing left to refuse")
        self.assertEqual((pre["timeout"], post["timeout"]), (10, 10))
        self.assertIs(start.get("async"), True, "the download must never hold up a session's start")

    def test_the_matchers_are_the_ones_the_readme_gives_for_settings(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        block = readme[readme.index('"PreToolUse": [{'):readme.index("Claude Code picks up an edit")]
        documented = re.findall(r'"matcher": "([^"]+)"', block)
        self.assertEqual(documented, [HOOKS["PreToolUse"][0]["matcher"], HOOKS["PostToolUse"][0]["matcher"]])

    def test_the_marketplace_lists_this_folder_under_a_name_claude_code_accepts(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        (entry,) = market["plugins"]
        self.assertEqual((ROOT / entry["source"]).resolve(), PLUGIN)
        self.assertEqual(entry["name"], manifest["name"])
        # Claude Code refuses third-party plugin names that start with "claude-".
        self.assertFalse(manifest["name"].startswith(("claude-", "anthropic")), manifest["name"])
        # A version in both files is a trap: plugin.json wins silently.
        self.assertNotIn("version", entry)
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")


class Copy(unittest.TestCase):
    def test_the_plugin_holds_an_up_to_date_copy_of_every_runtime_file(self):
        self.assertEqual(build_plugin.check(), [])

    def test_the_drift_check_sees_each_kind_of_drift(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        for name in build_plugin.RUNTIME:
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, root / name, ignore=shutil.ignore_patterns("*.local.txt", "__pycache__"))
            else:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, root / name)
        plugin = tmp / "plugin"
        shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.assertEqual(build_plugin.check(root, plugin), [])
        (root / "browser_shield" / "new.py").write_text("x = 1\n")
        (plugin / "page" / "shield.js").write_text("// changed\n")
        (plugin / "lists" / "old.txt").write_text("left over\n")
        (plugin / "LICENSE").unlink()
        (plugin / "LICENSE").symlink_to(root / "LICENSE")
        (plugin / "hooks" / "hooks.json").unlink()
        found = "\n".join(build_plugin.check(root, plugin))
        for expected in ("browser_shield/new.py: missing", "page/shield.js: the plugin's copy differs",
                         "lists/old.txt: in the plugin but not", "LICENSE: a symbolic link",
                         "hooks/hooks.json: the plugin's own file is missing"):
            self.assertIn(expected, found)

    def test_personal_lists_are_never_part_of_the_copy(self):
        self.assertTrue(build_plugin.untracked("lists/extensions.local.txt"))
        self.assertNotIn("lists/extensions.local.txt", build_plugin.source_files())


if __name__ == "__main__":
    unittest.main()
