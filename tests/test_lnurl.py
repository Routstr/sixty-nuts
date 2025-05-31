"""Tests for LNURL functionality."""

import pytest
from unittest.mock import AsyncMock, patch
import httpx

from sixty_nuts.lnurl import (
    decode_lnurl,
    get_lnurl_data,
    get_lnurl_invoice,
    LNURLError,
    LNURLData,
)


class TestDecodeLNURL:
    """Test LNURL decoding functionality."""

    @pytest.mark.asyncio
    async def test_decode_lightning_address(self) -> None:
        """Test decoding Lightning Address format."""
        result = await decode_lnurl("user@example.com")
        assert result == "https://example.com/.well-known/lnurlp/user"

    @pytest.mark.asyncio
    async def test_decode_lightning_prefix(self) -> None:
        """Test decoding with lightning: prefix."""
        result = await decode_lnurl("lightning:user@example.com")
        assert result == "https://example.com/.well-known/lnurlp/user"

    @pytest.mark.asyncio
    async def test_decode_direct_https(self) -> None:
        """Test direct HTTPS URL passes through."""
        url = "https://lnurl.example.com/pay/123"
        result = await decode_lnurl(url)
        assert result == url

    @pytest.mark.asyncio
    async def test_decode_invalid_direct_url(self) -> None:
        """Test non-HTTPS direct URL raises error."""
        with pytest.raises(LNURLError, match="Direct LNURL must use HTTPS"):
            await decode_lnurl("http://example.com/pay")

    @pytest.mark.asyncio
    async def test_decode_bech32_missing_library(self) -> None:
        """Test bech32 decoding when library is missing."""
        with patch("sixty_nuts.lnurl.bech32_decode", None):
            with pytest.raises(ImportError, match="bech32 library is required"):
                await decode_lnurl(
                    "lnurl1dp68gurn8ghj7um9wfmxjcm99e3k7mf0v9cxj0m385ekvcenxc6r2c35xvukxefcv5mkvv34x5ekzd3ev56nyd3hxqurzepexejxxepnxscrvwfnv9nxzcn9xq6xyefhvgcxxcmyxymnserxfq5fns"
                )


class TestGetLNURLData:
    """Test fetching LNURL data."""

    @pytest.mark.asyncio
    async def test_get_lnurl_data_success(self) -> None:
        """Test successful LNURL data fetch."""
        mock_response = {
            "tag": "payRequest",
            "callback": "https://example.com/lnurl/callback",
            "minSendable": 1000,
            "maxSendable": 1000000000,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            result = await get_lnurl_data("user@example.com")

            assert result["callback_url"] == "https://example.com/lnurl/callback"
            assert result["min_sendable"] == 1000
            assert result["max_sendable"] == 1000000000

    @pytest.mark.asyncio
    async def test_get_lnurl_data_invalid_tag(self) -> None:
        """Test error when tag is not payRequest."""
        mock_response = {
            "tag": "withdrawRequest",
            "callback": "https://example.com/lnurl/callback",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            with pytest.raises(LNURLError, match="Invalid LNURL tag"):
                await get_lnurl_data("user@example.com")

    @pytest.mark.asyncio
    async def test_get_lnurl_data_missing_callback(self) -> None:
        """Test error when callback is missing."""
        mock_response = {
            "tag": "payRequest",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            with pytest.raises(LNURLError, match="missing callback URL"):
                await get_lnurl_data("user@example.com")


class TestGetLNURLInvoice:
    """Test fetching Lightning invoice from LNURL."""

    @pytest.mark.asyncio
    async def test_get_invoice_success(self) -> None:
        """Test successful invoice fetch."""
        mock_response = {
            "pr": "lnbc100n1...",
            "routes": [],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            invoice, data = await get_lnurl_invoice(
                "https://example.com/callback", 100000
            )

            assert invoice == "lnbc100n1..."
            assert data == mock_response

            # Check correct parameters were sent
            mock_client.return_value.__aenter__.return_value.get.assert_called_with(
                "https://example.com/callback",
                params={"amount": 100000},
                follow_redirects=True,
                timeout=10,
            )

    @pytest.mark.asyncio
    async def test_get_invoice_error_response(self) -> None:
        """Test error response from LNURL service."""
        mock_response = {
            "reason": "Amount too low",
            "status": "ERROR",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            with pytest.raises(LNURLError, match="Amount too low"):
                await get_lnurl_invoice("https://example.com/callback", 100)

    @pytest.mark.asyncio
    async def test_get_invoice_invalid_response(self) -> None:
        """Test invalid response without pr field."""
        mock_response = {
            "invalid": "response",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = lambda: mock_response
            mock_response_obj.raise_for_status = AsyncMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response_obj
            )

            with pytest.raises(LNURLError, match="Invalid LNURL invoice response"):
                await get_lnurl_invoice("https://example.com/callback", 100000)
