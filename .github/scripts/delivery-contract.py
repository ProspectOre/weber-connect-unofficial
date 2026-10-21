#!/usr/bin/env python3
"""Fail when a repository's workflows drift from the delivery contract.

Usage: delivery-contract.py [REPO_ROOT]
Reads REPO_ROOT/.github/delivery-contract.json:
  {"workflows": {"ci.yml": "ci", "release.yml": "dispatch", "nightly.yml": "nightly"},
   "hosted": false, "dependabot": true}
Kinds: ci (pull_request + push + concurrency), dispatch (workflow_dispatch /
workflow_call / repository_dispatch only), nightly (one schedule, at most one file).
Contract: wiki workflows/delivery-contract.md. Stdlib only; no YAML parser needed.
"""
import json
import re
import sys
from pathlib import Path

KINDS = {"ci", "dispatch", "nightly"}
DISPATCH_EVENTS = {"workflow_dispatch", "workflow_call", "repository_dispatch"}
HOSTED = re.compile(r"runs-on:\s*\[?\s*['\"]?(ubuntu|macos|windows)-")


def triggers(text):
    """Return the set of event names under the top-level `on:` key."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(?:on|'on'|\"on\"):\s*(.*)$", line)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            return {t.strip(" '\"") for t in inline.strip("[]").split(",") if t.strip()}
        found = set()
        for nxt in lines[i + 1:]:
            if nxt.strip() == "" or nxt.lstrip().startswith("#"):
                continue
            if not nxt.startswith(" "):
                break
            k = re.match(r"^  ([A-Za-z_]+):", nxt)
            if k:
                found.add(k.group(1))
        return found
    return set()


def check(root):
    root = Path(root)
    gh = root / ".github"
    errors = []
    try:
        contract = json.loads((gh / "delivery-contract.json").read_text())
    except (OSError, ValueError) as exc:
        return [f".github/delivery-contract.json unreadable: {exc}"]
    allowed = contract.get("workflows", {})
    for name, kind in allowed.items():
        if kind not in KINDS:
            errors.append(f"{name}: unknown kind {kind!r}")
    present = sorted(p.name for p in (gh / "workflows").glob("*.y*ml")) if (gh / "workflows").is_dir() else []
    for name in present:
        if name not in allowed:
            errors.append(f"{name}: not in delivery-contract.json")
    for name in allowed:
        if name not in present:
            errors.append(f"{name}: listed but missing")
    nightly = 0
    for name in present:
        kind = allowed.get(name)
        if kind is None:
            continue
        text = (gh / "workflows" / name).read_text()
        on = triggers(text)
        if "pull_request_target" in on or "pull_request_target" in text:
            errors.append(f"{name}: pull_request_target is forbidden")
        if not contract.get("hosted") and HOSTED.search(text):
            errors.append(f"{name}: hosted runner in a private repository")
        if kind == "ci":
            if not {"pull_request", "push"} <= on:
                errors.append(f"{name}: ci must run on pull_request and push")
            if not re.search(r"^concurrency:", text, re.M):
                errors.append(f"{name}: ci needs top-level concurrency")
            if "schedule" in on:
                errors.append(f"{name}: ci must not be scheduled")
        elif kind == "dispatch":
            extra = on - DISPATCH_EVENTS
            if extra:
                errors.append(f"{name}: dispatch workflow also runs on {sorted(extra)}")
        elif kind == "nightly":
            nightly += 1
            if "schedule" not in on:
                errors.append(f"{name}: nightly needs a schedule")
            if not {"schedule", "workflow_dispatch"} >= on:
                errors.append(f"{name}: nightly runs on more than schedule/workflow_dispatch")
    if nightly > 1:
        errors.append("more than one nightly workflow")
    dep = gh / "dependabot.yml"
    if contract.get("dependabot", True) and dep.exists():
        text = dep.read_text()
        intervals = re.findall(r"interval:\s*['\"]?([a-z]+)", text)
        if any(i != "monthly" for i in intervals):
            errors.append("dependabot.yml: every interval must be monthly")
        blocks = len(re.findall(r"^\s*-\s*package-ecosystem:", text, re.M))
        if blocks and len(re.findall(r"^\s*groups:", text, re.M)) < blocks:
            errors.append("dependabot.yml: every ecosystem needs a groups: entry")
        limits = re.findall(r"open-pull-requests-limit:\s*(\d+)", text)
        if any(int(x) > 2 for x in limits):
            errors.append("dependabot.yml: open-pull-requests-limit must be <= 2")
    return errors


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    errors = check(root)
    for e in errors:
        print(f"delivery-contract: {e}", file=sys.stderr)
    print("delivery-contract: ok" if not errors else f"delivery-contract: {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
