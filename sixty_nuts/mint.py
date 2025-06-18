"""Cashu Mint API client wrapper."""

from __future__ import annotations

from typing import TypedDict, cast, Any, Literal

import httpx

from .crypto import BlindedMessage, BlindSignature as BlindedSignature, Proof


# ──────────────────────────────────────────────────────────────────────────────
# Type definitions based on NUT-01 and OpenAPI spec
# ──────────────────────────────────────────────────────────────────────────────

# NUT-01 compliant currency units
CurrencyUnit = Literal[
    "btc", "sat", "msat",           # Bitcoin units
    "usd", "eur", "gbp", "jpy",     # Major fiat (ISO 4217)
    "auth",                         # Authentication unit
    # Add more ISO 4217 codes and stablecoin units as needed
    "usdt", "usdc", "dai",          # Common stablecoins
]


class ProofOptional(TypedDict, total=False):
    """Optional fields for Proof (NUT-00 specification)."""

    Y: str  # Optional for P2PK (hex string)
    witness: str  # Optional witness data
    dleq: dict[str, Any]  # Optional DLEQ proof (NUT-12)


# Full Proof type combining required and optional fields
class ProofComplete(Proof, ProofOptional):
    """Complete Proof type with both required and optional fields."""

    pass


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


# NUT-01 compliant keyset definitions
class Keyset(TypedDict):
    """Individual keyset per NUT-01 specification."""
    
    id: str  # keyset identifier
    unit: CurrencyUnit  # currency unit
    keys: dict[str, str]  # amount -> compressed secp256k1 pubkey mapping


class KeysResponse(TypedDict):
    """NUT-01 compliant mint keys response from GET /v1/keys."""
    
    keysets: list[Keyset]


class KeysetInfo(TypedDict):
    """Extended keyset information for /v1/keysets endpoint."""
    
    id: str
    unit: CurrencyUnit
    active: bool
    input_fee_ppk: int  # input fee in parts per thousand


class KeysetsResponse(TypedDict):
    """Active keysets response from GET /v1/keysets."""

    keysets: list[KeysetInfo]


class PostMintQuoteRequest(TypedDict, total=False):
    """Request body for mint quote."""

    unit: CurrencyUnit
    amount: int
    description: str
    pubkey: str  # for P2PK


class PostMintQuoteResponse(TypedDict):
    """Mint quote response."""

    # Required fields
    quote: str  # quote id
    request: str  # bolt11 invoice
    amount: int
    unit: CurrencyUnit
    state: str  # "UNPAID", "PAID", "ISSUED"

    # Optional fields - use TypedDict with total=False for these if needed
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

    unit: CurrencyUnit
    request: str  # bolt11 invoice
    options: dict[str, Any]


class PostMeltQuoteResponse(TypedDict):
    """Melt quote response."""

    # Required fields
    quote: str
    amount: int
    fee_reserve: int
    unit: CurrencyUnit

    # Optional fields
    request: str
    paid: bool
    state: str
    expiry: int
    payment_preimage: str
    change: list[BlindedSignature]


class PostMeltRequest(TypedDict, total=False):
    """Request body for melting tokens."""

    quote: str
    inputs: list[ProofComplete]
    outputs: list[BlindedMessage]  # for change


class PostSwapRequest(TypedDict):
    """Request body for swapping proofs."""

    inputs: list[ProofComplete]
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


class InvalidKeysetError(MintError):
    """Raised when keyset structure is invalid per NUT-01."""


