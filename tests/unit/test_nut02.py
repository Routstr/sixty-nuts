#!/usr/bin/env python3
"""Test NUT-02 Keysets and fees implementation."""

import pytest
from unittest.mock import AsyncMock, Mock
from typing import cast, Any

from sixty_nuts.crypto import derive_keyset_id, validate_keyset_id
from sixty_nuts.mint import Mint
from sixty_nuts.temp import TempWallet
from sixty_nuts.wallet import Proof


class TestKeysetIDDerivation:
    """Test keyset ID derivation according to NUT-02."""

    def test_derive_keyset_id_basic(self) -> None:
        """Test basic keyset ID derivation."""
        keys = {"1": "02abc123", "2": "02def456", "4": "02ghi789"}

        keyset_id = derive_keyset_id(keys)

        # Should be 16 hex characters (8 bytes)
        assert len(keyset_id) == 16
        assert all(c in "0123456789abcdef" for c in keyset_id)

        # Version byte should be 00
        assert keyset_id.startswith("00")

    def test_derive_keyset_id_deterministic(self) -> None:
        """Test that keyset ID derivation is deterministic."""
        keys = {"1": "02abc123", "2": "02def456"}

        id1 = derive_keyset_id(keys)
        id2 = derive_keyset_id(keys)

        assert id1 == id2

    def test_derive_keyset_id_order_independent(self) -> None:
        """Test that key order doesn't affect derivation."""
        keys1 = {"1": "02abc123", "2": "02def456", "4": "02ghi789"}
        keys2 = {"4": "02ghi789", "1": "02abc123", "2": "02def456"}

        id1 = derive_keyset_id(keys1)
        id2 = derive_keyset_id(keys2)

        assert id1 == id2

    def test_derive_keyset_id_different_versions(self) -> None:
        """Test keyset ID derivation with different versions."""
        keys = {"1": "02abc123", "2": "02def456"}

        id_v0 = derive_keyset_id(keys, version=0)
        id_v1 = derive_keyset_id(keys, version=1)

        assert id_v0 != id_v1
        assert id_v0.startswith("00")
        assert id_v1.startswith("01")

    def test_validate_keyset_id_valid(self) -> None:
        """Test keyset ID validation with valid ID."""
        keys = {"1": "02abc123", "2": "02def456"}
        keyset_id = derive_keyset_id(keys)

        assert validate_keyset_id(keyset_id, keys)

    def test_validate_keyset_id_invalid(self) -> None:
        """Test keyset ID validation with invalid ID."""
        keys = {"1": "02abc123", "2": "02def456"}

        assert not validate_keyset_id("invalid_id", keys)
        assert not validate_keyset_id("00112233445566", {"1": "different_key"})

    def test_validate_keyset_id_case_insensitive(self) -> None:
        """Test that keyset ID validation is case insensitive."""
        keys = {"1": "02abc123", "2": "02def456"}
        keyset_id = derive_keyset_id(keys)

        assert validate_keyset_id(keyset_id.upper(), keys)
        assert validate_keyset_id(keyset_id.lower(), keys)


