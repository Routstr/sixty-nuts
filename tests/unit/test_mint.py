#!/usr/bin/env python3
"""Test Mint API client with NUT-01 compliance."""

import pytest
import httpx
from unittest.mock import AsyncMock, Mock
from sixty_nuts.mint import (
    Mint,
    MintError,
    InvalidKeysetError,
    BlindedMessage,
    CurrencyUnit,
)


@pytest.fixture
async def mint():
    """Create a mint instance for testing."""
    mint = Mint("https://testnut.cashu.space")
    yield mint
    await mint.aclose()


@pytest.fixture
async def mock_client():
    """Create a mock HTTP client."""
    client = AsyncMock(spec=httpx.AsyncClient)
    yield client


class TestMint:
    """Test cases for Mint class with NUT-01 compliance."""

    async def test_mint_initialization(self) -> None:
        """Test mint initialization."""
        mint = Mint("https://testnut.cashu.space")
        assert mint.url == "https://testnut.cashu.space"
        await mint.aclose()

        client = httpx.AsyncClient()
        mint = Mint("https://testnut.cashu.space")
        await client.aclose()

    async def test_get_info(self, mint, mock_client) -> None:
        """Test get_info method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "Test Mint",
            "pubkey": "02abc...",
            "version": "1.0.0",
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        info = await mint.get_info()
        assert info["name"] == "Test Mint"
        assert info["version"] == "1.0.0"

        mock_client.request.assert_called_once_with(
            "GET", "https://testnut.cashu.space/v1/info", json=None, params=None
        )

    async def test_get_keys_nut01_compliant(self, mint, mock_client) -> None:
        """Test get_keys method with NUT-01 compliant response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "keysets": [
                {
                    "id": "00ad268c4d1f5826",
                    "unit": "sat",
                    "keys": {
                        "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
                        "2": "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
                        "4": "02f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",
                    },
                }
            ]
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        keysets = await mint.get_active_keysets()
        assert len(keysets) == 1
        assert keysets[0]["id"] == "00ad268c4d1f5826"
        assert keysets[0]["unit"] == "sat"
        assert "keys" in keysets[0]

        keyset = await mint.get_keyset("00ad268c4d1f5826")
        mock_client.request.assert_called_with(
            "GET",
            "https://testnut.cashu.space/v1/keys/00ad268c4d1f5826",
            json=None,
            params=None,
        )
        assert keyset["id"] == "00ad268c4d1f5826"
        assert keyset["unit"] == "sat"

    async def test_get_keys_invalid_response(self, mint, mock_client) -> None:
        """Test get_keys with invalid response structure."""
        mock_response = Mock()
        mock_response.status_code = 200

        mock_response.json.return_value = {"invalid": "response"}
        mock_client.request.return_value = mock_response
        mint.client = mock_client

        with pytest.raises(
            InvalidKeysetError, match="Response missing 'keysets' field"
        ):
            await mint.get_active_keysets()

    async def test_get_keys_invalid_keyset_structure(self, mint, mock_client) -> None:
        """Test get_keys with invalid keyset structure."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "keysets": [
                {
                    "id": "00ad268c4d1f5826",
                }
            ]
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        with pytest.raises(InvalidKeysetError, match="Invalid keyset at index 0"):
            await mint.get_active_keysets()

    async def test_validate_compressed_pubkey(self, mint) -> None:
        """Test compressed public key validation."""
        # Valid compressed pubkeys
        valid_pubkeys = [
            "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
            "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
            "03f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",
        ]

        for pubkey in valid_pubkeys:
            assert mint._is_valid_compressed_pubkey(pubkey) is True

        # Invalid pubkeys
        invalid_pubkeys = [
            "invalid",  # Not hex
            "0279be667ef9dcbbac55a06295ce870",  # Too short
            "0479be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798abc",  # Too long, uncompressed prefix
            "0179be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",  # Invalid prefix
            "",  # Empty
        ]

        for pubkey in invalid_pubkeys:
            assert mint._is_valid_compressed_pubkey(pubkey) is False

    async def test_currency_units_supported(self, mint, mock_client) -> None:
        """Test that all NUT-01 currency units are supported."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "quote": "quote_id_123",
            "request": "lnbc100n1...",
            "amount": 100,
            "unit": "sat",
            "state": "UNPAID",
            "paid": False,
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        # Test all supported currency units
        currency_units: list[CurrencyUnit] = [
            "btc",
            "sat",
            "msat",
            "usd",
            "eur",
            "gbp",
            "jpy",
            "auth",
            "usdt",
            "usdc",
            "dai",
        ]

        for unit in currency_units:
            quote = await mint.create_mint_quote(unit=unit, amount=100)
            assert quote["quote"] == "quote_id_123"

    async def test_create_mint_quote(self, mint, mock_client) -> None:
        """Test create_mint_quote method with currency unit validation."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "quote": "quote_id_123",
            "request": "lnbc100n1...",
            "amount": 100,
            "unit": "sat",
            "state": "UNPAID",
            "paid": False,
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        quote = await mint.create_mint_quote(unit="sat", amount=100)
        assert quote["quote"] == "quote_id_123"
        assert quote["amount"] == 100
        assert quote["paid"] is False

        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args[0] == (
            "POST",
            "https://testnut.cashu.space/v1/mint/quote/bolt11",
        )
        assert call_args[1]["json"]["unit"] == "sat"
        assert call_args[1]["json"]["amount"] == 100

    async def test_mint_tokens(self, mint, mock_client) -> None:
        """Test mint method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "signatures": [
                {"id": "00ad268c4d1f5826", "amount": 1, "C_": "02abc..."},
                {"id": "00ad268c4d1f5826", "amount": 2, "C_": "02def..."},
            ]
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        outputs = [
            BlindedMessage(amount=1, id="00ad268c4d1f5826", B_="blind1"),
            BlindedMessage(amount=2, id="00ad268c4d1f5826", B_="blind2"),
        ]

        result = await mint.mint(quote="quote_id_123", outputs=outputs)
        assert len(result["signatures"]) == 2
        assert result["signatures"][0]["amount"] == 1

    async def test_swap(self, mint, mock_client) -> None:
        """Test swap method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "signatures": [
                {"id": "00ad268c4d1f5826", "amount": 1, "C_": "02new1..."},
                {"id": "00ad268c4d1f5826", "amount": 2, "C_": "02new2..."},
            ]
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        inputs = [
            {"id": "00ad268c4d1f5826", "amount": 3, "secret": "secret", "C": "02old..."}
        ]
        outputs = [
            BlindedMessage(amount=1, id="00ad268c4d1f5826", B_="blind1"),
            BlindedMessage(amount=2, id="00ad268c4d1f5826", B_="blind2"),
        ]

        result = await mint.swap(inputs=inputs, outputs=outputs)
        assert len(result["signatures"]) == 2

    async def test_error_handling(self, mint, mock_client) -> None:
        """Test error handling."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        with pytest.raises(MintError, match="Mint returned 400: Bad Request"):
            await mint.get_info()

    async def test_keyset_validation_comprehensive(self, mint) -> None:
        """Test comprehensive keyset validation."""
        # Valid keyset
        valid_keyset = {
            "id": "00ad268c4d1f5826",
            "unit": "sat",
            "keys": {
                "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
                "2": "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
            },
        }
        assert mint._validate_keyset(valid_keyset) is True

        # Missing id field
        invalid_keyset_1 = {
            "unit": "sat",
            "keys": {
                "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
            },
        }
        assert mint._validate_keyset(invalid_keyset_1) is False

        # Invalid pubkey format
        invalid_keyset_2 = {
            "id": "00ad268c4d1f5826",
            "unit": "sat",
            "keys": {"1": "invalid_pubkey"},
        }
        assert mint._validate_keyset(invalid_keyset_2) is False

        # Keys not a dict
        invalid_keyset_3 = {
            "id": "00ad268c4d1f5826",
            "unit": "sat",
            "keys": "not_a_dict",
        }
        assert mint._validate_keyset(invalid_keyset_3) is False


