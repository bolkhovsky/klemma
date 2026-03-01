"""Bump version in pyproject.toml and src/klemma/__init__.py.

Usage: python scripts/bump_version.py [patch|minor|major]
"""

import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
INIT = Path(__file__).parent.parent / "src" / "klemma" / "__init__.py"


def read_version() -> str:
    text = PYPROJECT.read_text()
    m = re.search(r'^version = "(.+?)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("Could not find version in pyproject.toml")
    return m.group(1)


def bump(version: str, part: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        raise SystemExit(f"Unexpected version format: {version!r}")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    elif part == "patch":
        patch += 1
    else:
        raise SystemExit(f"Unknown bump type: {part!r}. Use patch, minor, or major.")
    return f"{major}.{minor}.{patch}"


def replace_in_file(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text()
    new_text, n = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if n == 0:
        raise SystemExit(f"Pattern not found in {path}: {pattern!r}")
    path.write_text(new_text)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/bump_version.py [patch|minor|major]")

    part = sys.argv[1]
    old = read_version()
    new = bump(old, part)

    replace_in_file(PYPROJECT, r'^(version = ")[^"]+"', rf'\g<1>{new}"')
    replace_in_file(INIT, r'^(__version__ = ")[^"]+"', rf'\g<1>{new}"')

    print(f"{old} → {new}")


if __name__ == "__main__":
    main()
