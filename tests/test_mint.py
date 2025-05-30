#!/usr/bin/env python3
"""Test Mint API client."""

import pytest
import httpx
from unittest.mock import AsyncMock, Mock
from sixty_nuts.mint import Mint, MintError, BlindedMessage, Proof


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
    return client


class TestMint:
    """Test cases for Mint class."""

    async def test_mint_initialization(self):
        """Test mint initialization."""
        mint = Mint("https://testnut.cashu.space")
        assert mint.url == "https://testnut.cashu.space"
        assert mint._owns_client is True
        await mint.aclose()

        # Test with custom client
        client = httpx.AsyncClient()
        mint = Mint("https://testnut.cashu.space", client=client)
        assert mint._owns_client is False
        await client.aclose()

    async def test_get_info(self, mint, mock_client):
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

    async def test_get_keys(self, mint, mock_client):
        """Test get_keys method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "keysets": [
                {
                    "id": "00ad268c4d1f5826",
                    "unit": "sat",
                    "keys": {"1": "02abc...", "2": "02def..."},
                }
            ]
        }

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        # Test without keyset_id
        keys = await mint.get_keys()
        assert len(keys["keysets"]) == 1
        assert keys["keysets"][0]["id"] == "00ad268c4d1f5826"

        # Test with keyset_id
        keys = await mint.get_keys("00ad268c4d1f5826")
        mock_client.request.assert_called_with(
            "GET",
            "https://testnut.cashu.space/v1/keys/00ad268c4d1f5826",
            json=None,
            params=None,
        )

    async def test_create_mint_quote(self, mint, mock_client):
        """Test create_mint_quote method."""
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

    async def test_mint_tokens(self, mint, mock_client):
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

    async def test_swap(self, mint, mock_client):
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

        inputs = [Proof(id="00ad268c4d1f5826", amount=3, secret="secret", C="02old...")]
        outputs = [
            BlindedMessage(amount=1, id="00ad268c4d1f5826", B_="blind1"),
            BlindedMessage(amount=2, id="00ad268c4d1f5826", B_="blind2"),
        ]

        result = await mint.swap(inputs=inputs, outputs=outputs)
        assert len(result["signatures"]) == 2

    async def test_error_handling(self, mint, mock_client):
        """Test error handling."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client.request.return_value = mock_response
        mint.client = mock_client

        with pytest.raises(MintError, match="Mint returned 400: Bad Request"):
            await mint.get_info()


@pytest.mark.asyncio
async def test_mint_lifecycle():
    """Test the full lifecycle of mint operations."""
    # This would be an integration test with a real mint
    # For now, just test that the client can be created and closed
    mint = Mint("https://testnut.cashu.space")
    assert mint.url == "https://testnut.cashu.space"
    await mint.aclose()