class TestFeeCalculation:
    """Test input fee calculation functionality."""

    def test_calculate_input_fees_zero_fee(self) -> None:
        """Test fee calculation with zero fee rate."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 200,
                    "secret": "secret2",
                    "C": "sig2",
                    "mint": None,
                },
            ],
        )
        keyset_info = {"input_fee_ppk": 0}

        fee = wallet.calculate_input_fees(proofs, keyset_info)
        assert fee == 0

    def test_calculate_input_fees_positive_fee(self) -> None:
        """Test fee calculation with positive fee rate."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 200,
                    "secret": "secret2",
                    "C": "sig2",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 300,
                    "secret": "secret3",
                    "C": "sig3",
                    "mint": None,
                },
            ],
        )
        # 1000 ppk = 1 sat per proof
        keyset_info = {"input_fee_ppk": 1000}

        fee = wallet.calculate_input_fees(proofs, keyset_info)
        # 3 proofs * 1000 ppk / 1000 = 3 sats
        assert fee == 3

    def test_calculate_input_fees_fractional(self) -> None:
        """Test fee calculation with fractional fees."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 200,
                    "secret": "secret2",
                    "C": "sig2",
                    "mint": None,
                },
            ],
        )
        # 500 ppk = 0.5 sat per proof
        keyset_info = {"input_fee_ppk": 500}

        fee = wallet.calculate_input_fees(proofs, keyset_info)
        # 2 proofs * 500 ppk / 1000 = 1 sat (integer division)
        assert fee == 1

    def test_calculate_input_fees_string_conversion(self) -> None:
        """Test fee calculation with string fee value."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
            ],
        )
        # Fee value as string (from API)
        keyset_info = {"input_fee_ppk": "2000"}

        fee = wallet.calculate_input_fees(proofs, keyset_info)
        # 1 proof * 2000 ppk / 1000 = 2 sats
        assert fee == 2

    def test_calculate_input_fees_invalid_fee(self) -> None:
        """Test fee calculation with invalid fee value."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
            ],
        )
        keyset_info = {"input_fee_ppk": "invalid"}

        fee = wallet.calculate_input_fees(proofs, keyset_info)
        # Should fallback to 0 for invalid fee
        assert fee == 0

    def test_estimate_transaction_fees(self) -> None:
        """Test total transaction fee estimation."""
        wallet = TempWallet()
        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 200,
                    "secret": "secret2",
                    "C": "sig2",
                    "mint": None,
                },
            ],
        )
        keyset_info = {"input_fee_ppk": 1000}
        lightning_fee_reserve = 5

        input_fees, total_fees = wallet.estimate_transaction_fees(
            proofs, keyset_info, lightning_fee_reserve
        )

        assert input_fees == 2  # 2 proofs * 1000 ppk / 1000
        assert total_fees == 7  # 2 + 5


class TestKeysetValidation:
    """Test keyset structure validation."""

    def test_validate_keyset_valid_minimal(self) -> None:
        """Test validation of minimal valid keyset."""
        mint = Mint("https://test.mint")
        keyset = {"id": "00a1b2c3d4e5f6a7", "unit": "sat", "active": True}

        assert mint.validate_keyset(keyset)

    def test_validate_keyset_valid_with_fees(self) -> None:
        """Test validation of keyset with fee information."""
        mint = Mint("https://test.mint")
        keyset = {
            "id": "00a1b2c3d4e5f6a7",
            "unit": "sat",
            "active": True,
            "input_fee_ppk": 1000,
        }

        assert mint.validate_keyset(keyset)

    def test_validate_keyset_valid_with_keys(self) -> None:
        """Test validation of keyset with public keys."""
        mint = Mint("https://test.mint")
        keyset = {
            "id": "00a1b2c3d4e5f6a7",
            "unit": "sat",
            "active": True,
            "keys": {
                "1": "02a1b2c3d4e5f6a7a8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2",
                "2": "03a1b2c3d4e5f6a7a8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2",
            },
        }

        assert mint.validate_keyset(keyset)

    def test_validate_keyset_missing_required_field(self) -> None:
        """Test validation fails for missing required field."""
        mint = Mint("https://test.mint")
        keyset = {
            "id": "00a1b2c3d4e5f6a7",
            "unit": "sat",
            # Missing "active" field
        }

        assert not mint.validate_keyset(keyset)

    def test_validate_keyset_invalid_id_format(self) -> None:
        """Test validation fails for invalid keyset ID."""
        mint = Mint("https://test.mint")

        # Too short
        keyset1 = {"id": "00a1b2c3", "unit": "sat", "active": True}
        assert not mint.validate_keyset(keyset1)

        # Not hex
        keyset2 = {"id": "gggggggggggggggg", "unit": "sat", "active": True}
        assert not mint.validate_keyset(keyset2)

    def test_validate_keyset_invalid_unit(self) -> None:
        """Test validation fails for invalid unit."""
        mint = Mint("https://test.mint")
        keyset = {"id": "00a1b2c3d4e5f6a7", "unit": "invalid_unit", "active": True}

        assert not mint.validate_keyset(keyset)

    def test_validate_keyset_invalid_fee(self) -> None:
        """Test validation fails for invalid fee."""
        mint = Mint("https://test.mint")

        # Negative fee
        keyset1 = {
            "id": "00a1b2c3d4e5f6a7",
            "unit": "sat",
            "active": True,
            "input_fee_ppk": -100,
        }
        assert not mint.validate_keyset(keyset1)

        # Non-numeric fee
        keyset2 = {
            "id": "00a1b2c3d4e5f6a7",
            "unit": "sat",
            "active": True,
            "input_fee_ppk": "invalid",
        }
        assert not mint.validate_keyset(keyset2)

    def test_validate_keysets_response_valid(self) -> None:
        """Test validation of valid keysets response."""
        mint = Mint("https://test.mint")
        response = {
            "keysets": [
                {"id": "00a1b2c3d4e5f6a7", "unit": "sat", "active": True},
                {"id": "01a1b2c3d4e5f6a7", "unit": "sat", "active": False},
            ]
        }

        assert mint.validate_keysets_response(response)

    def test_validate_keysets_response_invalid(self) -> None:
        """Test validation fails for invalid keysets response."""
        mint = Mint("https://test.mint")

        # Missing keysets field
        response1: dict[str, Any] = {}
        assert not mint.validate_keysets_response(response1)

        # Invalid keyset in list
        response2 = {
            "keysets": [
                {"id": "00a1b2c3d4e5f6a7", "unit": "sat", "active": True},
                {"id": "invalid", "unit": "sat", "active": True},  # Invalid ID
            ]
        }
        assert not mint.validate_keysets_response(response2)


@pytest.mark.asyncio
class TestKeysetIntegration:
    """Test integration of keyset and fee functionality."""

    async def test_get_validated_keysets_success(self) -> None:
        mint = Mint("https://test.mint")

        mock_response = {
            "keysets": [
                {
                    "id": "00a1b2c3d4e5f6a7",
                    "unit": "sat",
                    "active": True,
                    "input_fee_ppk": 0,
                }
            ]
        }
        setattr(
            mint, "get_keysets_info", AsyncMock(return_value=mock_response["keysets"])
        )

        result = await mint.get_keysets_info()
        assert mint.validate_keysets_response(mock_response)
        assert result == mock_response["keysets"]

    async def test_get_validated_keysets_failure(self) -> None:
        mint = Mint("https://test.mint")

        mock_response = {"keysets": [{"id": "invalid", "unit": "sat", "active": True}]}
        setattr(
            mint, "get_keysets_info", AsyncMock(return_value=mock_response["keysets"])
        )

        result = await mint.get_keysets_info()
        assert not mint.validate_keysets_response(mock_response)
        assert result == mock_response["keysets"]

    async def test_calculate_total_input_fees_success(self) -> None:
        wallet = TempWallet()

        mint = Mock()
        mint.get_keysets_info = AsyncMock(
            return_value=[
                {"id": "keyset1", "input_fee_ppk": 1000},
                {"id": "keyset2", "input_fee_ppk": 2000},
            ]
        )

        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                },
                {
                    "id": "keyset1",
                    "amount": 200,
                    "secret": "secret2",
                    "C": "sig2",
                    "mint": None,
                },
                {
                    "id": "keyset2",
                    "amount": 300,
                    "secret": "secret3",
                    "C": "sig3",
                    "mint": None,
                },
            ],
        )

        total_fee = await wallet.calculate_total_input_fees(mint, proofs)
        assert total_fee == 4

    async def test_calculate_total_input_fees_failure(self) -> None:
        """Test total input fee calculation with mint failure."""
        wallet = TempWallet()

        # Mock mint that raises exception
        mint = Mock()
        mint.get_keysets_info = AsyncMock(side_effect=Exception("Mint error"))

        proofs = cast(
            list[Proof],
            [
                {
                    "id": "keyset1",
                    "amount": 100,
                    "secret": "secret1",
                    "C": "sig1",
                    "mint": None,
                }
            ],
        )

        # Should fallback to zero fees
        total_fee = await wallet.calculate_total_input_fees(mint, proofs)
        assert total_fee == 0
