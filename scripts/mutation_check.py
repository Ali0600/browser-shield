"""Prove the tests can fail: apply each deliberate bug in tests/mutants.json to a throwaway copy of the repo, run the
suites there, and require each one to turn a NAMED test red. Never touches this checkout (the live hook runs from it).

    /usr/bin/python3 -I scripts/mutation_check.py [mutant-id ...]

A mutant passes ("caught") only if: its pattern occurs exactly once, the file changed, the suites still ran the
same number of tests as the clean baseline (a mutant that stops a module loading measures nothing), and a test whose
name contains `expect` went red. Exit 1 if any mutant survived or was invalid.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = "/usr/bin/python3"
TIMEOUT = 180


# The every-code-point sweep and the UTS 46 table check take ~20 s a run; a mutant outside the URL parser cannot
# change their verdict, so they are skipped for it (tests/test_urls.py honours this variable; the gate never sets it).
SWEEP_FILES = {"browser_shield/urls.py", "browser_shield/uts46.txt", "scripts/gen_uts46.js"}


def run_suites(repo: Path, sweep: bool = True) -> tuple[int, int, set[str]]:
    """(tests run, tests failed, names of failed tests) for both suites in repo."""
    env = dict(os.environ)
    if not sweep:
        env["BROWSER_SHIELD_SKIP_SWEEP"] = "1"
    py = subprocess.run([PYTHON, "-I", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=repo,
                        capture_output=True, text=True, timeout=TIMEOUT, env=env)
    js = subprocess.run(["node", "--test", "tests/shield.test.js"], cwd=repo, capture_output=True, text=True,
                        timeout=TIMEOUT)
    failed: set[str] = set()
    ran = 0
    for line in py.stderr.splitlines():
        m = re.match(r"^(test\w+) \(([\w.]+)\).*\.\.\. (ok|FAIL|ERROR)", line)
        if m:
            ran += 1
            if m.group(3) != "ok":
                failed.add(f"{m.group(2)}.{m.group(1)}")
        m = re.match(r"^(FAIL|ERROR): (test\w+) \(([\w.]+)\)", line)
        if m:
            failed.add(f"{m.group(3)}.{m.group(2)}")
    m = re.search(r"Ran (\d+) tests?", py.stderr)
    ran = int(m.group(1)) if m else ran
    for line in js.stdout.splitlines():
        m = re.match(r"^(not ok|ok) \d+ - (.+)$", line)
        if m:
            ran += 1
            if m.group(1) == "not ok":
                failed.add(f"shield.js: {m.group(2)}")
    if py.returncode not in (0, 1) or js.returncode not in (0, 1):
        failed.add(f"<runner exit py={py.returncode} js={js.returncode}>")
    return ran, len(failed), failed


def main(argv: list[str]) -> int:
    mutants = json.loads((ROOT / "tests" / "mutants.json").read_text(encoding="utf-8"))
    if argv:
        mutants = [m for m in mutants if m["id"] in argv]
    with tempfile.TemporaryDirectory(prefix="browser-shield-mutants-") as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("cache", ".git", "__pycache__"))
        base_ran, base_failed, names = run_suites(repo)
        print(f"baseline: {base_ran} tests, {base_failed} failing")
        if base_failed or base_ran < 60:
            print("baseline is not green (or too small); refusing to judge mutants:", sorted(names))
            return 1
        bad = 0
        for m in mutants:
            target = repo / m["file"]
            original = target.read_bytes()
            text = original.decode("utf-8")
            count = text.count(m["old"])
            if count != 1:
                print(f"INVALID  {m['id']}: pattern found {count} times in {m['file']}")
                bad += 1
                continue
            target.write_text(text.replace(m["old"], m["new"]), encoding="utf-8")
            try:
                if target.read_bytes() == original:
                    print(f"INVALID  {m['id']}: the edit changed nothing")
                    bad += 1
                    continue
                ran, failed, names = run_suites(repo, sweep=m["file"] in SWEEP_FILES)
            finally:
                target.write_bytes(original)
            assert target.read_bytes() == original, f"restore of {m['file']} failed"
            hit = sorted(n for n in names if m["expect"] in n)
            if ran != base_ran:
                print(f"INVALID  {m['id']}: ran {ran} tests, baseline {base_ran} (did a module stop loading?)")
                bad += 1
            elif not hit:
                print(f"SURVIVED {m['id']}: red were {sorted(names) or 'none'}; expected one matching {m['expect']!r}")
                bad += 1
            else:
                print(f"caught   {m['id']}: {len(names)} red, including {hit[0]}")
        print(f"{len(mutants) - bad}/{len(mutants)} mutants caught")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
