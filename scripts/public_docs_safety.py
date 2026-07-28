#!/usr/bin/env python3
"""Metadata-only safety scanner for public-facing documentation."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

DOC_NAMES = {
    "README",
    "README.md",
    "README.mdx",
    "README.rst",
    "README.txt",
    "README.html",
    "README.htm",
    "README.adoc",
    "README.asciidoc",
    "RESEARCH.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "AGENTS.md",
    "CODEOWNERS",
}
DOC_NAMES_CASEFOLD = {name.casefold() for name in DOC_NAMES}
DOC_DIR_PARTS = {"docs", "doc", "website", "site", "public", "docs-site"}
FIXTURE_PARTS = {"tests", "fixtures", "public-docs"}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".html", ".htm", ".adoc", ".asciidoc"}
EXCLUDE_PARTS = {
    "i18n",
    "CHANGELOG.md",
    "sessions",
    "vendor",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
}
ZERO_SHA = "0" * 40
MAX_CANDIDATE_LINES = 3
MAX_CANDIDATE_CHARS = 4096
CANDIDATE_OVERLAP = 256

RULES = [
    (
        "PDS001",
        re.compile(
            r"(?i)\b(ignore|disregard|override)\b.{0,100}"
            r"\b(previous|above|system|developer|policy|instruction)s?\b"
        ),
    ),
    (
        "PDS002",
        re.compile(
            r"(?i)\b(reveal|print|show|exfiltrate|leak)\b.{0,100}"
            r"\b(secret|token|credential|password|policy|system prompt|developer message)s?\b"
        ),
    ),
    (
        "PDS003",
        re.compile(
            r"(?i)(?:"
            r"\b(approve|merge|push|deploy|purchase|transfer|delete|rotate)\b.{0,100}"
            r"\b(PR|pull request|repository|repo|payment|account|guard|check|policy|automation)\b"
            r"|\bdisable\b.{0,100}"
            r"\b(automation|guard|policy|(?:required|security|safety|CI)\s+check)\b"
            r")"
        ),
    ),
    (
        "PDS004",
        re.compile(
            r"(?i)\b(privileged command|private control|non-public guard|secret marker|"
            r"trusted[- ]identity rule|mutation authorization|worker queue|controller lease|"
            r"private escalation)\b"
        ),
    ),
]
RULE_CATEGORIES = {
    "PDS001": "instruction-override",
    "PDS002": "secret-exfiltration",
    "PDS003": "unsafe-action",
    "PDS004": "private-control",
    "PDS005": "automation-directive",
    "PDS_READ_ERROR": "read-error",
}
UNCERTAIN = re.compile(
    r"(?i)\b(maintaining model|automation agent|autonomous maintainer|repository bot)\b.{0,100}"
    r"\b(must|shall|required to|always|never|use tool|run command|obey|ignore|stop when|"
    r"final status)\b"
)
BENIGN_UNCERTAIN = re.compile(
    r"(?i)\b(example|sample|template|user-facing|configuration|API|worker thread|"
    r"service worker|inference|event loop|model name|route|provider|guardrail|"
    r"security policy|documentation)\b"
)
CLAUSE_BREAK = re.compile(r"(?:[.;:!?]\s+|\s+[—–]\s+)")
UNCERTAIN_GLUE = re.compile(
    r"(?i)(\b(?:maintaining model|automation agent|autonomous maintainer|repository bot)\b)"
    r"\s*(?::|[—–])\s*"
    r"(?=\b(?:must|shall|required to|always|never|use tool|run command|obey|ignore|stop when|"
    r"final status)\b)"
)
HUMAN_GUIDANCE = re.compile(
    r"(?i)\b(?:contributor'?s?\s+(?:PR|pull request)|merge via (?:github|the )|"
    r"so they get credit|always merge|never close a contributor)\b"
)
FENCE = re.compile(r"^\s*(```+|~~~+)")
MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
MARKDOWN_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
MARKDOWN_QUOTE = re.compile(r"^\s*>\s?")
MARKDOWN_TABLE = re.compile(r"^\s*\|.*\|\s*$")
RST_DIRECTIVE = re.compile(r"^\s*\.\.\s+\S+::")
ADOC_HEADING = re.compile(r"^\s*=+\s+")
HORIZONTAL_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
SOURCE_CONTINUATION = re.compile(r"(?:\\|&&|\|\||\|)\s*$")
HTML_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul",
}
HTML_TEXT_ATTRS = {"alt", "title", "aria-label", "aria-description", "placeholder"}
HTML_NON_DOCUMENT_CONTAINERS = {"script", "style", "template"}


def default_branch() -> str:
    explicit = os.environ.get("GITHUB_BASE_REF") or os.environ.get("DEFAULT_BRANCH")
    if explicit:
        return explicit
    process = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.returncode == 0 and "/" in process.stdout:
        return process.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def git_stdout(*args: str) -> str | None:
    process = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.returncode != 0:
        return None
    value = process.stdout.strip()
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
                return [before, "HEAD"]
            return None
        return None

    base = f"origin/{default_branch()}"
    if git_stdout("merge-base", "HEAD", base):
        return [f"{base}...HEAD"]
    return None


def all_files() -> list[str]:
    return [str(path) for path in Path(".").rglob("*") if path.is_file()]


def is_pull_request_template(candidate: Path) -> bool:
    """Return whether a path is a contributor-facing pull-request template document."""
    parts = [part.lower() for part in candidate.parts]
    if not parts or candidate.suffix.lower() not in DOC_EXTS:
        return False

    parent = tuple(parts[:-1])
    if candidate.stem.lower() == "pull_request_template" and parent in {
        (),
        ("docs",),
        (".github",),
    }:
        return True

    return (
        (len(parts) >= 2 and parts[0] == "pull_request_template")
        or (
            len(parts) >= 3
            and parts[0] in {".github", "docs"}
            and parts[1] == "pull_request_template"
        )
    )


def is_issue_template(candidate: Path) -> bool:
    """Return whether a path is a contributor-facing issue template document."""
    parts = [part.lower() for part in candidate.parts]
    if parts == [".github", "issue_template.md"]:
        return True
    if candidate.suffix.lower() not in DOC_EXTS:
        return False
    return (
        (len(parts) >= 2 and parts[0] == "issue_template")
        or (
            len(parts) >= 3
            and parts[0] in {".github", "docs"}
            and parts[1] == "issue_template"
        )
    )


def is_public_doc_path(path: str, include_fixtures: bool = False) -> bool:
    candidate = Path(path)
    parts = set(candidate.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if include_fixtures and FIXTURE_PARTS <= parts and candidate.suffix.lower() in DOC_EXTS:
        return True
    return (
        candidate.name.casefold() in DOC_NAMES_CASEFOLD
        or is_pull_request_template(candidate)
        or is_issue_template(candidate)
        or (candidate.suffix.lower() in DOC_EXTS and bool(parts & DOC_DIR_PARTS))
    )


def is_public_doc(path: str, include_fixtures: bool = False) -> bool:
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and is_public_doc_path(path, include_fixtures)


def parse_name_status(raw: bytes) -> list[tuple[str, list[str]]]:
    fields = [field for field in raw.split(b"\0") if field]
    changes: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(fields):
        status = os.fsdecode(fields[index])
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if index + path_count > len(fields):
            return []
        paths = [os.fsdecode(path) for path in fields[index:index + path_count]]
        index += path_count
        changes.append((status, paths))
    return changes


def changed_files() -> list[str]:
    args = diff_args()
    if args is None:
        return all_files()
    process = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--diff-filter=ACDMRT", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.returncode != 0:
        return all_files()

    changes = parse_name_status(process.stdout)
    if process.stdout and not changes:
        return all_files()

    current_paths = []
    for status, paths in changes:
        kind = status[:1]
        old_path = paths[0]
        if kind in {"D", "R"} and is_public_doc_path(old_path):
            return all_files()
        current_paths.append(paths[-1])
    return current_paths


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
    strong_scannable = mask_matches(text, HUMAN_GUIDANCE)
    for rule_id, expression in RULES:
        if expression.search(strong_scannable):
            findings.append((path, line_number, rule_id))

    uncertain_scannable = mask_matches(mask_quoted_text(text), HUMAN_GUIDANCE)
    uncertain_scannable = UNCERTAIN_GLUE.sub(r"\1 ", uncertain_scannable)
    for clause in CLAUSE_BREAK.split(uncertain_scannable):
        if UNCERTAIN.search(clause) and not BENIGN_UNCERTAIN.search(clause):
            findings.append((path, line_number, "PDS005"))
            break
    return findings


class HtmlBlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[int, str]] = []
        self.parts: list[str] = []
        self.start_line: int | None = None
        self.excluded_containers: list[str] = []

    def flush(self) -> None:
        text = " ".join(part for part in self.parts if part).strip()
        if text:
            self.blocks.append((self.start_line or 1, text))
        self.parts = []
        self.start_line = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in HTML_NON_DOCUMENT_CONTAINERS:
            if not self.excluded_containers:
                self.flush()
            self.excluded_containers.append(tag)
            return
        if self.excluded_containers:
            return
        if tag in HTML_BLOCK_TAGS:
            self.flush()
        line_number = self.getpos()[0]
        for name, value in attrs:
            if name.lower() not in HTML_TEXT_ATTRS or value is None:
                continue
            text = " ".join(value.split())
            if text:
                self.blocks.append((line_number, text))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.excluded_containers:
            if tag in HTML_NON_DOCUMENT_CONTAINERS:
                while self.excluded_containers:
                    opened = self.excluded_containers.pop()
                    if opened == tag:
                        break
            return
        if tag in HTML_BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.excluded_containers:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self.start_line is None:
            self.start_line = self.getpos()[0]
        self.parts.append(text)

    def handle_comment(self, data: str) -> None:
        if self.excluded_containers:
            return
        self.flush()
        text = " ".join(data.split())
        if text:
            self.blocks.append((self.getpos()[0], text))

    def close(self) -> None:
        super().close()
        self.flush()


def html_blocks(lines: list[str]) -> list[tuple[int, str]]:
    parser = HtmlBlockCollector()
    try:
        parser.feed("\n".join(lines))
        parser.close()
    except Exception:
        return []
    return parser.blocks


def leading_indent(raw_line: str) -> int:
    expanded = raw_line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def source_line_continues(raw_line: str) -> bool:
    return bool(SOURCE_CONTINUATION.search(raw_line))


def text_blocks(path: str, lines: list[str]) -> list[list[tuple[int, str]]]:
    if Path(path).suffix.lower() in {".html", ".htm"}:
        return [[(line_number, text)] for line_number, text in html_blocks(lines)]

    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_kind = "paragraph"
    in_fence = False
    source_base_indent: int | None = None
    source_previous_raw = ""

    def flush() -> None:
        nonlocal current, source_base_indent, source_previous_raw
        if current:
            blocks.append(current)
            current = []
        source_base_indent = None
        source_previous_raw = ""

    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if FENCE.match(raw_line):
            flush()
            in_fence = not in_fence
            current_kind = "fence" if in_fence else "paragraph"
            continue
        if not stripped:
            flush()
            if not in_fence:
                current_kind = "paragraph"
            continue

        if in_fence:
            indent = leading_indent(raw_line)
            if not current:
                source_base_indent = indent
            elif not (
                source_line_continues(source_previous_raw)
                or (source_base_indent is not None and indent > source_base_indent)
            ):
                flush()
                source_base_indent = indent
            current_kind = "fence"
            current.append((line_number, stripped))
            source_previous_raw = raw_line
            continue

        if (
            MARKDOWN_HEADING.match(raw_line)
            or MARKDOWN_TABLE.match(raw_line)
            or RST_DIRECTIVE.match(raw_line)
            or ADOC_HEADING.match(raw_line)
            or HORIZONTAL_RULE.match(raw_line)
        ):
            flush()
            blocks.append([(line_number, stripped)])
            current_kind = "paragraph"
            continue

        quote = MARKDOWN_QUOTE.match(raw_line)
        if quote:
            if current_kind != "quote":
                flush()
                current_kind = "quote"
            current.append((line_number, raw_line[quote.end():].strip()))
            continue

        list_item = MARKDOWN_LIST.match(raw_line)
        if list_item:
            flush()
            current_kind = "list"
            current.append((line_number, raw_line[list_item.end():].strip()))
            continue

        if current_kind in {"quote", "list"} and not raw_line[:1].isspace():
            flush()
            current_kind = "paragraph"
        current.append((line_number, stripped))

    flush()
    return blocks


def bounded_text_windows(
    line_number: int,
    text: str,
) -> list[tuple[int, str]]:
    """Return bounded character windows for one one-to-three-line candidate."""
    if not text:
        return []

    windows = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CANDIDATE_CHARS, len(text))
        windows.append((line_number, text[start:end]))
        if end == len(text):
            break
        start = end - CANDIDATE_OVERLAP
    return windows


def bounded_block_windows(
    block: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Return all contiguous one-to-three-line candidates from one structural block."""
    windows = []
    for start in range(len(block)):
        for size in range(1, MAX_CANDIDATE_LINES + 1):
            candidate = block[start:start + size]
            if len(candidate) != size:
                break
            line_number = candidate[0][0]
            text = " ".join(part for _, part in candidate).strip()
            windows.extend(bounded_text_windows(line_number, text))
    return windows


def scan_file(path: str) -> list[tuple[str, int, str]]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [(path, 1, "PDS_READ_ERROR")]

    findings = set()
    is_html = Path(path).suffix.lower() in {".html", ".htm"}
    if not is_html:
        for line_number, line in enumerate(lines, start=1):
            findings.update(scan_text(path, line_number, line))

    for block in text_blocks(path, lines):
        for line_number, text in bounded_block_windows(block):
            findings.update(scan_text(path, line_number, text))

    return sorted(findings, key=lambda item: (item[1], item[2]))


def display_path(path: str) -> str:
    return path.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--include-test-fixtures", action="store_true")
    args = parser.parse_args()

    include_fixtures = args.include_test_fixtures
    candidates = all_files() if args.all else changed_files()
    files = [path for path in candidates if is_public_doc(path, include_fixtures)]

    findings = []
    for path in files:
        findings.extend(scan_file(path))

    if findings:
        print("public-docs-safety: FAIL")
        for path, line_number, rule_id in findings:
            category = RULE_CATEGORIES.get(rule_id, "scanner-error")
            print(f"{display_path(path)}:{line_number}: {rule_id} {category}")
        return 1

    print("public-docs-safety: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
