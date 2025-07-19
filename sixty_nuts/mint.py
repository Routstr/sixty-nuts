"""
Cashu Mint API client wrapper."""

from __future__ import annotations

import os
import time
from typing import TypedDict, cast, Any

import httpx

from .types import (
    BlindedMessage,
    BlindedSignature,
    Proof,
    CurrencyUnit,
    MintError,
)
from .lnurl import parse_lightning_invoice_amount


# ──────────────────────────────────────────────────────────────────────────────
# Mint API client
# ──────────────────────────────────────────────────────────────────────────────


class InvalidKeysetError(MintError):
    """Raised when keyset structure is invalid per NUT-01."""


class Mint:
    def __init__(self, url: str) -> None:
        # Normalize URL by removing trailing slashes
        self.url = url.rstrip("/")
        self.client = httpx.AsyncClient()
        self._active_keysets: list[Keyset] = []
        self._currencies: list[CurrencyUnit] = []
        # Exchange rate cache: {cache_key: (rate, timestamp)}
        self._exchange_rate_cache: dict[str, tuple[float, float]] = {}
        self._exchange_rate_cache_ttl = 300  # 5 minutes cache TTL

    async def aclose(self) -> None:
        """Close the HTTP client if we created it."""
        if self.client:
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
        if os.environ.get("MINT_DEBUG", "false").lower() == "true":
            print(f"MINT_DEBUG {method} request to {self.url}{path}")
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
            if not pubkey.startswith(("02", "03")):
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

    def validate_keysets_response(self, response: dict) -> bool:
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

    # ───────────────────────── Denomination Management ─────────────────────────────────

    async def get_denominations_for_currency(self, unit: CurrencyUnit) -> list[int]:
        """Extract denominations for a given currency unit from active keyset.

        Args:
            unit: Currency unit to get denominations for

        Returns:
            Sorted list of denominations (ascending order)
        """
        keysets = await self.get_active_keysets()
        matching_keysets = [ks for ks in keysets if ks["unit"] == unit]

        if not matching_keysets:
            raise MintError(f"No keyset found for unit {unit}")

        keyset = matching_keysets[0]
        denominations = []

        if isinstance(keyset["keys"], dict):
            for amount_str in keyset["keys"]:
                try:
                    amount = int(amount_str)
                    denominations.append(amount)
                except (ValueError, TypeError):
                    continue

        return sorted(denominations)

    @staticmethod
    def calculate_optimal_split(
        amount: int, available_denominations: list[int]
    ) -> dict[int, int]:
        """Calculate optimal denomination breakdown for an amount.

        Uses a greedy algorithm to minimize the number of tokens while
        preferring the available denominations from the keyset.

        Args:
            amount: Total amount to split
            available_denominations: List of available denominations (sorted)

        Returns:
            Dict of denomination -> count
        """
        if not available_denominations:
            return Mint._default_split(amount)

        denominations: dict[int, int] = {}
        remaining = amount

        for denom in sorted(available_denominations, reverse=True):
            if remaining >= denom:
                count = remaining // denom
                denominations[denom] = count
                remaining -= denom * count

        if remaining > 0 and available_denominations:
            smallest = min(available_denominations)
            if smallest in denominations:
                denominations[smallest] += 1
            else:
                denominations[smallest] = 1

        return denominations

    @staticmethod
    def _default_split(amount: int) -> dict[int, int]:
        """Default split using powers of 2."""
        denominations: dict[int, int] = {}
        remaining = amount

        for denom in [
            16384,
            8192,
            4096,
            2048,
            1024,
            512,
            256,
            128,
            64,
            32,
            16,
            8,
            4,
            2,
            1,
        ]:
            if remaining >= denom:
                count = remaining // denom
                denominations[denom] = count
                remaining -= denom * count

        return denominations

    async def validate_denominations_for_currency(
        self, unit: CurrencyUnit, requested_denominations: dict[int, int]
    ) -> tuple[bool, str | None]:
        """Validate if requested denominations are available for the currency.

        Args:
            unit: Currency unit to validate for
            requested_denominations: Dict of denomination -> count

        Returns:
            Tuple of (is_valid, error_message)
        """
        available_denoms = await self.get_denominations_for_currency(unit)
        available_set = set(available_denoms)

        for denom in requested_denominations:
            if denom not in available_set:
                return False, f"Denomination {denom} not available for unit {unit}"

        return True, None

    @staticmethod
    def merge_denominations(denominations_list: list[dict[int, int]]) -> dict[int, int]:
        """Merge multiple denomination dicts into one.

        Args:
            denominations_list: List of denomination dicts to merge

        Returns:
            Merged denomination dict
        """
        merged: dict[int, int] = {}

        for denoms in denominations_list:
            for denom, count in denoms.items():
                if denom in merged:
                    merged[denom] += count
                else:
                    merged[denom] = count

        return merged

    # ───────────────────────── Info & Keys ─────────────────────────────────

    async def get_info(self) -> MintInfo:
        """Get mint information."""
        return cast(MintInfo, await self._request("GET", "/v1/info"))

    async def get_active_keysets(self) -> list[Keyset]:
        """Get mint public keys for a keyset (or newest if not specified).

        Implements NUT-01 specification for mint public key exchange.

        Args:
            keyset_id: Optional specific keyset ID to retrieve

        Returns:
            NUT-01 compliant KeysResponse with validated structure
        """
        if self._active_keysets:
            return self._active_keysets
        response = await self._request("GET", "/v1/keys")
        keysets = self._validate_keys_response(response)["keysets"]
        self._active_keysets = [Keyset(**keyset) for keyset in keysets]
        self._currencies = [keyset["unit"] for keyset in self._active_keysets]
        return self._active_keysets

    async def get_keyset(self, id: str) -> Keyset:
        """Get keyset details."""
        response = await self._request("GET", f"/v1/keys/{id}")
        keyset = self._validate_keys_response(response)["keysets"][0]
        return Keyset(**keyset)

    async def get_keysets_info(self) -> list[KeysetInfo]:
        """Get all active keyset IDs."""
        response = await self._request("GET", "/v1/keysets")
        return cast(list[KeysetInfo], response["keysets"])

    async def get_currencies(self) -> list[CurrencyUnit]:
        return self._currencies or [
            keyset["unit"] for keyset in (await self.get_active_keysets())
        ]

    async def mint_exchange_rate(self, unit: CurrencyUnit) -> float:
        """Get exchange rate for converting a currency unit to satoshis.

        Returns how many satoshis equal one unit of the given currency.
        For example: USD returns ~3000 (meaning 1 USD = 3000 sats)

        NOTE: This does not include mint fees. The actual cost to mint will be higher
        due to both Lightning network fees and mint fees. Consider adding a buffer
        (e.g., 1-2%) when using these rates for calculations.
        """
        # TODO: include mint fee in exchange rate calculation
        if unit == "sat":
            return 1
        elif unit == "msat":
            return 1000
        elif unit in await self.get_currencies():
            # Use same cache as melt_exchange_rate (rates should be similar)
            current_time = time.time()
            cache_key = f"mint_{unit}"  # Different cache key for mint rates
            if cache_key in self._exchange_rate_cache:
                rate, timestamp = self._exchange_rate_cache[cache_key]
                if current_time - timestamp < self._exchange_rate_cache_ttl:
                    return rate

            # Cache miss or expired - fetch new rate
            quote = await self.create_mint_quote(amount=1000, unit=unit)
            invoice_amount_sats = parse_lightning_invoice_amount(
                quote["request"], "sat"
            )
            sat_per_base_unit = invoice_amount_sats / 1000

            # Update cache
            self._exchange_rate_cache[cache_key] = (sat_per_base_unit, current_time)

            return sat_per_base_unit
        raise NotImplementedError(f"Exchange rate for {unit} not implemented")

    async def melt_exchange_rate(self, unit: CurrencyUnit) -> float:
        """Get exchange rate for converting a currency unit to satoshis.

        NOTE: The return values for BTC units seem inverted - this may be a bug:
        - Returns 1000 for "sat" (should be 1?)
        - Returns 1 for "msat" (should be 0.001?)

        TODO: Verify and fix the return values for BTC-based units.

        NOTE: This does not include mint fees. The actual proceeds from melting will be lower
        due to both Lightning network fees and mint fees. Consider adding a buffer
        (e.g., 1-2%) when using these rates for calculations.
        """
        # TODO: include mint fee in exchange rate calculation
        PRECISION_FACTOR = 100_000
        if unit == "sat":
            return 1000
        elif unit == "msat":
            return 1
        elif unit in await self.get_currencies():
            # Check cache first
            current_time = time.time()
            if unit in self._exchange_rate_cache:
                rate, timestamp = self._exchange_rate_cache[unit]
                if current_time - timestamp < self._exchange_rate_cache_ttl:
                    return rate

            # Cache miss or expired - fetch new rate
            # TODO: test this
            quote = await self.create_mint_quote(amount=PRECISION_FACTOR, unit="sat")
            melt_quote = await self.create_melt_quote(quote["request"], unit=unit)
            sat_per_base_unit = 1 / (melt_quote["amount"] / PRECISION_FACTOR)

            # Update cache
            self._exchange_rate_cache[unit] = (sat_per_base_unit, current_time)

            return sat_per_base_unit
        raise NotImplementedError(f"Exchange rate for {unit} not implemented")

    # ───────────────────────── Minting (receive) ─────────────────────────────────

    async def create_mint_quote(
        self,
        *,
        amount: int,
        unit: CurrencyUnit | None = None,
        description: str | None = None,
        pubkey: str | None = None,
    ) -> PostMintQuoteResponse:
        """Request a Lightning invoice to mint tokens."""
        if unit is None:
            currencies = await self.get_currencies()
            if "sat" in currencies:
                unit = "sat"
            else:
                unit = currencies[0]

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
        request: str,
        *,
        unit: CurrencyUnit | None = None,
        options: dict[str, Any] | None = None,
    ) -> PostMeltQuoteResponse:
        """Get a quote for paying a Lightning invoice."""
        if unit is None:
            currencies = await self.get_currencies()
            if "sat" in currencies:
                unit = "sat"
            else:
                unit = currencies[0]

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

    # ───────────────────────── Quote Status & Minting ─────────────────────────────────

    async def check_quote_status_and_mint(
        self,
        quote_id: str,
        amount: int | None = None,
        *,
        minted_quotes: set[str],
    ) -> tuple[dict[str, object], list[dict] | None]:
        """Check whether a quote has been paid and mint proofs if so.

        Args:
            quote_id: Quote ID to check
            amount: Expected amount (if not available in quote status)
            minted_quotes: Set of already minted quote IDs to avoid double-minting
            mint_url: Mint URL to include in proof metadata

        Returns:
            Tuple of (quote_status, new_proofs_or_none)
        """
        from .crypto import (
            create_blinded_messages_for_amount,
            get_mint_pubkey_for_amount,
            unblind_signature,
        )
        from coincurve import PublicKey

        # Check quote status
        quote_status = await self.get_mint_quote(quote_id)

        if quote_status.get("paid") and quote_status.get("state") == "PAID":
            # Check if we've already minted for this quote
            if quote_id in minted_quotes:
                return dict(quote_status), None

            # Mark this quote as being minted
            minted_quotes.add(quote_id)

            # Get amount from quote_status or use provided amount
            mint_amount = quote_status.get("amount", amount)
            if mint_amount is None:
                raise ValueError(
                    "Amount not available in quote status and not provided"
                )

            # Get the quote's unit
            quote_unit = quote_status.get("unit")

            # Get active keyset for the quote's unit
            keysets_info = await self.get_keysets_info()
            keysets = [
                keyset
                for keyset in keysets_info
                if keyset.get("active", True) and keyset.get("unit") == quote_unit
            ]

            # Filter for active keysets with the quote's unit
            matching_keysets = [
                ks
                for ks in keysets
                if ks.get("active", True) and ks.get("unit") == quote_unit
            ]

            if not matching_keysets:
                raise MintError(f"No active keysets found for unit '{quote_unit}'")

            keyset_id_active = matching_keysets[0]["id"]

            # Create blinded messages for the amount
            outputs, secrets, blinding_factors = create_blinded_messages_for_amount(
                mint_amount, keyset_id_active
            )

            # Mint tokens
            mint_resp = await self.mint(quote=quote_id, outputs=outputs)

            # Get mint public key for unblinding
            keyset = await self.get_keyset(keyset_id_active)

            if not (mint_keys := keyset["keys"]):
                raise MintError("Could not find mint keys")

            # Convert to proofs
            new_proofs: list[dict] = []
            for i, sig in enumerate(mint_resp["signatures"]):
                # Get the public key for this amount
                amount_val = sig["amount"]
                mint_pubkey = get_mint_pubkey_for_amount(mint_keys, amount_val)
                if not mint_pubkey:
                    raise MintError(
                        f"Could not find mint public key for amount {amount_val}"
                    )

                # Unblind the signature
                C_ = PublicKey(bytes.fromhex(sig["C_"]))
                r = bytes.fromhex(blinding_factors[i])
                C = unblind_signature(C_, r, mint_pubkey)

                new_proofs.append(
                    {
                        "id": sig["id"],
                        "amount": sig["amount"],
                        "secret": secrets[i],
                        "C": C.format(compressed=True).hex(),
                        "mint": self.url,
                        "unit": quote_unit,  # Add the unit from the quote
                    }
                )

            return dict(quote_status), new_proofs

        return dict(quote_status), None

    # ───────────────────────── Keyset Validation ─────────────────────────────────

    def validate_keyset(self, keyset: dict) -> bool:
        """Validate keyset structure according to NUT-02 specification.

        Args:
            keyset: Keyset dictionary to validate

        Returns:
            True if keyset is valid, False otherwise

        Example:
            keyset = {"id": "00a1b2c3d4e5f6a7", "unit": "sat", "active": True}
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


# ──────────────────────────────────────────────────────────────────────────────
# Mint Environment
# ──────────────────────────────────────────────────────────────────────────────


# Popular public mints for user selection
POPULAR_MINTS = [
    # "https://mint.routstr.com"  # coming soon
    "https://mint.minibits.cash/Bitcoin",
    "https://mint.cubabitcoin.org",
    "https://stablenut.umint.cash",
    "https://mint.macadamia.cash",
]


def get_mints_from_env() -> list[str]:
    """Get mint URLs from environment variable or .env file.

    Expected format: comma-separated URLs
    Example: CASHU_MINTS="https://mint1.com,https://mint2.com"

    Priority order:
    1. Environment variable CASHU_MINTS
    2. .env file in current working directory

    Returns:
        List of mint URLs from environment or .env file, empty list if not set
    """
    # First check environment variable
    env_mints = os.getenv("CASHU_MINTS")
    if env_mints:
        # Split by comma and clean up
        mints = [mint.strip() for mint in env_mints.split(",")]
        # Filter out empty strings and remove duplicates while preserving order
        mints = list(dict.fromkeys(mint for mint in mints if mint))
        return mints

    # Then check .env file in current working directory
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("CASHU_MINTS="):
                    # Extract value after the equals sign
                    value = line.split("=", 1)[1]
                    # Remove quotes if present
                    value = value.strip("\"'")
                    if value:
                        # Split by comma and clean up
                        mints = [mint.strip() for mint in value.split(",")]
                        # Filter out empty strings and remove duplicates while preserving order
                        mints = list(dict.fromkeys(mint for mint in mints if mint))
                        return mints
    except Exception:
        # If reading .env file fails, continue
        pass

    return []


def set_mints_in_env(mints: list[str]) -> None:
    """Set mint URLs in .env file for persistent caching.

    Args:
        mints: List of mint URLs to cache
    """
    if not mints:
        return

    from pathlib import Path

    mint_str = ",".join(mints)
    env_file = Path.cwd() / ".env"
    env_line = f'CASHU_MINTS="{mint_str}"\n'

    try:
        if env_file.exists():
            # Check if CASHU_MINTS already exists in the file
            content = env_file.read_text()
            lines = content.splitlines()

            # Look for existing CASHU_MINTS line
            mint_line_found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith("CASHU_MINTS="):
                    # Replace existing CASHU_MINTS line
                    new_lines.append(env_line.rstrip())
                    mint_line_found = True
                else:
                    new_lines.append(line)

            if not mint_line_found:
                # Add new CASHU_MINTS line at the end
                new_lines.append(env_line.rstrip())

            # Write back to file
            env_file.write_text("\n".join(new_lines) + "\n")
        else:
            # Create new .env file
            env_file.write_text(env_line)

    except Exception as e:
        # If writing to .env file fails, fall back to environment variable
        print(f"Warning: Could not write to .env file: {e}")
        print("Falling back to session environment variable")
        os.environ["CASHU_MINTS"] = mint_str


def clear_mints_from_env() -> bool:
    """Clear mint URLs from .env file and environment variable.

    Returns:
        True if mints were cleared, False if none were set
    """
    cleared = False

    # Clear from environment variable
    if "CASHU_MINTS" in os.environ:
        del os.environ["CASHU_MINTS"]
        cleared = True

    # Clear from .env file
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            lines = content.splitlines()

            # Remove CASHU_MINTS line
            new_lines = []
            for line in lines:
                if not line.strip().startswith("CASHU_MINTS="):
                    new_lines.append(line)
                else:
                    cleared = True

            if new_lines:
                # Write back remaining lines
                env_file.write_text("\n".join(new_lines) + "\n")
            else:
                # If file would be empty, remove it
                env_file.unlink()

    except Exception:
        # If clearing from .env file fails, that's okay
        pass

    return cleared


def validate_mint_url(url: str) -> bool:
    """Validate that a mint URL has the correct format.

    Args:
        url: Mint URL to validate

    Returns:
        True if URL appears valid, False otherwise
    """
    if not url:
        return False

    # Basic URL validation - should start with http:// or https://
    if not (url.startswith("http://") or url.startswith("https://")):
        return False

    # Should not end with slash for consistency
    if url.endswith("/"):
        return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Type definitions based on NUT-01 and OpenAPI spec
# ──────────────────────────────────────────────────────────────────────────────


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


class KeysetInfoRequired(TypedDict):
    """Required fields for keyset information."""

    id: str
    unit: CurrencyUnit
    active: bool


class KeysetInfoOptional(TypedDict, total=False):
    """Optional fields for keyset information."""

    input_fee_ppk: int  # input fee in parts per thousand


class KeysetInfo(KeysetInfoRequired, KeysetInfoOptional):
    """Extended keyset information for /v1/keysets endpoint."""

    pass


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
