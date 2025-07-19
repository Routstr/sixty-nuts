"""Integration tests for LNURL functionality against real endpoints."""

from __future__ import annotations

import os
import pytest
import httpx
from typing import Any

from sixty_nuts.lnurl import (
    decode_lnurl,
    get_lnurl_data,
    get_lnurl_invoice,
    LNURLError,
    LNURLData,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)


class TestLNURLIntegration:
    """Integration tests for LNURL functionality with real endpoints."""

    @pytest.mark.asyncio
    async def test_decode_lightning_address(self) -> None:
        """Test decoding Lightning Address format."""
        lnurl: str = "routstr@minibits.cash"
        decoded_url: str = await decode_lnurl(lnurl)

        assert decoded_url == "https://minibits.cash/.well-known/lnurlp/routstr"
        assert decoded_url.startswith("https://")

    @pytest.mark.asyncio
    async def test_decode_lightning_prefix(self) -> None:
        """Test decoding with lightning: prefix."""
        lnurl: str = "lightning:routstr@minibits.cash"
        decoded_url: str = await decode_lnurl(lnurl)

        assert decoded_url == "https://minibits.cash/.well-known/lnurlp/routstr"

    @pytest.mark.asyncio
    async def test_decode_direct_https_url(self) -> None:
        """Test direct HTTPS URL passthrough."""
        direct_url: str = "https://minibits.cash/.well-known/lnurlp/routstr"
        decoded_url: str = await decode_lnurl(direct_url)

        assert decoded_url == direct_url

    @pytest.mark.asyncio
    async def test_decode_invalid_formats(self) -> None:
        """Test error handling for invalid LNURL formats."""
        # Test non-HTTPS direct URL
        with pytest.raises(LNURLError, match="Direct LNURL must use HTTPS"):
            await decode_lnurl("http://example.com/.well-known/lnurlp/user")

        # Test invalid Lightning Address format
        with pytest.raises(LNURLError):
            await decode_lnurl("invalid@format@example.com")

    @pytest.mark.asyncio
    async def test_get_lnurl_data_real_endpoint(self) -> None:
        """Test fetching LNURL data from real Lightning Address."""
        lnurl: str = "routstr@minibits.cash"

        try:
            lnurl_data: LNURLData = await get_lnurl_data(lnurl)

            assert isinstance(lnurl_data["callback_url"], str)
            assert lnurl_data["callback_url"].startswith("https://")
            assert isinstance(lnurl_data["min_sendable"], int)
            assert isinstance(lnurl_data["max_sendable"], int)
            assert lnurl_data["min_sendable"] > 0
            assert lnurl_data["max_sendable"] >= lnurl_data["min_sendable"]

        except httpx.HTTPError as e:
            pytest.skip(f"Network error accessing real endpoint: {e}")
        except LNURLError as e:
            pytest.skip(f"LNURL endpoint error: {e}")

    @pytest.mark.asyncio
    async def test_get_lnurl_data_invalid_endpoint(self) -> None:
        """Test error handling for invalid LNURL endpoints."""
        invalid_lnurl: str = "nonexistent@invalid-domain-that-does-not-exist.com"

        with pytest.raises((httpx.HTTPError, LNURLError)):
            await get_lnurl_data(invalid_lnurl)

    @pytest.mark.asyncio
    async def test_full_lnurl_flow_with_invoice_generation(self) -> None:
        """Test complete LNURL flow: decode -> data -> invoice."""
        lnurl: str = "routstr@minibits.cash"
        amount_msat: int = 1000  # 1 sat

        try:
            # Step 1: Get LNURL data
            lnurl_data: LNURLData = await get_lnurl_data(lnurl)

            # Step 2: Validate amount is within bounds
            assert amount_msat >= lnurl_data["min_sendable"]
            assert amount_msat <= lnurl_data["max_sendable"]

            # Step 3: Generate invoice
            invoice_result = await get_lnurl_invoice(
                lnurl_data["callback_url"], amount_msat
            )
            invoice: str
            response_data: dict[str, Any]
            invoice, response_data = invoice_result

            # Validate invoice response
            assert isinstance(invoice, str)
            assert invoice.startswith("lnbc") or invoice.startswith("lntb")
            assert isinstance(response_data, dict)
            assert "pr" in response_data
            assert response_data["pr"] == invoice

        except httpx.HTTPError as e:
            pytest.skip(f"Network error during LNURL flow: {e}")
        except LNURLError as e:
            pytest.skip(f"LNURL error during flow: {e}")

    @pytest.mark.asyncio
    async def test_lnurl_invoice_amount_validation(self) -> None:
        """Test invoice generation with different amounts."""
        lnurl: str = "routstr@minibits.cash"

        try:
            lnurl_data: LNURLData = await get_lnurl_data(lnurl)

            # Test minimum amount
            min_amount: int = lnurl_data["min_sendable"]
            invoice_min_result = await get_lnurl_invoice(
                lnurl_data["callback_url"], min_amount
            )
            invoice_min: str
            _: dict[str, Any]
            invoice_min, _ = invoice_min_result
            assert isinstance(invoice_min, str)
            assert len(invoice_min) > 0

            # Test amount within range (if range allows)
            if lnurl_data["max_sendable"] > lnurl_data["min_sendable"]:
                mid_amount: int = min(
                    lnurl_data["min_sendable"] + 1000, lnurl_data["max_sendable"]
                )
                invoice_mid_result = await get_lnurl_invoice(
                    lnurl_data["callback_url"], mid_amount
                )
                invoice_mid: str
                invoice_mid, _ = invoice_mid_result
                assert isinstance(invoice_mid, str)
                assert len(invoice_mid) > 0

        except httpx.HTTPError as e:
            pytest.skip(f"Network error during amount validation: {e}")
        except LNURLError as e:
            pytest.skip(f"LNURL error during amount validation: {e}")

    @pytest.mark.asyncio
    async def test_lnurl_invoice_error_handling(self) -> None:
        """Test error handling in invoice generation."""
        lnurl: str = "routstr@minibits.cash"

        try:
            lnurl_data: LNURLData = await get_lnurl_data(lnurl)

            # Test amount too large (if applicable)
            if lnurl_data["max_sendable"] < 100000000000:  # 100k sats
                large_amount: int = lnurl_data["max_sendable"] + 1000

                # Either LNURLError or HTTPError is acceptable for invalid amounts
                with pytest.raises((LNURLError, httpx.HTTPError)):
                    await get_lnurl_invoice(lnurl_data["callback_url"], large_amount)

            # Test amount too small (if applicable)
            if lnurl_data["min_sendable"] > 1:
                small_amount: int = lnurl_data["min_sendable"] - 1

                # Either LNURLError or HTTPError is acceptable for invalid amounts
                with pytest.raises((LNURLError, httpx.HTTPError)):
                    await get_lnurl_invoice(lnurl_data["callback_url"], small_amount)

        except httpx.HTTPError as e:
            pytest.skip(f"Network error accessing endpoint: {e}")
        except LNURLError as e:
            pytest.skip(f"LNURL endpoint error: {e}")

    @pytest.mark.asyncio
    async def test_multiple_lightning_addresses(self) -> None:
        """Test with multiple different Lightning Address providers."""
        test_addresses: list[str] = [
            "routstr@minibits.cash",
            # Add more test addresses if available
        ]

        successful_tests: int = 0

        for address in test_addresses:
            try:
                decoded_url: str = await decode_lnurl(address)
                assert decoded_url.startswith("https://")

                lnurl_data: LNURLData = await get_lnurl_data(address)
                assert isinstance(lnurl_data["callback_url"], str)

                # Test small invoice generation
                amount: int = max(1000, lnurl_data["min_sendable"])  # 1 sat or minimum
                if amount <= lnurl_data["max_sendable"]:
                    invoice_result = await get_lnurl_invoice(
                        lnurl_data["callback_url"], amount
                    )
                    invoice: str
                    _: dict[str, Any]
                    invoice, _ = invoice_result
                    assert isinstance(invoice, str)
                    assert len(invoice) > 0

                successful_tests += 1

            except (httpx.HTTPError, LNURLError) as e:
                # Skip individual failures but continue testing others
                print(f"Skipping {address} due to error: {e}")
                continue

        # Ensure at least one test succeeded
        assert successful_tests > 0, "No Lightning Address tests succeeded"
