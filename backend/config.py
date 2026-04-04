import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root (parent of `backend/`), not the shell's cwd — so Gmail/QB vars load reliably.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return value