class Mint:
    """Async HTTP client wrapper for Cashu mint API with NUT-01 compliance."""

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

    def _validate_keyset(self, keyset: dict[str, Any]) -> bool:
        """Validate keyset structure per NUT-01 specification.
        
        Args:
            keyset: Keyset dictionary to validate
            
        Returns:
            True if valid, False otherwise
        """
        # Check required fields
        required_fields = ["id", "unit", "keys"]
        if not all(field in keyset for field in required_fields):
            return False
        
        # Validate keys structure (amount -> pubkey mapping)
        keys = keyset.get("keys", {})
        if not isinstance(keys, dict):
            return False
            
        # Validate each public key is compressed secp256k1 format
        for amount_str, pubkey in keys.items():
            if not self._is_valid_compressed_pubkey(pubkey):
                return False
                
        return True
    
    def _is_valid_compressed_pubkey(self, pubkey: str) -> bool:
        """Validate that pubkey is a valid compressed secp256k1 public key.
        
        Args:
            pubkey: Hex-encoded public key string
            
        Returns:
            True if valid compressed secp256k1 pubkey
        """
        try:
            # Compressed secp256k1 pubkeys are 33 bytes (66 hex chars)
            if len(pubkey) != 66:
                return False
            
            # Must start with 02 or 03 for compressed format
            if not pubkey.startswith(('02', '03')):
                return False
                
            # Verify it's valid hex
            bytes.fromhex(pubkey)
            return True
        except (ValueError, TypeError):
            return False

    def _validate_keys_response(self, response: dict[str, Any]) -> KeysResponse:
        """Validate and cast response to NUT-01 compliant KeysResponse.
        
        Args:
            response: Raw response from mint
            
        Returns:
            Validated KeysResponse
            
        Raises:
            InvalidKeysetError: If response doesn't match NUT-01 specification
        """
        if "keysets" not in response:
            raise InvalidKeysetError("Response missing 'keysets' field")
            
        keysets = response["keysets"]
        if not isinstance(keysets, list):
            raise InvalidKeysetError("'keysets' must be a list")
            
        for i, keyset in enumerate(keysets):
            if not self._validate_keyset(keyset):
                raise InvalidKeysetError(f"Invalid keyset at index {i}")
                
        return cast(KeysResponse, response)

    # ───────────────────────── Info & Keys ─────────────────────────────────

    async def get_info(self) -> MintInfo:
        """Get mint information."""
        return cast(MintInfo, await self._request("GET", "/v1/info"))

    async def get_keys(self, keyset_id: str | None = None) -> KeysResponse:
        """Get mint public keys for a keyset (or newest if not specified).
        
        Implements NUT-01 specification for mint public key exchange.
        
        Args:
            keyset_id: Optional specific keyset ID to retrieve
            
        Returns:
            NUT-01 compliant KeysResponse with validated structure
        """
        path = f"/v1/keys/{keyset_id}" if keyset_id else "/v1/keys"
        response = await self._request("GET", path)
        return self._validate_keys_response(response)

    async def get_keysets(self) -> KeysetsResponse:
        """Get all active keyset IDs."""
        return cast(KeysetsResponse, await self._request("GET", "/v1/keysets"))

    # ───────────────────────── Minting (receive) ─────────────────────────────────

    async def create_mint_quote(
        self,
        *,
        unit: CurrencyUnit,
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
        unit: CurrencyUnit,
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
        inputs: list[ProofComplete],
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
        inputs: list[ProofComplete],
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

    # ───────────────────────── Keyset Validation ─────────────────────────────────

    def validate_keyset(self, keyset: dict) -> bool:
        """Validate keyset structure according to NUT-02 specification.
        
        Args:
            keyset: Keyset dictionary to validate
            
        Returns:
            True if keyset is valid, False otherwise
            
        Example:
            keyset = {"id": "00a1b2c3d4e5f6g7", "unit": "sat", "active": True}
            is_valid = mint.validate_keyset(keyset)
        """
        # Check required fields
        required_fields = ["id", "unit", "active"]
        for field in required_fields:
            if field not in keyset:
                return False
        
        # Validate keyset ID format (hex string, 16 characters)
        keyset_id = keyset["id"]
        if not isinstance(keyset_id, str) or len(keyset_id) != 16:
            return False
        
        try:
            # Verify it's valid hex
            int(keyset_id, 16)
        except ValueError:
            return False
        
        # Validate unit
        valid_units = ["sat", "msat", "usd", "eur", "btc"]  # Common units
        if keyset["unit"] not in valid_units:
            return False
        
        # Validate active flag
        if not isinstance(keyset["active"], bool):
            return False
        
        # Validate fee structure if present
        if "input_fee_ppk" in keyset:
            fee_value = keyset["input_fee_ppk"]
            try:
                fee_int = int(fee_value)
                if fee_int < 0:
                    return False
            except (ValueError, TypeError):
                return False
        
        # Validate keys structure if present
        if "keys" in keyset:
            keys = keyset["keys"]
            if not isinstance(keys, dict):
                return False
            
            # Each key should map amount string to pubkey hex string
            for amount_str, pubkey_hex in keys.items():
                try:
                    # Amount should be parseable as positive integer
                    amount = int(amount_str)
                    if amount <= 0:
                        return False
                except ValueError:
                    return False
                
                # Pubkey should be valid hex string (33 bytes = 66 hex chars)
                if not isinstance(pubkey_hex, str) or len(pubkey_hex) != 66:
                    return False
                
                try:
                    int(pubkey_hex, 16)
                except ValueError:
                    return False
        
        return True

    def validate_keysets_response(self, response: dict) -> bool:
        """Validate a complete keysets response structure.
        
        Args:
            response: Response dictionary from /v1/keysets endpoint
            
        Returns:
            True if response is valid, False otherwise
        """
        if "keysets" not in response:
            return False
        
        keysets = response["keysets"]
        if not isinstance(keysets, list):
            return False
        
        # Validate each keyset
        for keyset in keysets:
            if not isinstance(keyset, dict):
                return False
            if not self.validate_keyset(keyset):
                return False
        
        return True

    async def get_validated_keysets(self) -> KeysetsResponse:
        """Get keysets with validation according to NUT-02.
        
        Returns:
            Validated keysets response
            
        Raises:
            MintError: If response is invalid or validation fails
        """
        response = await self.get_keysets()
        
        if not self.validate_keysets_response(dict(response)):
            raise MintError("Invalid keysets response from mint")
        
        return response
