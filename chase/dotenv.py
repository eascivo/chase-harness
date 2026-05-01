"""Minimal .env file loader — zero dependencies."""

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Skips empty lines and comments (#). Does not override existing env vars.
    """
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Remove surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        # Don't override existing env vars (env vars take precedence)
        if key not in os.environ:
            os.environ[key] = value
