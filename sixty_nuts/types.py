"""Shared types for the sixty_nuts package."""

from __future__ import annotations

from typing import TypedDict


class ProofDict(TypedDict):
    """Extended proof structure for NIP-60 wallet use.

    Extends the basic Proof with mint URL tracking for multi-mint support.
    """

    id: str
    amount: int
    secret: str
    C: str
    mint: str


class WalletError(Exception):
    """Base class for wallet errors."""
