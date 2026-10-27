"""Install a tested, pinned copy of browser-shield for Claude Code's hooks and the weekly list update.

Claude Code's hooks used to run straight from this checkout, so every save was live in every session at once, and a
half-saved file crashed the hook, which Claude Code reads as a non-blocking error: the call went through unchecked.
The hooks now run from a copy of a committed commit that passed the gate, and an edit here reaches no session until
the next install.

    /usr/bin/python3 -I scripts/install.py               # gate HEAD in a copy, then point `current` at that copy
    /usr/bin/python3 -I scripts/install.py --status      # what is installed, and what the hooks and launchd run
    /usr/bin/python3 -I scripts/install.py --schedule    # load the weekly list update (a macOS LaunchAgent)
    /usr/bin/python3 -I scripts/install.py --unschedule  # unload and remove it

Layout under ~/.local/share/browser-shield ($BROWSER_SHIELD_INSTALL moves it, for tests):
    releases/<commit>/  a `git archive` of an installed commit, gated there, byte-compiled, read-only
    current             symlink to the release in use, swapped by one rename, so a hook never sees half a copy
    data/               downloaded lists, the compiled index, guard.log and session markers, kept across releases
    data/local/         personal lists: block.local.txt, allow.local.txt, extensions.local.txt
Each release's cache/ and lists/*.local.txt are symlinks into data/, so the code finds everything where it always has.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
PYTHON = "/usr/bin/python3"
HOME = Path(os.environ.get("HOME") or str(Path.home()))
INSTALL = Path(os.environ.get("BROWSER_SHIELD_INSTALL") or HOME / ".local" / "share" / "browser-shield")
SETTINGS = HOME / ".claude" / "settings.json"
# The personal lists browser_shield/lists.py reads (tests/test_install.py pins the two together).
LOCAL_LISTS = ("allow.local.txt", "block.local.txt", "extensions.local.txt")
KEEP = 3  # newest releases kept for a rollback; the one `current` names is never removed
LABEL = "local.browser-shield.update-lists"
PLIST = HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"
JOB_LOG = HOME / "Library" / "Logs" / "browser-shield-update-lists.log"
# Monday 11:00 local time. launchd runs a calendar job missed while the Mac slept when it next wakes.
WHEN = {"Weekday": 1, "Hour": 11, "Minute": 0}
# The repo's gate, run inside the copy being installed (CLAUDE.md "Gate"; tests/test_install.py pins the two).
GATE = (
    (PYTHON, "-I", "-m", "unittest", "discover", "-s", "tests", "-t", "."),
    ("node", "--test", "tests/shield.test.js"),
    ("node", "--experimental-strip-types", "--no-warnings", "--test", "tests/autorun.test.mjs"),
    ("claude", "plugin", "test", "."),
)
GATE_TIMEOUT = 900


class InstallError(Exception):
    """A step refused; nothing that sessions run has changed."""


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if proc.returncode != 0:
        raise InstallError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def clean_commit(repo: Path = REPO) -> str:
    """HEAD's full id, if nothing is uncommitted: the install copies a commit, so the gate must see only that."""
    dirty = git(repo, "status", "--porcelain").decode(errors="replace").strip()
    if dirty:
        raise InstallError("the checkout has uncommitted changes; commit them first (the install copies a commit "
                           "and gates exactly what it copies):\n" + dirty)
    return git(repo, "rev-parse", "HEAD").decode().strip()


def archive(repo: Path, commit: str) -> bytes:
    return git(repo, "archive", "--format=tar", commit)


def extract(tar_bytes: bytes, dest: Path) -> None:
    dest.mkdir(parents=True)
    proc = subprocess.run(["/usr/bin/tar", "-x", "-f", "-", "-C", str(dest)], input=tar_bytes, capture_output=True)
    if proc.returncode != 0:
        raise InstallError(f"tar failed: {proc.stderr.decode(errors='replace').strip()}")


