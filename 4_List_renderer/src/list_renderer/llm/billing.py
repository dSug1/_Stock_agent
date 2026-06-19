"""Pluggable billing/credential context (D24).

v1 ships exactly one context: `self` — the local user's own Anthropic key from
the environment / repo `.env`. Future payer models (user BYOK, app-owner
corporate key, advertiser-subsidized pool) become additional contexts behind the
same interface, so the runner never changes.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root .env (shared with 2_Funds / 3_Biopharm).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _REPO_ROOT / ".env"


def load_env() -> None:
    """Load KEY=VALUE pairs from the repo .env into os.environ (no overwrite).
    Silent if the file is absent. Never prints secrets."""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class BillingContext:
    """Who pays + how the API client is built. v1: name='self', key from env."""

    def __init__(self, name: str = "self", api_key: str | None = None):
        self.name = name
        self._api_key = api_key

    @property
    def payer(self) -> str:
        return self.name

    def has_credentials(self) -> bool:
        return bool(self._api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def client(self):
        """Construct an Anthropic client for this context."""
        import anthropic  # imported lazily so cost-only paths need no SDK
        if self._api_key:
            return anthropic.Anthropic(api_key=self._api_key)
        return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def self_context() -> BillingContext:
    """The local single-user context (reads ANTHROPIC_API_KEY from env/.env)."""
    load_env()
    return BillingContext("self")
