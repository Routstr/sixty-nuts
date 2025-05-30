"""Cashu Mint API client wrapper."""

from __future__ import annotations

from typing import TypedDict, cast, Any

import httpx


# ──────────────────────────────────────────────────────────────────────────────
# Type definitions based on OpenAPI spec
# ──────────────────────────────────────────────────────────────────────────────


class BlindedMessage(TypedDict):
    """Blinded message to be signed by mint."""

    amount: int
    id: str  # keyset id
    B_: str  # blinded secret


class BlindedSignature(TypedDict):
    """Blind signature from mint."""

    amount: int
    id: str  # keyset id
    C_: str  # blinded signature


class Proof(TypedDict, total=False):
    """Cashu proof/token."""

    id: str  # keyset id
    amount: int
    secret: str
    C: str  # signature
    Y: str  # optional for P2PK
    witness: str  # optional witness data
    dleq: dict[str, Any]  # optional DLEQ proof


class MintInfo(TypedDict, total=False):
    """Mint information response."""

    name: str
    pubkey: str
    version: str
    description: str
    description_long: str
    contact: list[dict[str, str]]
    icon_url: str
    motd: str
    nuts: dict[str, dict[str, Any]]


class KeysResponse(TypedDict):
    """Mint keys response."""

    keysets: list[dict[str, str]]  # id -> keys mapping


class KeysetsResponse(TypedDict):
    """Active keysets response."""

    keysets: list[dict[str, str | int]]  # id, unit, active, input_fee_ppk


class PostMintQuoteRequest(TypedDict, total=False):
    """Request body for mint quote."""

    unit: str
    amount: int
    description: str
    pubkey: str  # for P2PK


class PostMintQuoteResponse(TypedDict, total=False):
    """Mint quote response."""

    quote: str  # quote id
    request: str  # bolt11 invoice
    amount: int
    unit: str
    state: str  # "UNPAID", "PAID", "ISSUED"
    expiry: int
    pubkey: str
    paid: bool


class PostMintRequest(TypedDict, total=False):
    """Request body for minting tokens."""

    quote: str
    outputs: list[BlindedMessage]
    signature: str  # optional for P2PK


class PostMintResponse(TypedDict):
    """Mint response with signatures."""

    signatures: list[BlindedSignature]


class PostMeltQuoteRequest(TypedDict, total=False):
    """Request body for melt quote."""

    unit: str
    request: str  # bolt11 invoice
    options: dict[str, Any]


class PostMeltQuoteResponse(TypedDict, total=False):
    """Melt quote response."""

    quote: str
    amount: int
    unit: str
    request: str
    fee_reserve: int
    paid: bool
    state: str
    expiry: int
    payment_preimage: str
    change: list[BlindedSignature]


class PostMeltRequest(TypedDict, total=False):
    """Request body for melting tokens."""

    quote: str
    inputs: list[Proof]
    outputs: list[BlindedMessage]  # for change


class PostSwapRequest(TypedDict):
    """Request body for swapping proofs."""

    inputs: list[Proof]
    outputs: list[BlindedMessage]


class PostSwapResponse(TypedDict):
    """Swap response."""

    signatures: list[BlindedSignature]


class PostCheckStateRequest(TypedDict):
    """Request body for checking proof states."""

    Ys: list[str]  # Y values from proofs


class PostCheckStateResponse(TypedDict):
    """Check state response."""

    states: list[dict[str, str]]  # Y -> state mapping


class PostRestoreRequest(TypedDict):
    """Request body for restoring proofs."""

    outputs: list[BlindedMessage]


class PostRestoreResponse(TypedDict, total=False):
    """Restore response."""

    outputs: list[BlindedMessage]
    signatures: list[BlindedSignature]
    promises: list[BlindedSignature]  # deprecated


# ──────────────────────────────────────────────────────────────────────────────
# Mint API client
# ──────────────────────────────────────────────────────────────────────────────


class MintError(Exception):
    """Raised when mint returns an error response."""


