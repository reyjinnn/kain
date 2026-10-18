"""Fail closed for release/staged files; report locations, never secret values."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_PARTS = {".git", ".backups", "__pycache__", "node_modules", "artifacts", ".pytest_cache", ".venv", "venv", "output", "tmp", ".sisa", "localdata"}
RELEASE_DIRS = {"backend", "frontend", "references", "scripts", ".githooks", "reports"}
PATTERNS = {
    "provider_key": re.compile(rb"(?:9r_[A-Za-z0-9_-]{24,}|sk-[A-Za-z0-9_-]{24,})"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "assigned_credential": re.compile(rb"(?:NINEROUTER_KEY|OPENAI_API_KEY)[ \t]*[=:][ \t]*[\"']?([A-Za-z0-9_-]{16,})"),
}


def release_files(root=ROOT):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts) or path.name.startswith(".env") and path.name != ".env.example":
            continue
        if path.suffix in (".pyc", ".pyo", ".zip", ".log", ".sqlite", ".sqlite3", ".db") or path.name.endswith((".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm")):
            continue
        if relative.parts[0] in RELEASE_DIRS or len(relative.parts) == 1 and (path.suffix == ".md" or path.name in (".env.example", ".gitignore")):
            yield path


def local_secrets(root=ROOT):
    path = root / ".env.local"
    if not path.is_file():
        return []
    return [value.strip().encode() for line in path.read_text(encoding="utf-8").splitlines()
            for name, sep, value in [line.partition("=")]
            if sep and name.strip().endswith(("_KEY", "_TOKEN", "_SECRET")) and len(value.strip()) >= 8]


def inspect_content(name, content, secrets=()):
    findings = []
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(content):
            findings.append({"path": name, "line": content[:match.start()].count(b"\n") + 1, "rule": rule})
    for secret in secrets:
        index = content.find(secret)
        if index >= 0:
            findings.append({"path": name, "line": content[:index].count(b"\n") + 1, "rule": "configured_secret"})
    if Path(name).name.startswith(".env") and Path(name).name != ".env.example":
        findings.append({"path": name, "line": 1, "rule": "private_env_file"})
    return findings


def scan_files(files, root=ROOT):
    secrets = local_secrets(root)
    return [item for file in files for item in inspect_content(str(file.relative_to(root)), file.read_bytes(), secrets)]


def scan_staged(root=ROOT):
    names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=root).decode().split("\0")
    secrets = local_secrets(root)
    return [finding for name in names if name for finding in inspect_content(name,
            subprocess.check_output(["git", "show", ":" + name], cwd=root), secrets)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    try:
        findings = scan_staged() if args.staged else scan_files(list(release_files()))
    except (OSError, subprocess.CalledProcessError):
        print(json.dumps({"status": "failed", "reason": "FILES_NOT_INSPECTED"}))
        return 1
    print(json.dumps({"status": "failed" if findings else "passed", "findings": findings}, indent=2))
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
