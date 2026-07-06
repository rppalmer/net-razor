from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Find the Net-Razor checkout root without relying on the current directory."""

    for candidate in _candidates(start):
        if (candidate / "pyproject.toml").exists() and (
            candidate / "scripts" / "net-razor-mcp"
        ).exists():
            return candidate
    return Path.cwd().resolve()


def _candidates(start: Path | None) -> list[Path]:
    bases: list[Path] = []
    if start is not None:
        resolved = start.resolve()
        bases.append(resolved.parent if resolved.is_file() else resolved)
    bases.append(Path.cwd().resolve())

    candidates: list[Path] = []
    for base in bases:
        candidates.extend([base, *base.parents])
    return candidates
