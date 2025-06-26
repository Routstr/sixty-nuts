"""Sixty Nuts - NIP-60 Cashu Wallet Implementation.

Lightweight stateless Cashu wallet implementing NIP-60.
"""

from .wallet import Wallet
from .temp import TempWallet
from .types import ProofDict, WalletError

__all__ = [
    # Main wallet classes
    "Wallet",
    # Temporary wallet
    "TempWallet",
    # Shared types
    "ProofDict",
    "WalletError",
]
