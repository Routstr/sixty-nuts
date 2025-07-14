"""Unit tests for wallet functionality that doesn't require live mint/relay connections."""

import pytest
import asyncio

from sixty_nuts.wallet import Wallet
from sixty_nuts.crypto import generate_privkey
from sixty_nuts.types import Proof
from unittest.mock import patch, AsyncMock


class TestWalletFeeCalculation:
    """Test fee calculation logic."""

    def test_fee_calculation_empty_proofs(self):
        """Test fee calculation with empty proofs."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        fees = wallet.calculate_input_fees([], {"input_fee_ppk": 1000})
        assert fees == 0

    def test_fee_calculation_with_proofs(self):
        """Test fee calculation with mock proofs."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        mock_proofs: list[Proof] = [
            {
                "id": "test1",
                "amount": 10,
                "secret": "secret1",
                "C": "C1",
                "mint": "test",
                "unit": "sat",
            },
            {
                "id": "test2",
                "amount": 20,
                "secret": "secret2",
                "C": "C2",
                "mint": "test",
                "unit": "sat",
            },
        ]

        # Test with 1 sat per proof fee
        keyset_info = {"input_fee_ppk": 1000}  # 1000 ppk = 1 sat per proof
        fees = wallet.calculate_input_fees(mock_proofs, keyset_info)
        assert fees == 2  # 2 proofs * 1 sat = 2 sats

        # Test with no fees
        keyset_info_no_fee = {"input_fee_ppk": 0}
        fees_no_fee = wallet.calculate_input_fees(mock_proofs, keyset_info_no_fee)
        assert fees_no_fee == 0

    def test_fee_calculation_fractional(self):
        """Test fee calculation with fractional fees."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        mock_proofs: list[Proof] = [
            {
                "id": "test1",
                "amount": 1,
                "secret": "secret1",
                "C": "C1",
                "mint": "test",
                "unit": "sat",
            },
        ]

        # Test with 0.5 sat per proof fee (500 ppk)
        keyset_info = {"input_fee_ppk": 500}
        fees = wallet.calculate_input_fees(mock_proofs, keyset_info)
        assert (
            fees == 1
        )  # Should round up to 1 (ceiling division to match mint behavior)

        # Test with 1.5 sat per proof fee (1500 ppk)
        keyset_info = {"input_fee_ppk": 1500}
        fees = wallet.calculate_input_fees(mock_proofs, keyset_info)
        assert fees == 2  # Should round up to 2

    def test_estimate_transaction_fees(self):
        """Test transaction fee estimation."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        mock_proofs: list[Proof] = [
            {
                "id": "test1",
                "amount": 10,
                "secret": "secret1",
                "C": "C1",
                "mint": "test",
                "unit": "sat",
            },
            {
                "id": "test2",
                "amount": 20,
                "secret": "secret2",
                "C": "C2",
                "mint": "test",
                "unit": "sat",
            },
        ]

        keyset_info = {"input_fee_ppk": 1000}  # 1 sat per proof
        lightning_fee = 5

        input_fees, total_fees = wallet.estimate_transaction_fees(
            mock_proofs, keyset_info, lightning_fee
        )

        assert input_fees == 2  # 2 proofs * 1 sat
        assert total_fees == 7  # 2 + 5


class TestWalletTokenSerialization:
    """Test token serialization and parsing logic."""

    def test_token_serialization_v3(self):
        """Test V3 token serialization."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        sample_proofs: list[Proof] = [
            {
                "id": "00ffe7838f8d9312",
                "amount": 10,
                "secret": "dGVzdA==",
                "C": "02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "mint": "http://test.mint",
                "unit": "sat",
            }
        ]

        token_v3 = wallet._serialize_proofs_v3(sample_proofs, "http://test.mint", "sat")
        assert token_v3.startswith("cashuA"), "V3 tokens should start with cashuA"

    def test_token_serialization_v4(self):
        """Test V4 token serialization."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        sample_proofs: list[Proof] = [
            {
                "id": "00ffe7838f8d9312",
                "amount": 10,
                "secret": "dGVzdA==",
                "C": "02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "mint": "http://test.mint",
                "unit": "sat",
            }
        ]

        token_v4 = wallet._serialize_proofs_v4(sample_proofs, "http://test.mint", "sat")
        assert token_v4.startswith("cashuB"), "V4 tokens should start with cashuB"

    def test_token_roundtrip_v3(self):
        """Test V3 token serialization and parsing roundtrip."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        sample_proofs: list[Proof] = [
            {
                "id": "00ffe7838f8d9312",
                "amount": 10,
                "secret": "dGVzdA==",
                "C": "02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "mint": "http://test.mint",
                "unit": "sat",
            }
        ]

        # Serialize to V3
        token_v3 = wallet._serialize_proofs_v3(sample_proofs, "http://test.mint", "sat")

        # Parse back
        mint_url, unit, parsed_proofs = wallet._parse_cashu_token(token_v3)

        assert mint_url == "http://test.mint"
        assert unit == "sat"
        assert len(parsed_proofs) == 1
        assert parsed_proofs[0]["amount"] == 10

    def test_token_roundtrip_v4(self):
        """Test V4 token serialization and parsing roundtrip."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        sample_proofs: list[Proof] = [
            {
                "id": "00ffe7838f8d9312",
                "amount": 10,
                "secret": "dGVzdA==",
                "C": "02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "mint": "http://test.mint",
                "unit": "sat",
            }
        ]

        # Serialize to V4
        token_v4 = wallet._serialize_proofs_v4(sample_proofs, "http://test.mint", "sat")

        # Parse back
        mint_url, unit, parsed_proofs = wallet._parse_cashu_token(token_v4)

        assert mint_url == "http://test.mint"
        assert unit == "sat"
        assert len(parsed_proofs) == 1
        assert parsed_proofs[0]["amount"] == 10

    def test_parse_invalid_tokens(self):
        """Test parsing invalid tokens raises appropriate errors."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with pytest.raises((ValueError, Exception)):
            wallet._parse_cashu_token("invalid_token")

        with pytest.raises((ValueError, Exception)):
            wallet._parse_cashu_token("cashuAinvalid_base64")

        with pytest.raises((ValueError, Exception)):
            wallet._parse_cashu_token("cashuBinvalid_cbor")


class TestWalletVersionValidation:
    """Test version validation logic."""

    def test_send_token_invalid_version(self):
        """Test error handling for invalid token versions."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        # These should raise ValueError without needing async context
        with pytest.raises(ValueError, match="Unsupported token version"):
            # This will fail early in the method before any async operations
            asyncio.run(wallet.send(10, token_version=2))  # type: ignore

        with pytest.raises(ValueError, match="Unsupported token version"):
            asyncio.run(wallet.send(10, token_version=5))  # type: ignore


