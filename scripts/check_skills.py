"""Gate: every skill is loadable and the permission set stays narrow.

A skill with broken frontmatter is not a weak skill, it is an absent one: the loader
skips it and nothing says so. Same for a name that disagrees with its filename.

Usage:
    python check_skills.py
    python check_skills.py --self-test
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN_KEYS = {"name", "description", "tools", "model", "allowed-tools"}
BARE = re.compile(r"^(Bash|Read|Write|Edit|WebFetch)?\(?\*\)?$")


def frontmatter(text):
    if not text.startswith("---\n"):
        return None, "no frontmatter block"
    end = text.find("\n---", 4)
    if end < 0:
        return None, "frontmatter block is never closed"
    out = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            return None, f"frontmatter line is not a key: {line[:40]!r}"
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out, None


def skill_problems(path, text):
    fm, err = frontmatter(text)
    if err:
        return [f"{path.name}: {err}"]
    bad = []
    for key in ("name", "description"):
        if not fm.get(key):
            bad.append(f"{path.name}: frontmatter has no {key}")
    if fm.get("name") and fm["name"] != path.stem:
        bad.append(f"{path.name}: name is {fm['name']!r}, so the file and the skill disagree")
    unknown = set(fm) - KNOWN_KEYS
    if unknown:
        bad.append(f"{path.name}: unknown frontmatter {sorted(unknown)}")
    return bad


def settings_problems(text):
    try:
        d = json.loads(text)
    except ValueError as exc:
        return [f"settings.json does not parse: {exc}"]
    allow = d.get("permissions", {}).get("allow", [])
    wide = [r for r in allow if BARE.match(r.strip())]
    if wide:
        return [f"settings.json: a permission answers every question: {wide}"]
    return []


def run():
    skills = sorted(p for p in (ROOT / "skills").glob("*.md") if not p.is_symlink())
    if not skills:
        print("FAIL no skills found, so nothing was checked")
        return 1
    problems = []
    for p in skills:
        problems += skill_problems(p, p.read_text(encoding="utf-8"))
    s = ROOT / "settings.json"
    if not s.is_file():
        problems.append("settings.json is absent")
    else:
        problems += settings_problems(s.read_text(encoding="utf-8"))
    for line in problems:
        print(f"  FAIL {line}")
    print(f"\n{len(skills)} skill(s) and settings.json, {len(problems)} problem(s)")
    return 1 if problems else 0


def self_test():
    good = "---\nname: a-skill\ndescription: does a thing\n---\nbody\n"
    checks = [
        ("a well-formed skill passes", not skill_problems(Path("a-skill.md"), good)),
        ("  control: no frontmatter is caught",
         skill_problems(Path("a-skill.md"), "body only\n")),
        ("  control: an unclosed block is caught",
         skill_problems(Path("a-skill.md"), "---\nname: a-skill\n")),
        ("  control: a missing description is caught",
         skill_problems(Path("a-skill.md"), "---\nname: a-skill\n---\n")),
        ("  control: a name that disagrees with the file is caught",
         skill_problems(Path("other.md"), good)),
        ("  control: an unknown key is caught",
         skill_problems(Path("a-skill.md"), good.replace("body", "") .replace(
             "description:", "descriptoin:"))),
        ("a narrow permission set passes",
         not settings_problems('{"permissions":{"allow":["Bash(env)"]}}')),
        ("  control: a bare wildcard is caught",
         settings_problems('{"permissions":{"allow":["Bash(*)"]}}')),
        ("  control: unparseable settings are caught", settings_problems("{not json")),
    ]
    # Each entry is already "behaved as intended": a positive check is clean, a control
    # is a non-empty problem list. So the pass condition is the value itself.
    ok = all(bool(r) for _, r in checks)
    for label, r in checks:
        print(f"  {'ok  ' if r else 'FAIL'} {label}")
    print(f"\n{'ok   ' if ok else 'FAIL '}{len(checks)} controls, both directions")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    sys.exit(self_test() if ap.parse_args().self_test else run())
