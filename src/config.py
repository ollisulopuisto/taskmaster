"""Central configuration loader.

Loads environment variables from ~/.config/taskmaster/.env (preferred)
and falls back to .env in the current working directory.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_config() -> None:
    """Load configuration from standard locations.

    Priority order (later overrides earlier):
    1. ~/.config/taskmaster/.env
    2. .env in current working directory
    3. Environment variables already set (never overridden)
    """
    # User config directory (cross-platform)
    config_dir = Path.home() / ".config" / "taskmaster"
    user_env = config_dir / ".env"

    # Load user config first (lower priority)
    if user_env.exists():
        load_dotenv(user_env, override=False)

    # Then load project .env (higher priority for local dev)
    project_env = Path.cwd() / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=False)


def get_config_dir() -> Path:
    """Return the user config directory, creating it if needed."""
    config_dir = Path.home() / ".config" / "taskmaster"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_user_env_path() -> Path:
    """Return the path to the user config .env file."""
    return get_config_dir() / ".env"