class Mint:
    """Async HTTP client wrapper for Cashu mint API."""

    def __init__(self, url: str, *, client: httpx.AsyncClient | None = None) -> None:
        """Initialize mint client.

        Args:
            url: Base URL of the mint (e.g. "https://testnut.cashu.space")
            client: Optional httpx client to reuse connections
        """
        self.url = url.rstrip("/")
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client if we created it."""
        if self._owns_client:
            await self.client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to mint."""
        response = await self.client.request(
            method,
            f"{self.url}{path}",
            json=json,
            params=params,
        )

        if response.status_code >= 400:
            raise MintError(f"Mint returned {response.status_code}: {response.text}")

        return response.json()

    # ───────────────────────── Info & Keys ─────────────────────────────────

    async def get_info(self) -> MintInfo:
        """Get mint information."""
        return cast(MintInfo, await self._request("GET", "/v1/info"))

    async def get_keys(self, keyset_id: str | None = None) -> KeysResponse:
        """Get mint public keys for a keyset (or newest if not specified)."""
        path = f"/v1/keys/{keyset_id}" if keyset_id else "/v1/keys"
        return cast(KeysResponse, await self._request("GET", path))

    async def get_keysets(self) -> KeysetsResponse:
        """Get all active keyset IDs."""
        return cast(KeysetsResponse, await self._request("GET", "/v1/keysets"))

    # ───────────────────────── Minting (receive) ─────────────────────────────────

    async def create_mint_quote(
        self,
        *,
        unit: str,
        amount: int,
        description: str | None = None,
        pubkey: str | None = None,
    ) -> PostMintQuoteResponse:
        """Request a Lightning invoice to mint tokens."""
        body: dict[str, Any] = {
            "unit": unit,
            "amount": amount,
        }
        if description is not None:
            body["description"] = description
        if pubkey is not None:
            body["pubkey"] = pubkey

        return cast(
            PostMintQuoteResponse,
            await self._request("POST", "/v1/mint/quote/bolt11", json=body),
        )

    async def get_mint_quote(self, quote_id: str) -> PostMintQuoteResponse:
        """Check status of a mint quote."""
        return cast(
            PostMintQuoteResponse,
            await self._request("GET", f"/v1/mint/quote/bolt11/{quote_id}"),
        )

    async def mint(
        self,
        *,
        quote: str,
        outputs: list[BlindedMessage],
        signature: str | None = None,
    ) -> PostMintResponse:
        """Mint tokens after paying the Lightning invoice."""
        body: dict[str, Any] = {
            "quote": quote,
            "outputs": outputs,
        }
        if signature is not None:
            body["signature"] = signature

        return cast(
            PostMintResponse, await self._request("POST", "/v1/mint/bolt11", json=body)
        )

    # ───────────────────────── Melting (send) ─────────────────────────────────

    async def create_melt_quote(
        self,
        *,
        unit: str,
        request: str,
        options: dict[str, Any] | None = None,
    ) -> PostMeltQuoteResponse:
        """Get a quote for paying a Lightning invoice."""
        body: dict[str, Any] = {
            "unit": unit,
            "request": request,
        }
        if options is not None:
            body["options"] = options

        return cast(
            PostMeltQuoteResponse,
            await self._request("POST", "/v1/melt/quote/bolt11", json=body),
        )

    async def get_melt_quote(self, quote_id: str) -> PostMeltQuoteResponse:
        """Check status of a melt quote."""
        return cast(
            PostMeltQuoteResponse,
            await self._request("GET", f"/v1/melt/quote/bolt11/{quote_id}"),
        )

    async def melt(
        self,
        *,
        quote: str,
        inputs: list[Proof],
        outputs: list[BlindedMessage] | None = None,
    ) -> PostMeltQuoteResponse:
        """Melt tokens to pay a Lightning invoice."""
        body: dict[str, Any] = {
            "quote": quote,
            "inputs": inputs,
        }
        if outputs is not None:
            body["outputs"] = outputs

        return cast(
            PostMeltQuoteResponse,
            await self._request("POST", "/v1/melt/bolt11", json=body),
        )

    # ───────────────────────── Token Management ─────────────────────────────────

    async def swap(
        self,
        *,
        inputs: list[Proof],
        outputs: list[BlindedMessage],
    ) -> PostSwapResponse:
        """Swap proofs for new blinded signatures."""
        body: dict[str, Any] = {
            "inputs": inputs,
            "outputs": outputs,
        }
        return cast(
            PostSwapResponse, await self._request("POST", "/v1/swap", json=body)
        )

    async def check_state(self, *, Ys: list[str]) -> PostCheckStateResponse:
        """Check if proofs are spent or pending."""
        body: dict[str, Any] = {"Ys": Ys}
        return cast(
            PostCheckStateResponse,
            await self._request("POST", "/v1/checkstate", json=body),
        )

    async def restore(self, *, outputs: list[BlindedMessage]) -> PostRestoreResponse:
        """Restore proofs from blinded messages."""
        body: dict[str, Any] = {"outputs": outputs}
        return cast(
            PostRestoreResponse, await self._request("POST", "/v1/restore", json=body)
        )