@pytest.mark.asyncio
async def test_mint_lifecycle() -> None:
    """Test the full lifecycle of mint operations with NUT-01 compliance."""
    # This would be an integration test with a real mint
    # For now, just test that the client can be created and closed
    mint = Mint("https://testnut.cashu.space")
    assert mint.url == "https://testnut.cashu.space"
    await mint.aclose()


class TestNUT01Compliance:
    """Specific tests for NUT-01 specification compliance."""

    async def test_keys_response_structure(self, mint, mock_client) -> None:
        """Test that keys response follows NUT-01 structure exactly."""
        # NUT-01 compliant response
        nut01_response = {
            "keysets": [
                {
                    "id": "00ad268c4d1f5826",
                    "unit": "sat",
                    "keys": {
                        "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
                        "2": "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
                        "4": "02f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",
                        "8": "03e493dbf1c10d80f3581e4904930b1404cc6c13900ee0758474fa94abe8c4cd13",
                    },
                }
            ]
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = nut01_response

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        keysets = await mint.get_active_keysets()

        assert isinstance(keysets, list)
        assert len(keysets) > 0

        keyset = keysets[0]
        assert "id" in keyset
        assert "unit" in keyset
        assert "keys" in keyset
        assert isinstance(keyset["keys"], dict)

        for amount, pubkey in keyset["keys"].items():
            assert mint._is_valid_compressed_pubkey(pubkey)

    async def test_currency_unit_validation(self) -> None:
        """Test that all NUT-01 required currency units are supported."""
        from sixty_nuts.mint import CurrencyUnit
        from typing import get_args

        # Get all currency units from the type
        supported_units = get_args(CurrencyUnit)

        # Verify mandatory units are present
        mandatory_units = ["btc", "sat", "msat", "auth"]
        for unit in mandatory_units:
            assert unit in supported_units, f"Mandatory unit '{unit}' not supported"

        # Verify common units are present
        common_units = ["usd", "eur"]
        for unit in common_units:
            assert unit in supported_units, f"Common unit '{unit}' not supported"
