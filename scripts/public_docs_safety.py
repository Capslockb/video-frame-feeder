#!/usr/bin/env python3
"""Metadata-only safety scanner for public-facing documentation."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DOC_NAMES = {"README.md", "RESEARCH.md", "SECURITY.md", "CONTRIBUTING.md", "AGENTS.md"}
DOC_DIR_PARTS = {"docs", "doc", "website", "site", "public", "docs-site"}
FIXTURE_PARTS = {"tests", "fixtures", "public-docs"}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".html", ".htm"}
EXCLUDE_PARTS = {"i18n", "CHANGELOG.md", "sessions", "vendor", "node_modules", ".git", "__pycache__", ".pytest_cache"}
ZERO_SHA = "0" * 40

RULES = [
    ("PDS001", re.compile(r"(?i)\b(ignore|disregard|override)\b.{0,100}\b(previous|above|system|developer|policy|instruction)s?\b")),
    ("PDS002", re.compile(r"(?i)\b(reveal|print|show|exfiltrate|leak)\b.{0,100}\b(secret|token|credential|password|policy|system prompt|developer message)s?\b")),
    ("PDS003", re.compile(r"(?i)\b(approve|merge|push|deploy|purchase|transfer|delete|rotate|disable)\b.{0,100}\b(PR|pull request|repository|repo|payment|account|guard|check|policy|automation)\b")),
    ("PDS004", re.compile(r"(?i)\b(privileged command|private control|non-public guard|secret marker|trusted[- ]identity rule|mutation authorization|worker queue|controller lease|private escalation)\b")),
]
UNCERTAIN = re.compile(r"(?i)\b(maintaining model|automation agent|autonomous maintainer|repository bot)\b.{0,100}\b(must|shall|required to|always|never|use tool|run command|obey|ignore|stop when|final status)\b")
BENIGN_UNCERTAIN = re.compile(r"(?i)\b(example|sample|template|user-facing|configuration|API|worker thread|service worker|inference|event loop|model name|route|provider|guardrail|security policy|documentation)\b")
CLAUSE_BREAK = re.compile(r"(?:[.;:!?]\s+|\s+[—–]\s+)")
HUMAN_GUIDANCE = re.compile(
    r"(?i)\b(?:contributor'?s?\s+(?:PR|pull request)|merge via (?:github|the )|"
    r"so they get credit|always merge|never close a contributor)\b"
)


def default_branch() -> str:
    explicit = os.environ.get("GITHUB_BASE_REF") or os.environ.get("DEFAULT_BRANCH")
    if explicit:
        return explicit
    p = subprocess.run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode == 0 and "/" in p.stdout:
        return p.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def git_stdout(*args: str) -> str | None:
    p = subprocess.run(["git", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        return None
    value = p.stdout.strip()
    return value or None


def git_commit_exists(ref: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def diff_args() -> list[str] | None:
    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        before = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
        if before and before != ZERO_SHA:
            if git_commit_exists(before):
                return [f"{before}...HEAD"]
            # A force-push can make the event's previous revision unavailable.
            # Full-scan rather than treating a failed diff as an empty safe diff.
            return None

        branch = default_branch()
        for ref in (f"origin/{branch}", branch):
            merge_base = git_stdout("merge-base", "HEAD", ref)
            if merge_base:
                return [f"{merge_base}...HEAD"]

        return None

    base = f"origin/{default_branch()}"
    if git_stdout("merge-base", "HEAD", base):
        return [f"{base}...HEAD"]
    return None


def all_files() -> list[str]:
    return [str(path) for path in Path(".").rglob("*") if path.is_file()]


def is_public_doc(path: str, include_fixtures: bool = False) -> bool:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    parts = set(p.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if include_fixtures and FIXTURE_PARTS <= parts and p.suffix.lower() in DOC_EXTS:
        return True
    return p.name in DOC_NAMES or (p.suffix.lower() in DOC_EXTS and bool(parts & DOC_DIR_PARTS))


def changed_files() -> list[str]:
    args = diff_args()
    if args is None:
        return all_files()
    p = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRT", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        return all_files()
    return [os.fsdecode(raw_path) for raw_path in p.stdout.split(b"\0") if raw_path]


def changed_added_lines(files: list[str]) -> dict[str, set[int]] | None:
    if not files:
        return {}
    args = diff_args()
    if args is None:
        return None

    out: dict[str, set[int]] = {}
    for path in files:
        p = subprocess.run(
            ["git", "diff", "--no-color", "--unified=0", "--diff-filter=ACMRT", *args, "--", path],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            return None

        new_line = None
        selected: set[int] = set()
        for line in p.stdout.splitlines():
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)(?:,(\d+))?", line)
                new_line = int(m.group(1)) if m else None
            elif new_line is not None:
                if line.startswith("+"):
                    selected.add(new_line)
                    new_line += 1
                elif not line.startswith("-"):
                    new_line += 1
        out[path] = selected
    return out


def mask_quoted_text(text: str) -> str:
    chars = list(text)
    start = None
    escaped = False
    for index, char in enumerate(text):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == '"' and not escaped:
            if start is None:
                start = index
            else:
                for position in range(start, index + 1):
                    chars[position] = " "
                start = None
        escaped = False
    return "".join(chars)


def mask_matches(text: str, pattern: re.Pattern[str]) -> str:
    chars = list(text)
    for match in pattern.finditer(text):
        for position in range(*match.span()):
            chars[position] = " "
    return "".join(chars)


def scan_text(path: str, line_number: int, text: str) -> list[tuple[str, int, str]]:
    findings = []
    unquoted = mask_quoted_text(text)
    scannable = mask_matches(unquoted, HUMAN_GUIDANCE)
    for rule_id, rx in RULES:
        if rx.search(scannable):
            findings.append((path, line_number, rule_id))
    for clause in CLAUSE_BREAK.split(scannable):
        if UNCERTAIN.search(clause) and not BENIGN_UNCERTAIN.search(clause):
            findings.append((path, line_number, "PDS005"))
            break
    return findings


def scan_file(path: str, line_numbers) -> list[tuple[str, int, str]]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [(path, 1, "PDS_READ_ERROR")]

    selected = {i for i in line_numbers if 1 <= i <= len(lines)}
    findings = set()

    for i in selected:
        findings.update(scan_text(path, i, lines[i - 1]))

    # Scan bounded windows inside one Markdown paragraph so line wrapping cannot
    # split a risky phrase across physical lines. The window is deliberately
    # capped at three non-empty lines to avoid joining unrelated sections.
    for start in range(len(lines)):
        for size in (2, 3):
            end = start + size
            if end > len(lines):
                continue
            window_lines = lines[start:end]
            if any(not line.strip() for line in window_lines):
                continue
            window_numbers = set(range(start + 1, end + 1))
            touched = selected & window_numbers
            if not touched:
                continue
            text = " ".join(line.strip() for line in window_lines)
            findings.update(scan_text(path, min(touched), text))

    return sorted(findings, key=lambda item: (item[1], item[2]))


def display_path(path: str) -> str:
    return path.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-test-fixtures", action="store_true")
    args = ap.parse_args()
    include_fixtures = args.include_test_fixtures or args.all
    candidates = all_files() if args.all else changed_files()
    files = [f for f in candidates if is_public_doc(f, include_fixtures)]
    added = None if args.all else changed_added_lines(files)
    findings = []
    for f in files:
        if added is None:
            try:
                line_numbers = range(1, len(Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()) + 1)
            except Exception:
                line_numbers = [1]
        else:
            line_numbers = sorted(added.get(f, set()))
        findings.extend(scan_file(f, line_numbers))
    if findings:
        print("public-docs-safety: FAIL")
        for f, i, rule_id in findings:
            print(f"{display_path(f)}:{i}: {rule_id}")
        return 1
    print("public-docs-safety: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