def archive_hashes(tar_bytes: bytes) -> dict[str, str]:
    """sha256 of every regular file in a tar, by its path."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                fh = tar.extractfile(member)
                out[member.name] = hashlib.sha256(fh.read() if fh else b"").hexdigest()
    return out


def verify_tree(tree: Path, expected: dict[str, str]) -> None:
    """The tree holds exactly the expected files with the expected bytes; byte-code caches are allowed."""
    seen = {}
    for root, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(tree).as_posix()
            if path.is_symlink():
                raise InstallError(f"unexpected symlink in the copy: {rel}")
            seen[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    extra = sorted(set(seen) - set(expected))
    missing = sorted(set(expected) - set(seen))
    changed = sorted(p for p in set(seen) & set(expected) if seen[p] != expected[p])
    if extra or missing or changed:
        raise InstallError("the copy is not the commit: " + "; ".join(
            f"{label} {', '.join(items[:5])}" for label, items in
            (("extra", extra), ("missing", missing), ("changed", changed)) if items))


def claude_binary() -> str:
    found = shutil.which("claude") or str(HOME / ".local" / "bin" / "claude")
    if not os.access(found, os.X_OK):
        raise InstallError("the gate needs the `claude` CLI (for `claude plugin test`), and it was not found")
    return found


def run_gate(tree: Path) -> None:
    """Every gate command, inside the copy; any failure refuses the install with the command's tail."""
    for cmd in GATE:
        argv = [claude_binary(), *cmd[1:]] if cmd[0] == "claude" else list(cmd)
        print(f"gate   {' '.join(cmd)}", flush=True)
        try:
            proc = subprocess.run(argv, cwd=tree, capture_output=True, text=True, timeout=GATE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"gate step `{' '.join(cmd)}` could not run: {exc}")
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
            raise InstallError(f"gate step `{' '.join(cmd)}` failed (exit {proc.returncode}):\n{tail}")


