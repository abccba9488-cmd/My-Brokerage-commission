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


def get_loader() -> DataLoader:
    """Return an authenticated DataLoader if FINMIND_TOKEN is set, otherwise
    an anonymous loader restricted to free-tier datasets (300 req/hour)."""
    loader = DataLoader()
    token = os.getenv("FINMIND_TOKEN")
    if token:
        loader.login_by_token(api_token=token)
    return loader
