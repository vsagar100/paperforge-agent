"""Explicit, non-secret-reporting dotenv loading for CLI and Python integrations."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def checkout_root(start: Path | None = None) -> Path | None:
    """Find a PaperForge checkout, not an unrelated ancestor's .env."""
    location = (start or Path.cwd()).resolve()
    for parent in (location, *location.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/paperforge").is_dir():
            return parent
    return None


def environment_files(project: Path | None = None, root: Path | None = None) -> list[Path]:
    shared = root or checkout_root() or checkout_root(Path(__file__).parent)
    paths = ([shared / ".env"] if shared else []) + (
        [project.resolve() / ".env"] if project else []
    )
    return list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))


def file_values(project: Path | None = None, root: Path | None = None) -> dict[str, str]:
    """Root defaults, then project overrides; ignore blank template placeholders."""
    values = {}
    for path in environment_files(project, root):
        values.update(
            {
                key: value
                for key, value in dotenv_values(
                    path, encoding="utf-8-sig", interpolate=False
                ).items()
                if value is not None and value.strip()
            }
        )
    return values


def load_environment(project: Path | None = None, root: Path | None = None) -> list[Path]:
    """Existing process values take precedence; never return or log key values."""
    for key, value in file_values(project, root).items():
        if not os.environ.get(key):
            os.environ[key] = value
    return environment_files(project, root)