def compile_tree(tree: Path) -> None:
    """Byte-compile with the hook's own interpreter, so the read-only copy never needs to write a cache."""
    proc = subprocess.run([PYTHON, "-I", "-m", "compileall", "-q", str(tree)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise InstallError(f"compileall failed: {(proc.stdout + proc.stderr).strip()[-500:]}")


def link_data(release: Path, data: Path) -> None:
    (release / "cache").symlink_to(data, target_is_directory=True)
    for name in LOCAL_LISTS:
        (release / "lists" / name).symlink_to(data / "local" / name)


def set_writable(tree: Path, writable: bool) -> None:
    """Files 0644/0444 and folders 0755/0555; symlinks are skipped (chmod would follow them into data/)."""
    for root, dirs, files in os.walk(tree, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                os.chmod(path, 0o644 if writable else 0o444)
        for name in dirs:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                os.chmod(path, 0o755 if writable else 0o555)
    os.chmod(tree, 0o755 if writable else 0o555)


def remove_tree(tree: Path) -> None:
    """Rename the folder to a dot-name first: a removal cut short leaves a leftover prune() sweeps, never a release
    with its marker but half its files, which a later install of that commit would reuse."""
    if not tree.name.startswith("."):
        doomed = tree.with_name(f".{tree.name}-removing-{os.getpid()}")
        os.rename(tree, doomed)
        tree = doomed
    os.chmod(tree, 0o755)
    set_writable(tree, True)
    shutil.rmtree(tree)


def ensure_data(data: Path, checkout: Path = REPO) -> list[str]:
    """Create data/ and, the first time, copy the checkout's lists, index and log into it. Never overwrites: a
    file data/ already has may hold what only it gained (a newer download, the live log)."""
    notes = []
    (data / "local").mkdir(parents=True, exist_ok=True)
    cache = checkout / "cache"
    for name in ("blocklist.sqlite", "guard.log"):
        if not (data / name).exists() and (cache / name).is_file():
            shutil.copy2(cache / name, data / name)
            notes.append(f"copied cache/{name} into {data}")
    if not (data / "lists").exists() and (cache / "lists").is_dir():
        shutil.copytree(cache / "lists", data / "lists")
        notes.append(f"copied cache/lists/ into {data}")
    for name in LOCAL_LISTS:
        src, dst = checkout / "lists" / name, data / "local" / name
        if not src.is_file():
            continue
        if not dst.exists():
            shutil.copy2(src, dst)
            notes.append(f"copied lists/{name} to {dst} (the installed copy reads it there)")
        elif src.read_bytes() != dst.read_bytes():
            notes.append(f"WARNING lists/{name} differs from {dst}; the installed copy reads {dst}")
    return notes


def run_hook(release: Path, event: dict, state_dir: Path) -> dict:
    """The release's guard on one event, run as Claude Code runs it (a bare environment, the event on stdin)."""
    env = {"HOME": str(HOME), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
           "BROWSER_SHIELD_STATE_DIR": str(state_dir)}
    proc = subprocess.run([PYTHON, "-I", str(release / "hooks" / "guard.py")], input=json.dumps(event).encode(),
                          capture_output=True, timeout=30, env=env)
    if proc.returncode != 0:
        raise InstallError(f"the copy's hook exited {proc.returncode}: {proc.stderr.decode(errors='replace')[-500:]}")
    try:
        return json.loads(proc.stdout.decode() or "{}")
    except ValueError:
        raise InstallError(f"the copy's hook printed something other than JSON: {proc.stdout[:200]!r}")


def first_ad_host(release: Path) -> str:
    for line in (release / "lists" / "ad-hosts.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            return line
    raise InstallError("lists/ad-hosts.txt names no host")


def smoke(release: Path, data: Path) -> list[str]:
    """Refuse a copy whose hook does not deny an ad host, does not stay silent on a plain `ls`, or reads an index
    other than data's. Run before `current` moves, so a bad copy never reaches a session."""
    notes = []
    with tempfile.TemporaryDirectory() as tmp:
        base = {"hook_event_name": "PreToolUse", "session_id": "install-smoke", "cwd": tmp}
        host = first_ad_host(release)
        out = run_hook(release, {**base, "tool_name": "mcp__Claude_Browser__navigate",
                                 "tool_input": {"url": f"https://{host}/"}}, Path(tmp))
        if out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
            raise InstallError(f"the copy's hook did not deny the ad host {host}: {json.dumps(out)[:300]}")
        if run_hook(release, {**base, "tool_name": "Bash", "tool_input": {"command": "ls"}}, Path(tmp)):
            raise InstallError("the copy's hook did not let a plain `ls` through silently")
        out = run_hook(release, {**base, "tool_name": "mcp__Claude_Browser__navigate",
                                 "tool_input": {"url": "https://example.com/"}}, Path(tmp))
        if out.get("hookSpecificOutput", {}).get("permissionDecision"):
            raise InstallError(f"the copy's hook did not let https://example.com/ through: {json.dumps(out)[:300]}")
        message = out.get("systemMessage", "")
        if (data / "blocklist.sqlite").exists():
            if "not installed" in message or "cannot be read" in message:
                raise InstallError(f"the copy does not read {data / 'blocklist.sqlite'}: {message}")
        elif "not installed" not in message:
            raise InstallError(f"no index in {data}, yet the copy did not say the lists are missing: {message!r}")
        else:
            notes.append("no downloaded lists yet: the hooks check the curated lists only until the first update "
                         "(--schedule runs it weekly)")
    return notes


def swap(install: Path, release: Path) -> None:
    """Point install/current at release with one rename: a hook starting meanwhile sees the old copy or the new."""
    tmp = install / f".current-{os.getpid()}"
    if os.path.lexists(tmp):
        tmp.unlink()
    tmp.symlink_to(Path("releases") / release.name)
    os.replace(tmp, install / "current")
    os.utime(release, follow_symlinks=False)  # newest first for prune(), also when an older release comes back


def prune(install: Path, keep: int = KEEP) -> list[str]:
    releases = install / "releases"
    current = (install / "current").resolve() if os.path.lexists(install / "current") else None
    found = sorted((p for p in releases.iterdir() if p.is_dir() and not p.is_symlink()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in found:
        if old.name.startswith("."):  # a staging folder an interrupted install left
            remove_tree(old)
            removed.append(old.name)
    found = [p for p in found if not p.name.startswith(".")]
    for old in found[keep:]:
        if current is not None and old.resolve() == current:
            continue
        remove_tree(old)
        removed.append(old.name)
    return removed


def hook_commands(settings: Optional[Path] = None) -> list[tuple[str, str]]:
    """(event, command) for every hook in settings.json that runs a browser-shield guard.py."""
    return [(event, command) for event, command, _, _ in hook_entries(settings)]


def hook_entries(settings: Optional[Path] = None, root: Optional[Path] = None) -> list[tuple[str, str, str, str]]:
    """(event, command, matcher, onFailure) for every hook in settings.json that runs a browser-shield guard.py, or
    the guard installed under root (an install folder named otherwise)."""
    try:
        config = json.loads((settings or SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for event, groups in (config.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                command = str(hook.get("command", ""))
                if "guard.py" in command and ("browser-shield" in command or (root and runs_installed(command, root))):
                    out.append((event, command, str(group.get("matcher", "")), str(hook.get("onFailure", ""))))
    return out


def runs_installed(command: str, install: Path) -> bool:
    expanded = command.replace("$HOME", str(HOME)).replace("${HOME}", str(HOME)).replace("~/", f"{HOME}/")
    return f"{install / 'current' / 'hooks' / 'guard.py'}" in expanded


def lock(install: Path):
    install.mkdir(parents=True, exist_ok=True)
    fh = open(install / ".lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise InstallError("another install is running (it holds .lock)")
    return fh


def install(repo: Path = REPO, root: Path = INSTALL) -> int:
    commit = clean_commit(repo)
    releases, data = root / "releases", root / "data"
    release = releases / commit[:12]
    releases.mkdir(parents=True, exist_ok=True)
    for note in ensure_data(data, repo):
        print(f"data   {note}")
    if (release / ".installed").is_file():
        print(f"reuse  {release} (gated when it was installed)")
    else:
        tar_bytes = archive(repo, commit)
        staging = releases / f".{commit[:12]}-{os.getpid()}"
        try:
            extract(tar_bytes, staging)
            run_gate(staging)
            verify_tree(staging, archive_hashes(tar_bytes))  # the gate left the copy as the commit made it
            compile_tree(staging)
            link_data(staging, data)
            (staging / ".installed").write_text(json.dumps(
                {"commit": commit, "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n", encoding="utf-8")
            set_writable(staging, False)
            if os.path.lexists(release):
                remove_tree(release)  # a release folder without its marker is an interrupted install's
            os.rename(staging, release)
        except BaseException:
            if os.path.lexists(staging):
                remove_tree(staging)
            raise
    for note in smoke(release, data):
        print(f"note   {note}")
    swap(root, release)
    print(f"ok     current -> releases/{release.name} ({commit[:12]})")
    for name in prune(root):
        print(f"pruned releases/{name}")
    report_hooks(root)
    return 0


def guards_calls(matcher: str) -> bool:
    """The entry's matcher covers a shell call and a browser navigation (an empty matcher matches every tool)."""
    try:
        return all(re.search(matcher, tool) for tool in ("Bash", "mcp__Claude_Browser__navigate")) if matcher else True
    except re.error:
        return False


def report_hooks(root: Path) -> bool:
    """True only when a PreToolUse entry runs the installed guard on Bash and browser calls, refusing a call when it
    fails to run, and every browser-shield entry runs the installed copy."""
    entries = hook_entries(None, root)
    good = True
    for event, command, matcher, on_failure in entries:
        ok = runs_installed(command, root)
        good &= ok
        print(("ok     " if ok else "WARN   ") + f"{event} runs {command}"
              + ("" if ok else f" (not the installed copy: point it at {root / 'current' / 'hooks' / 'guard.py'})"))
    pre = [e for e in entries if e[0] == "PreToolUse" and runs_installed(e[1], root) and guards_calls(e[2])]
    if not [e for e in pre if e[3] == "block"]:
        print(f"WARN   no PreToolUse entry in {SETTINGS} runs the installed guard on Bash and browser calls: nothing "
              "is refused" if not pre else 'WARN   the PreToolUse entry lacks "onFailure": "block": a guard that '
              "fails to run lets the call through")
        return False
    return good


def plist(root: Path = INSTALL) -> dict:
    """The LaunchAgent: the installed copy's update_lists.py, weekly, in a pinned bare environment."""
    script = root / "current" / "scripts" / "update_lists.py"
    stamp = 'printf "\\n--- %s\\n" "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"; exec /usr/bin/python3 -I "$0"'
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", "-c", stamp, str(script)],
        "StartCalendarInterval": dict(WHEN),
        "EnvironmentVariables": {"HOME": str(HOME), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "WorkingDirectory": str(root),
        "StandardOutPath": str(JOB_LOG),
        "StandardErrorPath": str(JOB_LOG),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def domain() -> str:
    return f"gui/{os.getuid()}"


def loaded() -> bool:
    return launchctl("print", f"{domain()}/{LABEL}").returncode == 0


def unload() -> None:
    """bootout returns before the job is gone; wait until it is, or the next bootstrap fails (5: I/O error)."""
    if not loaded():
        return
    launchctl("bootout", f"{domain()}/{LABEL}")
    deadline = time.monotonic() + 15
    while loaded():
        if time.monotonic() > deadline:
            raise InstallError(f"launchd still holds {LABEL} 15 s after bootout")
        time.sleep(0.2)


def schedule(root: Path = INSTALL) -> int:
    if not os.path.lexists(root / "current"):
        raise InstallError("nothing is installed yet: run scripts/install.py first")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = PLIST.with_suffix(".plist.new")
    tmp.write_bytes(plistlib.dumps(plist(root)))
    lint = subprocess.run(["/usr/bin/plutil", "-lint", str(tmp)], capture_output=True, text=True)
    if lint.returncode != 0:
        tmp.unlink()
        raise InstallError(f"plutil refused the job file: {lint.stdout.strip()} {lint.stderr.strip()}")
    unload()
    os.replace(tmp, PLIST)
    boot = launchctl("bootstrap", domain(), str(PLIST))
    if boot.returncode != 0 or not loaded():
        raise InstallError(f"launchctl bootstrap failed: {boot.stderr.strip() or boot.stdout.strip()}")
    print(f"ok     {LABEL} loaded: every Monday 11:00, log {JOB_LOG}")
    return 0


def unschedule() -> int:
    unload()
    if PLIST.exists():
        PLIST.unlink()
    print(f"ok     {LABEL} unloaded and removed")
    return 0


def status(root: Path = INSTALL) -> int:
    current = root / "current"
    if os.path.lexists(current):
        target = os.readlink(current)
        marker = current / ".installed"
        info = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        print(f"current -> {target} (installed {info.get('installed', '?')})")
    else:
        print("current: nothing installed")
    releases = root / "releases"
    if releases.is_dir():
        print("releases: " + ", ".join(sorted(p.name for p in releases.iterdir() if not p.name.startswith("."))))
    index = root / "data" / "blocklist.sqlite"
    if index.exists():
        days = (time.time() - index.stat().st_mtime) / 86400
        print(f"lists: {days:.1f} days since the oldest source last downloaded ({index})")
    else:
        print("lists: not downloaded yet")
    print("local lists: " + (", ".join(n for n in LOCAL_LISTS if (root / "data" / "local" / n).is_file()) or "none"))
    hooks_ok = report_hooks(root)
    job = launchctl("print", f"{domain()}/{LABEL}")
    if job.returncode == 0:
        last = [line.strip() for line in job.stdout.splitlines() if "last exit code" in line]
        print(f"weekly update: loaded ({last[0] if last else 'not run yet'}), log {JOB_LOG}")
    else:
        print("weekly update: not loaded (--schedule loads it)")
    return 0 if hooks_ok else 1


def main(argv: list[str]) -> int:
    commands = {"": install, "--status": status, "--schedule": schedule, "--unschedule": unschedule}
    if len(argv) > 1 or (argv and argv[0] not in commands):
        print(__doc__.split("\n\n")[1])
        return 2
    command = argv[0] if argv else ""
    try:
        if command == "--status":
            return status()
        held = lock(INSTALL)
        try:
            return commands[command]()
        finally:
            held.close()
    except InstallError as exc:
        print(f"REFUSED {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