class TestWalletCurrencyValidation:
    """Test currency unit validation."""

    def test_valid_currency_units(self):
        """Test that valid currency units are accepted."""
        from sixty_nuts.types import CurrencyUnit

        valid_units: list[CurrencyUnit] = ["sat", "msat", "btc", "usd", "eur"]

        for unit in valid_units:
            wallet = Wallet(
                nsec=generate_privkey(),
                mint_urls=["http://test.mint"],
                relay_urls=["ws://test.relay"],
            )
            wallet._validate_currency_unit(unit)

    def test_invalid_currency_unit(self):
        """Test invalid currency unit handling."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with pytest.raises(ValueError, match="Unsupported currency unit"):
            wallet._validate_currency_unit("invalid")  # type: ignore


class TestWalletOptimalDenominations:
    """Test optimal denomination calculation logic."""

    @pytest.mark.asyncio
    async def test_calculate_optimal_denominations_small(self):
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with patch(
            "sixty_nuts.wallet.Mint.get_currencies", new_callable=AsyncMock
        ) as mock_currencies:
            mock_currencies.return_value = ["sat"]
            with patch(
                "sixty_nuts.wallet.Mint.get_denominations_for_currency",
                new_callable=AsyncMock,
            ) as mock_denoms:
                mock_denoms.return_value = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
                denoms = await wallet._calculate_optimal_denominations(
                    1, "http://test.mint", "sat"
                )
                assert denoms == {1: 1}

                denoms = await wallet._calculate_optimal_denominations(
                    3, "http://test.mint", "sat"
                )
                assert denoms == {2: 1, 1: 1}

                denoms = await wallet._calculate_optimal_denominations(
                    7, "http://test.mint", "sat"
                )
                assert denoms == {4: 1, 2: 1, 1: 1}

    @pytest.mark.asyncio
    async def test_calculate_optimal_denominations_large(self):
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with patch(
            "sixty_nuts.wallet.Mint.get_currencies", new_callable=AsyncMock
        ) as mock_currencies:
            mock_currencies.return_value = ["sat"]
            with patch(
                "sixty_nuts.wallet.Mint.get_denominations_for_currency",
                new_callable=AsyncMock,
            ) as mock_denoms:
                mock_denoms.return_value = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
                denoms = await wallet._calculate_optimal_denominations(
                    1000, "http://test.mint", "sat"
                )
                expected = {512: 1, 256: 1, 128: 1, 64: 1, 32: 1, 8: 1}
                assert denoms == expected

                total = sum(denom * count for denom, count in denoms.items())
                assert total == 1000

    @pytest.mark.asyncio
    async def test_calculate_optimal_denominations_zero(self):
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with patch(
            "sixty_nuts.wallet.Mint.get_currencies", new_callable=AsyncMock
        ) as mock_currencies:
            mock_currencies.return_value = ["sat"]
            with patch(
                "sixty_nuts.wallet.Mint.get_denominations_for_currency",
                new_callable=AsyncMock,
            ) as mock_denoms:
                mock_denoms.return_value = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
                denoms = await wallet._calculate_optimal_denominations(
                    0, "http://test.mint", "sat"
                )
                assert denoms == {}


class TestWalletInsufficientBalanceCheck:
    """Test insufficient balance validation logic."""

    def test_raise_if_insufficient_balance_sufficient(self):
        """Test that sufficient balance doesn't raise error."""
        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        # Should not raise
        wallet.raise_if_insufficient_balance(100, 50)
        wallet.raise_if_insufficient_balance(100, 100)

    def test_raise_if_insufficient_balance_insufficient(self):
        """Test that insufficient balance raises WalletError."""
        from sixty_nuts.types import WalletError

        wallet = Wallet(
            nsec=generate_privkey(),
            mint_urls=["http://test.mint"],
            relay_urls=["ws://test.relay"],
        )

        with pytest.raises(WalletError, match="Insufficient balance"):
            wallet.raise_if_insufficient_balance(50, 100)

        with pytest.raises(WalletError, match="Insufficient balance"):
            wallet.raise_if_insufficient_balance(0, 1)
