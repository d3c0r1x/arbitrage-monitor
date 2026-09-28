#!/usr/bin/env python3
"""
Concat Project Files.

Walks all project files (excluding .env, __pycache__, .git, .freebuff,
.agents, .mypy_cache, .pytest_cache, .ruff_cache, binary/sqlite/venv data)
and concatenates them into a single .txt file with clear file-path headers.

Usage:
    python concat_project.py
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "project_dump.txt"

EXCLUDE_DIRS = {
    ".env", "__pycache__", ".git", ".freebuff", ".agents",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

EXCLUDE_EXTENSIONS = {
    ".db", ".db-shm", ".db-wal", ".sqlite3", ".pyc", ".pyo",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib",
    ".log", ".jsonl", ".json",
}

EXCLUDE_FILES = {
    ".env", "project_dump.txt", "concat_project.py",
    "database.db", "run_report.html",
    "desktop.db", "desktop.db-shm", "desktop.db-wal",
}

BINARY_EXTENSIONS = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg", ".ico"}


def should_include(path: Path) -> bool:
    """Check if file should be included in the dump."""
    # Skip excluded dirs
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return False

    # Skip excluded files by name
    if path.name in EXCLUDE_FILES:
        return False

    # Skip by extension
    if path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False

    # Skip binary
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False

    # Skip if looks binary (first 1KB check)
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
            if b"\x00" in chunk:
                return False  # null byte → binary
    except (OSError, PermissionError):
        return False

    # Skip large files (>5MB)
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return False
    except OSError:
        return False

    return True


def collect_files(root: Path) -> list[Path]:
    """Collect all includable files sorted by path."""
    files = []
    for fpath in sorted(root.rglob("*")):
        if fpath.is_file() and should_include(fpath):
            files.append(fpath)
    return files


def write_dump(files: list[Path], root: Path, output: Path) -> None:
    """Write concatenated dump with file path headers."""
    total_chars = 0
    with open(output, "w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write("PROJECT DUMP — MEXC × DEX Arbitrage Monitor\n")
        out.write(f"Generated: {__import__('datetime').datetime.now()}\n")
        out.write(f"Total files: {len(files)}\n")
        out.write("=" * 80 + "\n\n")

        for fpath in files:
            rel = fpath.relative_to(root).as_posix()
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            sep = "=" * 80
            header = f"{sep}\n# FILE: {rel}\n{sep}\n\n"
            out.write(header)
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            out.write("\n")
            total_chars += len(content)

    size_kb = output.stat().st_size / 1024
    print(f"Written: {output} ({size_kb:.0f} KB, {len(files)} files, {total_chars:,} chars)")


def main():
    print("Collecting files...")
    files = collect_files(ROOT)
    print(f"Found {len(files)} files to include")

    # Filter out non-text files by trying to read
    text_files = []
    for f in files:
        try:
            f.read_text(encoding="utf-8", errors="replace")
            text_files.append(f)
        except (UnicodeDecodeError, OSError):
            pass

    print(f"After text filter: {len(text_files)} files")
    write_dump(text_files, ROOT, OUTPUT)


if __name__ == "__main__":
    main()
