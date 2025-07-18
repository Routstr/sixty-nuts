"""Type definitions for the sixty-nuts package following NUT-00 specifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


class Proof(TypedDict):
    """Extended proof structure for NIP-60 wallet use.

    Extends the basic Proof with mint URL tracking for multi-mint support.
    """

    id: str
    amount: int
    secret: str
    C: str
    mint: str
    unit: CurrencyUnit


class WalletError(Exception):
    """Base class for wallet errors."""


class MintError(Exception):
    """Base exception for mint errors."""

    pass


class RelayError(Exception):
    """Base exception for relay errors."""

    pass


class LNURLError(Exception):
    """Base exception for LNURL errors."""

    pass


# Standard currency units as per NUT-00 specification
CurrencyUnit = Literal[
    "btc",  # Bitcoin
    "sat",  # Satoshi (1e-8 BTC)
    "msat",  # Millisatoshi (1e-11 BTC)
    "usd",  # US Dollar
    "eur",  # Euro
    "gbp",  # British Pound
    "jpy",  # Japanese Yen
    "cny",  # Chinese Yuan
    "cad",  # Canadian Dollar
    "chf",  # Swiss Franc
    "aud",  # Australian Dollar
    "inr",  # Indian Rupee
    # Special units
    "auth",  # Authentication tokens
    # Stablecoins
    "usdt",  # Tether
    "usdc",  # USD Coin
    "dai",  # DAI Stablecoin
]


class BlindedMessage(TypedDict):
    """Blinded message for mint operations."""

    amount: int
    B_: str  # hex encoded blinded message
    id: str  # keyset ID


class BlindedSignature(TypedDict):
    """Blinded signature response from mint."""

    amount: int
    C_: str  # hex encoded blinded signature
    id: str  # keyset ID


@dataclass
class KeysetInfo:
    """Complete keyset information."""

    id: str
    mint_url: str
    unit: CurrencyUnit
    active: bool
    input_fee_ppk: int = 0
    keys: dict[str, str] = field(default_factory=dict)  # amount -> pubkey
    denominations: list[int] = field(default_factory=list)  # available denominations

    def __post_init__(self):
        """Extract denominations from keys if not provided."""
        if not self.denominations and self.keys:
            self.denominations = sorted([int(amount) for amount in self.keys.keys()])


class EventKind:
    """Nostr event kinds used by the wallet."""

    # NIP-60 wallet events
    Wallet = 37375  # NIP-60 wallet event kind (replaceable)
    Token = 7375  # NIP-60 token event kind
    TokenHistory = 7376  # NIP-60 spending history event kind

    # Standard Nostr events
    Metadata = 0  # NIP-01 metadata
    TextNote = 1  # NIP-01 text note
    ContactList = 3  # NIP-02 contact list
    DirectMessage = 4  # NIP-04 encrypted direct message
    Deletion = 5  # NIP-09 event deletion
    FollowList = 30000  # NIP-65 relay list


# Event type definitions
EventDict = dict[str, Any]  # Generic Nostr event dictionary


@dataclass
class WalletState:
    """Wallet state with balance tracking."""

    proofs: list[Proof]
    proof_to_event_id: dict[str, str] | None = None

    @property
    def balance_by_mint(self) -> dict[str, int]:
        return {p["mint"]: p["amount"] for p in self.proofs}

    @property
    def balance_by_unit(self) -> dict[CurrencyUnit, int]:
        """Get total balance grouped by currency unit."""
        balances: dict[CurrencyUnit, int] = {}
        for proof in self.proofs:
            unit = proof["unit"]
            balances[unit] = balances.get(unit, 0) + proof["amount"]
        return balances

    async def total_balance_sat(self) -> int:
        """Get total balance in satoshis (only BTC-based currencies)."""
        total_sats = 0
        for proof in self.proofs:
            if proof["unit"] == "sat":
                total_sats += proof["amount"]
            elif proof["unit"] == "msat":
                total_sats += proof["amount"] // 1000
            else:
                from .mint import Mint

                mint = Mint(proof["mint"])
                exchange_rate = await mint.melt_exchange_rate(proof["unit"])
                total_sats += int(proof["amount"] * exchange_rate * 0.99)
        return total_sats

    @property
    def proofs_by_keyset(self) -> dict[str, list[Proof]]:
        """Group proofs by keyset ID."""
        grouped: dict[str, list[Proof]] = {}
        for proof in self.proofs:
            keyset_id = proof["id"]
            if keyset_id not in grouped:
                grouped[keyset_id] = []
            grouped[keyset_id].append(proof)
        return grouped

    @property
    def proofs_by_mint(self) -> dict[str, list[Proof]]:
        """Group proofs by mint URL."""
        grouped: dict[str, list[Proof]] = {}
        for proof in self.proofs:
            mint_url = proof["mint"]
            if mint_url not in grouped:
                grouped[mint_url] = []
            grouped[mint_url].append(proof)
        return grouped

    @property
    def mint_balances(self) -> dict[str, int]:
        """Get balances for all mints in sats."""
        balances: dict[str, int] = {}
        for mint_url, proofs in self.proofs_by_mint.items():
            for proof in proofs:
                if proof["unit"] == "sat":
                    balances[mint_url] = balances.get(mint_url, 0) + proof["amount"]
                elif proof["unit"] == "msat":
                    balances[mint_url] = (
                        balances.get(mint_url, 0) + proof["amount"] // 1000
                    )
                else:
                    raise NotImplementedError(
                        f"Balance for {proof['unit']} not supported"
                    )
        return balances


class KeysetResponse(TypedDict):
    """Response from GET /v1/keysets endpoint."""

    keysets: list[dict[str, Any]]


class KeysResponse(TypedDict):
    """Response from GET /v1/keys endpoint."""

    keysets: list[dict[str, Any]]


class MintQuoteResponse(TypedDict):
    """Response from POST /v1/mint/quote/bolt11 endpoint."""

    quote: str
    request: str  # Lightning invoice
    state: str
    expiry: int


class MeltQuoteResponse(TypedDict):
    """Response from POST /v1/melt/quote/bolt11 endpoint."""

    quote: str
    amount: int
    fee_reserve: int
    state: str
    expiry: int


class SwapResponse(TypedDict):
    """Response from POST /v1/swap endpoint."""

    signatures: list[BlindedSignature]


class MintResponse(TypedDict):
    """Response from POST /v1/mint/bolt11 endpoint."""

    signatures: list[BlindedSignature]


class CheckStateResponse(TypedDict):
    """Response from POST /v1/checkstate endpoint."""

    states: list[dict[str, str]]  # Y -> state mapping
