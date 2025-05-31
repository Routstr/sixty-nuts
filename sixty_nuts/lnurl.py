"""LNURL functionality for sixty_nuts wallet."""

from __future__ import annotations

from typing import TypedDict
import httpx

try:
    from bech32 import bech32_decode, convertbits  # type: ignore
except ModuleNotFoundError:  # pragma: no cover – allow runtime miss
    bech32_decode = None  # type: ignore
    convertbits = None  # type: ignore


class LNURLData(TypedDict):
    """LNURL payRequest data."""

    callback_url: str
    min_sendable: int  # millisatoshi
    max_sendable: int  # millisatoshi


class LNURLError(Exception):
    """LNURL related errors."""


async def decode_lnurl(lnurl: str) -> str:
    """Decode LNURL to get the actual URL.

    Handles:
    - lightning: prefix
    - user@host format
    - bech32 encoded lnurl
    - direct HTTPS URLs

    Args:
        lnurl: LNURL string in any supported format

    Returns:
        The decoded HTTPS URL

    Raises:
        LNURLError: If the LNURL format is invalid
    """
    # Remove lightning: prefix if present
    if lnurl.startswith("lightning:"):
        lnurl = lnurl[10:]

    # Handle user@host format (Lightning Address)
    if "@" in lnurl and len(lnurl.split("@")) == 2:
        user, host = lnurl.split("@")
        return f"https://{host}/.well-known/lnurlp/{user}"

    # Handle bech32 encoded LNURL
    if lnurl.lower().startswith("lnurl"):
        if bech32_decode is None or convertbits is None:
            raise ImportError(
                "bech32 library is required for LNURL bech32 decoding. "
                "Install it with: pip install bech32"
            )

        try:
            hrp, data = bech32_decode(lnurl)
            if data is None:
                raise LNURLError("Invalid bech32 data in LNURL")

            decoded_data = convertbits(data, 5, 8, False)
            if decoded_data is None:
                raise LNURLError("Failed to convert LNURL bits")

            return bytes(decoded_data).decode("utf-8")
        except Exception as e:
            raise LNURLError(f"Failed to decode LNURL: {e}") from e

    # Assume it's a direct URL
    if not lnurl.startswith("https://"):
        raise LNURLError("Direct LNURL must use HTTPS")

    return lnurl


async def get_lnurl_data(lnurl: str) -> LNURLData:
    """Fetch LNURL payRequest data.

    Args:
        lnurl: LNURL string in any supported format

    Returns:
        LNURLData with callback URL and sendable amounts

    Raises:
        LNURLError: If the LNURL data is invalid
        httpx.HTTPError: If the HTTP request fails
    """
    url = await decode_lnurl(lnurl)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=10)
        response.raise_for_status()

    lnurl_data = response.json()

    # Validate payRequest data
    if lnurl_data.get("tag") != "payRequest":
        raise LNURLError(
            f"Invalid LNURL tag: expected 'payRequest', got '{lnurl_data.get('tag')}'"
        )

    if not isinstance(lnurl_data.get("callback"), str):
        raise LNURLError("Invalid LNURL payRequest: missing callback URL")

    return LNURLData(
        callback_url=lnurl_data["callback"],
        min_sendable=lnurl_data.get("minSendable", 1000),  # Default 1 sat
        max_sendable=lnurl_data.get("maxSendable", 1000000000),  # Default 1000 BTC
    )


async def get_lnurl_invoice(
    callback_url: str, amount_msat: int
) -> tuple[str, dict[str, object]]:
    """Request a Lightning invoice from LNURL callback.

    Args:
        callback_url: The LNURL callback URL
        amount_msat: Amount in millisatoshi

    Returns:
        Tuple of (bolt11_invoice, full_response_data)

    Raises:
        LNURLError: If the response is invalid
        httpx.HTTPError: If the HTTP request fails
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            callback_url,
            params={"amount": amount_msat},
            follow_redirects=True,
            timeout=10,
        )
        response.raise_for_status()

    invoice_data = response.json()

    if "pr" not in invoice_data:
        # Check if there's an error in the response
        if "reason" in invoice_data:
            raise LNURLError(f"LNURL error: {invoice_data['reason']}")
        raise LNURLError(f"Invalid LNURL invoice response: {invoice_data}")

    return invoice_data["pr"], invoice_data
