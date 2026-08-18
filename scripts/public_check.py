#!/usr/bin/env python3
"""Fail closed when repository content is not suitable for public release."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / ".public-allowlist"
ZERO_OBJECT_ID = "0" * 40
MAX_TEXT_BYTES = 1_000_000

ALLOWED_SUFFIXES = {".json", ".md", ".py", ".yaml"}
ALLOWED_EXTENSIONLESS = {
    ".gitignore",
    ".public-allowlist",
    "LICENSE",
    "pre-commit",
    "pre-push",
}
FORBIDDEN_PARTS = {
    ".agents",
    ".local",
    ".private",
    "__pycache__",
    "backups",
    "local-data",
    "logs",
    "screenshots",
    "sessions",
    "trash",
}
FORBIDDEN_NAMES = {
    ".DS_Store",
    ".metadata.json",
    ".skillshare-manifest.json",
}
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
    "users.noreply.github.com",
}

HOME_PATTERNS = (
    re.compile(r"/" + r"Users/[A-Za-z0-9._-]+(?:/|\b)"),
    re.compile(r"/" + r"home/[A-Za-z0-9._-]+(?:/|\b)"),
    re.compile(r"(?i)\b[A-Z]:\\" + r"Users\\[^\\\s]+"),
)
EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"([A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,}))"
)
SENSITIVE_PATTERNS = (
    (
        "private key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?"
            + r"PRIVATE KEY-----"
        ),
    ),
    (
        "GitHub token",
        re.compile(r"\b" + r"gh" + r"[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "GitHub fine-grained token",
        re.compile(r"\b" + r"github_pat_" + r"[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "OpenAI-style token",
        re.compile(r"\b" + r"sk-" + r"[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "Tavily token",
        re.compile(r"\b" + r"tvly-" + r"[A-Za-z0-9_-]{16,}\b"),
    ),
    (
        "Slack token",
        re.compile(r"\b" + r"xox[baprs]-" + r"[A-Za-z0-9-]{16,}\b"),
    ),
    (
        "AWS access key",
        re.compile(r"\b" + r"AKIA" + r"[A-Z0-9]{16}\b"),
    ),
    (
        "assigned secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9][A-Za-z0-9_./+=-]{15,}"
        ),
    ),
)


@dataclass(frozen=True)
class Record:
    path: str
    data: bytes
    mode: str
    origin: str


@dataclass(frozen=True)
class Issue:
    path: str
    message: str
    origin: str


def run_git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def parse_allowlist(text: str) -> tuple[set[str], tuple[str, ...]]:
    exact: set[str] = set()
    prefixes: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        entry = raw_line.strip()
        if not entry or entry.startswith("#"):
            continue
        normalized = entry.rstrip("/")
        parts = PurePosixPath(normalized).parts
        if (
            not normalized
            or normalized.startswith("/")
            or "\\" in normalized
            or ".." in parts
        ):
            raise ValueError(
                f"invalid allowlist entry on line {line_number}: {entry}"
            )
        if entry.endswith("/"):
            prefixes.append(normalized + "/")
        else:
            exact.add(normalized)
    return exact, tuple(prefixes)


def load_allowlist(mode: str) -> tuple[set[str], tuple[str, ...]]:
    if mode == "index":
        try:
            payload = run_git("show", ":.public-allowlist")
        except RuntimeError as exc:
            raise ValueError(
                ".public-allowlist must be present in the index"
            ) from exc
        text = payload.decode("utf-8")
    else:
        text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    return parse_allowlist(text)


def is_allowed_path(
    path: str,
    exact: set[str],
    prefixes: tuple[str, ...],
) -> bool:
    return path in exact or any(path.startswith(prefix) for prefix in prefixes)


def read_blob(object_id: str, cache: dict[str, bytes]) -> bytes:
    if object_id not in cache:
        cache[object_id] = run_git("cat-file", "blob", object_id)
    return cache[object_id]


def working_tree_records() -> tuple[list[Record], list[Issue]]:
    paths = run_git(
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    records: list[Record] = []
    for raw_path in paths.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        absolute = ROOT / path
        if not os.path.lexists(absolute):
            continue
        if absolute.is_symlink():
            data = os.readlink(absolute).encode("utf-8")
            mode = "120000"
        elif absolute.is_file():
            data = absolute.read_bytes()
            mode = "100755" if os.access(absolute, os.X_OK) else "100644"
        else:
            continue
        records.append(Record(path, data, mode, "working tree"))
    return records, []


def index_records() -> tuple[list[Record], list[Issue]]:
    output = run_git("ls-files", "-s", "-z")
    records: list[Record] = []
    issues: list[Issue] = []
    cache: dict[str, bytes] = {}
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        metadata, raw_path = raw_entry.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if stage != "0":
            issues.append(Issue(path, "unmerged index entry", "index"))
            continue
        records.append(
            Record(path, read_blob(object_id, cache), mode, "index")
        )
    return records, issues


def pushed_history_roots() -> list[str]:
    pushed: list[str] = []
    if not sys.stdin.isatty():
        for line in sys.stdin.read().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] != ZERO_OBJECT_ID:
                pushed.append(fields[1])
    if pushed:
        return sorted(set(pushed))

    refs = run_git(
        "for-each-ref",
        "--format=%(objectname)",
        "refs/heads",
        "refs/tags",
    )
    return sorted(
        {
            line.strip()
            for line in refs.decode("ascii").splitlines()
            if line.strip()
        }
    )


def history_records() -> tuple[list[Record], list[Issue]]:
    roots = pushed_history_roots()
    if not roots:
        return [], []
    commits = run_git("rev-list", *roots).decode("ascii").splitlines()
    records: list[Record] = []
    cache: dict[str, bytes] = {}
    seen: set[tuple[str, str, str]] = set()
    for commit in commits:
        tree = run_git("ls-tree", "-r", "-z", "--full-tree", commit)
        for raw_entry in tree.split(b"\0"):
            if not raw_entry:
                continue
            metadata, raw_path = raw_entry.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split()
            if object_type != "blob":
                continue
            path = raw_path.decode("utf-8", "surrogateescape")
            identity = (path, object_id, mode)
            if identity in seen:
                continue
            seen.add(identity)
            records.append(
                Record(
                    path,
                    read_blob(object_id, cache),
                    mode,
                    f"history {commit[:12]}",
                )
            )
    return records, []


def validate_symlink(path: str, data: bytes) -> list[str]:
    try:
        target = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["symlink target is not UTF-8"]
    if target.startswith("/") or "\\" in target:
        return ["symlink target must be repository-relative"]
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(path), target)
    )
    if resolved == ".." or resolved.startswith("../"):
        return ["symlink target escapes the repository"]
    return []


def validate_text_content(path: str, data: bytes) -> list[str]:
    messages: list[str] = []
    if len(data) > MAX_TEXT_BYTES:
        return [f"text file exceeds {MAX_TEXT_BYTES} bytes"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["public text file is not UTF-8"]

    for pattern in HOME_PATTERNS:
        if pattern.search(text):
            messages.append("contains a machine-local home path")
            break

    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            messages.append(f"contains a possible {label}")

    for _address, domain in EMAIL_PATTERN.findall(text):
        if domain.lower() not in ALLOWED_EMAIL_DOMAINS:
            messages.append("contains a personal email address")
            break

    if path.startswith("examples/") and path.endswith(".json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            messages.append("example is not valid JSON")
        else:
            provenance = payload.get("meta", {}).get("provenance")
            if provenance != "synthetic":
                messages.append(
                    "public example must declare meta.provenance=synthetic"
                )
    return messages


def validate_records(
    records: list[Record],
    exact: set[str],
    prefixes: tuple[str, ...],
) -> list[Issue]:
    issues: list[Issue] = []
    for record in records:
        path = record.path
        parts = PurePosixPath(path).parts
        path_messages: list[str] = []

        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in parts
        ):
            path_messages.append("unsafe repository path")
        if any(part in FORBIDDEN_PARTS for part in parts):
            path_messages.append("path is reserved for private/local data")
        name = PurePosixPath(path).name
        if (
            name in FORBIDDEN_NAMES
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith(".log")
        ):
            path_messages.append("forbidden private/generated filename")
        if not is_allowed_path(path, exact, prefixes):
            path_messages.append("path is outside .public-allowlist")

        is_symlink = record.mode == "120000"
        suffix = PurePosixPath(path).suffix.lower()
        if (
            not is_symlink
            and suffix not in ALLOWED_SUFFIXES
            and name not in ALLOWED_EXTENSIONLESS
        ):
            path_messages.append("file type is not approved for publication")

        for message in dict.fromkeys(path_messages):
            issues.append(Issue(path, message, record.origin))

        content_messages = (
            validate_symlink(path, record.data)
            if is_symlink
            else validate_text_content(path, record.data)
        )
        for message in dict.fromkeys(content_messages):
            issues.append(Issue(path, message, record.origin))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check repository content against the public-release policy."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--working-tree", action="store_true")
    mode.add_argument("--index", action="store_true")
    mode.add_argument("--history", action="store_true")
    args = parser.parse_args()

    selected = (
        "working-tree"
        if args.working_tree
        else "index"
        if args.index
        else "history"
    )
    try:
        exact, prefixes = load_allowlist(selected)
        if selected == "working-tree":
            records, issues = working_tree_records()
        elif selected == "index":
            records, issues = index_records()
        else:
            records, issues = history_records()
        issues.extend(validate_records(records, exact, prefixes))
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError) as exc:
        print(f"Public check failed: {exc}", file=sys.stderr)
        return 1

    if issues:
        print(
            f"Public check failed: {len(issues)} issue(s) found.",
            file=sys.stderr,
        )
        for issue in issues[:100]:
            print(
                f"- [{issue.origin}] {issue.path}: {issue.message}",
                file=sys.stderr,
            )
        if len(issues) > 100:
            print(
                f"- ... {len(issues) - 100} additional issue(s) omitted",
                file=sys.stderr,
            )
        return 1

    print(
        f"Public check passed: {len(records)} file record(s) checked "
        f"in {selected} mode."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
