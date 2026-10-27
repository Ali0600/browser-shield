"""scripts/install.py on throwaway repos and folders: nothing here touches ~/.local/share, settings.json or launchd."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import install  # noqa: E402

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]

# A stand-in guard for the smoke check: denies one ad host, silent on Bash, and (for a URL) says the lists are
# missing only when FAKE_MISSING is set in its source.
FAKE_GUARD = """import json, sys
event = json.loads(sys.stdin.read())
tool, url = event.get("tool_name"), (event.get("tool_input") or {}).get("url", "")
if tool == "Bash":
    print("{}")
elif "ads.example" in url:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}))
elif MISSING:
    print(json.dumps({"systemMessage": "the malware and phishing lists are not installed"}))
else:
    print("{}")
"""


def fake_release(tmp: Path, guard: str = FAKE_GUARD, missing: bool = False) -> Path:
    release = tmp / "release"
    (release / "hooks").mkdir(parents=True)
    (release / "lists").mkdir()
    (release / "hooks" / "guard.py").write_text(f"MISSING = {missing}\n" + guard)
    (release / "lists" / "ad-hosts.txt").write_text("# curated\nads.example\n")
    return release


def make_repo(tmp: Path, files: dict) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(GIT + ["init", "-q", str(repo)], check=True)
    subprocess.run(GIT + ["-C", str(repo), "add", "-A"], check=True)
    subprocess.run(GIT + ["-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    return repo


def quietly(fn, *args, **kwargs):
    with redirect_stdout(StringIO()):
        return fn(*args, **kwargs)


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        for root, dirs, _ in os.walk(self.tmp):
            for name in dirs:
                path = os.path.join(root, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Pins(unittest.TestCase):
    def test_gate_matches_claude_md(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        block = re.search(r"## Gate.*?```bash\n(.*?)\n```", text, re.S)
        self.assertIsNotNone(block, "CLAUDE.md has no Gate block")
        self.assertEqual(block.group(1).strip(), " && ".join(" ".join(cmd) for cmd in install.GATE))

    def test_local_lists_match_lists_py(self):
        names = set(re.findall(r'"([a-z]+\.local\.txt)"', (ROOT / "browser_shield" / "lists.py").read_text()))
        self.assertTrue(names)
        self.assertEqual(names, set(install.LOCAL_LISTS))


class Copy(Temp):
    def test_dirty_checkout_refused(self):
        repo = make_repo(self.tmp, {"a.txt": "a\n"})
        (repo / "new.txt").write_text("untracked\n")
        with self.assertRaisesRegex(install.InstallError, "uncommitted"):
            install.clean_commit(repo)
        (repo / "new.txt").unlink()
        (repo / "a.txt").write_text("changed\n")
        with self.assertRaisesRegex(install.InstallError, "uncommitted"):
            install.clean_commit(repo)

    def test_clean_checkout_gives_head(self):
        repo = make_repo(self.tmp, {"a.txt": "a\n"})
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(install.clean_commit(repo), head.strip())

    def extracted(self, name: str):
        (self.tmp / name).mkdir()
        repo = make_repo(self.tmp / name, {"a.txt": "a\n", "pkg/b.py": "B = 1\n"})
        tar = install.archive(repo, "HEAD")
        dest = self.tmp / name / "copy"
        install.extract(tar, dest)
        return dest, install.archive_hashes(tar)

    def test_copy_is_the_commit(self):
        dest, hashes = self.extracted("ok")
        self.assertEqual(set(hashes), {"a.txt", "pkg/b.py"})
        (dest / "pkg" / "__pycache__").mkdir()
        (dest / "pkg" / "__pycache__" / "b.cpython-39.pyc").write_bytes(b"\0")
        install.verify_tree(dest, hashes)

    def test_copy_with_extra_or_changed_file_refused(self):
        dest, hashes = self.extracted("extra")
        (dest / "left-behind.txt").write_text("x")
        with self.assertRaisesRegex(install.InstallError, "extra left-behind.txt"):
            install.verify_tree(dest, hashes)
        dest, hashes = self.extracted("changed")
        (dest / "a.txt").write_text("A\n")
        with self.assertRaisesRegex(install.InstallError, "changed a.txt"):
            install.verify_tree(dest, hashes)
        dest, hashes = self.extracted("missing")
        (dest / "a.txt").unlink()
        with self.assertRaisesRegex(install.InstallError, "missing a.txt"):
            install.verify_tree(dest, hashes)


class Layout(Temp):
    def test_link_data(self):
        release, data = fake_release(self.tmp), self.tmp / "data"
        (data / "local").mkdir(parents=True)
        install.link_data(release, data)
        self.assertEqual((release / "cache").resolve(), data.resolve())
        (release / "cache" / "guard.log").write_text("written through the link\n")
        self.assertEqual((data / "guard.log").read_text(), "written through the link\n")
        for name in install.LOCAL_LISTS:
            self.assertEqual(os.readlink(release / "lists" / name), str(data / "local" / name))

    def test_set_writable_skips_symlinks(self):
        release, data = fake_release(self.tmp), self.tmp / "data"
        (data / "local").mkdir(parents=True)
        install.link_data(release, data)
        install.set_writable(release, False)
        self.assertEqual(stat.S_IMODE((release / "hooks" / "guard.py").stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(os.stat(release / "hooks").st_mode), 0o555)
        self.assertEqual(stat.S_IMODE(data.stat().st_mode) & 0o200, 0o200, "chmod followed cache/ into data/")
        install.remove_tree(release)
        self.assertFalse(release.exists())
        self.assertTrue(data.is_dir())

    def test_ensure_data_copies_once_never_overwrites(self):
        checkout, data = self.tmp / "checkout", self.tmp / "data"
        (checkout / "cache" / "lists").mkdir(parents=True)
        (checkout / "lists").mkdir()
        (checkout / "cache" / "guard.log").write_text("old\n")
        (checkout / "cache" / "lists" / "state.json").write_text("{}\n")
        (checkout / "lists" / "extensions.local.txt").write_text("a\n")
        notes = install.ensure_data(data, checkout)
        self.assertEqual((data / "guard.log").read_text(), "old\n")
        self.assertTrue((data / "lists" / "state.json").is_file())
        self.assertEqual((data / "local" / "extensions.local.txt").read_text(), "a\n")
        self.assertEqual(len(notes), 3)
        (checkout / "cache" / "guard.log").write_text("newer\n")
        (checkout / "lists" / "extensions.local.txt").write_text("b\n")
        notes = install.ensure_data(data, checkout)
        self.assertEqual((data / "guard.log").read_text(), "old\n")
        self.assertEqual((data / "local" / "extensions.local.txt").read_text(), "a\n")
        self.assertTrue(any(n.startswith("WARNING lists/extensions.local.txt differs") for n in notes), notes)


class Swap(Temp):
    def releases(self, *names: str) -> Path:
        root = self.tmp / "install"
        for i, name in enumerate(names):
            (root / "releases" / name).mkdir(parents=True)
            os.utime(root / "releases" / name, (1_000_000 + i, 1_000_000 + i))
        return root

    def test_swap_points_current_by_one_rename(self):
        root = self.releases("a", "b")
        install.swap(root, root / "releases" / "a")
        with mock.patch.object(install.os, "replace", wraps=os.replace) as replace:
            install.swap(root, root / "releases" / "b")
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(Path(replace.call_args[0][1]), root / "current")
        self.assertEqual(os.readlink(root / "current"), "releases/b")
        self.assertEqual([p.name for p in root.iterdir() if p.name.startswith(".current")], [])

    def test_swap_refuses_a_real_folder(self):
        root = self.releases("a")
        (root / "current").mkdir()
        with self.assertRaises(OSError):
            install.swap(root, root / "releases" / "a")

    def test_prune_keeps_newest_and_current(self):
        root = self.releases("a", "b", "c", "d", "e")
        (root / "current").symlink_to("releases/a")
        install.set_writable(root / "releases" / "b", False)
        (root / "releases" / ".f-123").mkdir()
        removed = install.prune(root, keep=3)
        self.assertEqual(sorted(removed), [".f-123", "b"])
        self.assertEqual(sorted(p.name for p in (root / "releases").iterdir()), ["a", "c", "d", "e"])


class Smoke(Temp):
    def test_smoke_passes_on_this_checkout(self):
        quietly(install.smoke, ROOT, ROOT / "cache")

    def test_smoke_refuses_a_hook_that_allows_everything(self):
        release = fake_release(self.tmp, guard="print('{}')\n")
        with self.assertRaisesRegex(install.InstallError, "did not deny the ad host ads.example"):
            install.smoke(release, self.tmp / "data")

    def test_smoke_refuses_a_hook_that_crashes(self):
        release = fake_release(self.tmp, guard="raise SystemExit(3)\n")
        with self.assertRaisesRegex(install.InstallError, "exited 3"):
            install.smoke(release, self.tmp / "data")

    def test_smoke_refuses_a_copy_reading_another_index(self):
        release, data = fake_release(self.tmp, missing=True), self.tmp / "data"
        data.mkdir()
        (data / "blocklist.sqlite").write_bytes(b"")
        with self.assertRaisesRegex(install.InstallError, "does not read"):
            install.smoke(release, data)

    def test_smoke_notes_when_no_lists_are_downloaded(self):
        notes = install.smoke(fake_release(self.tmp, missing=True), self.tmp / "data")
        self.assertTrue(any("no downloaded lists" in n for n in notes), notes)


class Settings(Temp):
    def test_hook_commands_and_runs_installed(self):
        settings = self.tmp / "settings.json"
        old = "/usr/bin/python3 -I $HOME/projects/automation/browser-shield/hooks/guard.py"
        new = "/usr/bin/python3 -I $HOME/.local/share/browser-shield/current/hooks/guard.py"
        settings.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"matcher": "^Bash$", "hooks": [{"type": "command", "command": old}]},
                           {"matcher": "^Edit$", "hooks": [{"type": "command", "command": "other-hook"}]}],
            "PostToolUse": [{"matcher": "^WebFetch$", "hooks": [{"type": "command", "command": new}]}]}}))
        self.assertEqual(install.hook_commands(settings), [("PreToolUse", old), ("PostToolUse", new)])
        root = install.HOME / ".local" / "share" / "browser-shield"
        self.assertFalse(install.runs_installed(old, root))
        self.assertTrue(install.runs_installed(new, root))
        self.assertEqual(install.hook_commands(self.tmp / "absent.json"), [])


class Job(Temp):
    def test_plist_runs_the_installed_copy_in_a_pinned_env(self):
        job = install.plist(Path("/opt/bs"))
        self.assertEqual(job["ProgramArguments"][:2], ["/bin/sh", "-c"])
        self.assertIn('exec /usr/bin/python3 -I "$0"', job["ProgramArguments"][2])
        self.assertEqual(job["ProgramArguments"][3], "/opt/bs/current/scripts/update_lists.py")
        env = job["EnvironmentVariables"]
        self.assertTrue(env["HOME"].startswith("/"))
        self.assertEqual((env["LANG"], env["LC_ALL"]), ("en_US.UTF-8", "en_US.UTF-8"))
        self.assertEqual(set(job["StartCalendarInterval"]), {"Weekday", "Hour", "Minute"})
        for key in ("StandardOutPath", "StandardErrorPath", "WorkingDirectory"):
            self.assertTrue(job[key].startswith("/"), key)

    @unittest.skipUnless(os.path.exists("/usr/bin/plutil"), "macOS only")
    def test_plist_lints(self):
        path = self.tmp / "job.plist"
        path.write_bytes(plistlib.dumps(install.plist(self.tmp)))
        lint = subprocess.run(["/usr/bin/plutil", "-lint", str(path)], capture_output=True, text=True)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_job_command_stamps_and_runs_the_script(self):
        script = self.tmp / "with space" / "update_lists.py"
        script.parent.mkdir()
        script.write_text("import sys; print('ran', sys.flags.isolated); sys.exit(4)\n")
        argv = install.plist(self.tmp)["ProgramArguments"][:3] + [str(script)]
        proc = subprocess.run(argv, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(proc.returncode, 4, "the job's exit code must be the script's, for launchd")
        self.assertRegex(proc.stdout, r"--- \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\nran 1\n")


class EndToEnd(Temp):
    """install() on a tiny repo, with the gate swapped for a stand-in (the real one runs on this repo itself)."""

    def setUp(self):
        super().setUp()
        # MISSING: the new data/ folder holds no index, so a copy that reads it must say the lists are missing.
        self.repo = make_repo(self.tmp, {"hooks/guard.py": "MISSING = True\n" + FAKE_GUARD,
                                         "lists/ad-hosts.txt": "ads.example\n", "scripts/x.py": "X = 1\n"})
        self.root = self.tmp / "install"
        settings = mock.patch.object(install, "SETTINGS", self.tmp / "settings.json")
        settings.start()
        self.addCleanup(settings.stop)

    def run_install(self, gate=(("/usr/bin/true",),)):
        with mock.patch.object(install, "GATE", gate):
            return quietly(install.install, self.repo, self.root)

    def test_install_then_reuse(self):
        self.assertEqual(self.run_install(), 0)
        commit = install.clean_commit(self.repo)
        release = self.root / "releases" / commit[:12]
        self.assertEqual(os.readlink(self.root / "current"), f"releases/{commit[:12]}")
        self.assertEqual(json.loads((release / ".installed").read_text())["commit"], commit)
        self.assertEqual((release / "cache").resolve(), (self.root / "data").resolve())
        self.assertTrue((release / "scripts" / "__pycache__").is_dir(), "not byte-compiled")
        self.assertEqual(stat.S_IMODE(os.stat(release / "hooks" / "guard.py").st_mode), 0o444)
        with mock.patch.object(install, "run_gate") as gate:
            self.assertEqual(self.run_install(), 0)
        gate.assert_not_called()

    def test_failed_gate_changes_nothing(self):
        with self.assertRaisesRegex(install.InstallError, "gate step `/usr/bin/false` failed"):
            self.run_install(gate=(("/usr/bin/false",),))
        self.assertFalse(os.path.lexists(self.root / "current"))
        self.assertEqual(list((self.root / "releases").iterdir()), [], "a staging folder was left behind")

    def test_gate_runs_inside_the_copy(self):
        record = self.tmp / "where.txt"
        gate = (("/bin/sh", "-c", f"pwd -P > '{record}'; test -f hooks/guard.py"),)
        self.assertEqual(self.run_install(gate=gate), 0)
        self.assertNotEqual(Path(record.read_text().strip()).resolve(), self.repo.resolve())
        self.assertIn("/releases/.", record.read_text())


if __name__ == "__main__":
    unittest.main()
