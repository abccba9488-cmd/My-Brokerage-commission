"""Thin wrapper around the FinMind SDK with token-aware, fail-soft behavior."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from FinMind.data import DataLoader

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def has_sponsor_token() -> bool:
    return bool(os.getenv("FINMIND_TOKEN"))


_cached_loader: DataLoader | None = None


def get_loader() -> DataLoader:
    """Return a cached, authenticated DataLoader (or an anonymous one on the
    free tier). Cached at module scope so the broker-branch fetch loop, which
    calls this once per date, doesn't re-authenticate on every call."""
    global _cached_loader
    if _cached_loader is not None:
        return _cached_loader

    loader = DataLoader()
    token = os.getenv("FINMIND_TOKEN")
    if token:
        loader.login_by_token(api_token=token)
    _cached_loader = loader
    return loader
