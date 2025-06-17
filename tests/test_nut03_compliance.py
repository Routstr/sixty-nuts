#!/usr/bin/env python3
"""Test NUT-03 specification compliance and privacy features."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from sixty_nuts.wallet import Wallet, SwapError
from sixty_nuts.mint import BlindedMessage, Proof


class TestNUT03Compliance:
    """Test cases for NUT-03 specification compliance."""

    @pytest.fixture
    def wallet(self):
        """Create a test wallet instance."""
        return Wallet(
            nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
            mint_urls=["https://testnut.cashu.space"],
        )

    def test_output_ordering_privacy(self, wallet):
        """Test that outputs are created in ascending order by amount for privacy."""
        # Test with various amounts
        test_amounts = [7, 23, 100, 1337]
        keyset_id = "test_keyset_id"

        for amount in test_amounts:
            outputs, secrets, blinding_factors = wallet._create_outputs_with_privacy(
                amount, keyset_id
            )

            # Extract amounts from outputs
            output_amounts = [output["amount"] for output in outputs]

            # Verify outputs are in ascending order (NUT-03 requirement)
            assert output_amounts == sorted(output_amounts), (
                f"Outputs for amount {amount} not in ascending order: {output_amounts}"
            )

            # Verify we have the correct total amount
            assert sum(output_amounts) == amount, (
                f"Total output amount {sum(output_amounts)} doesn't match input {amount}"
            )

            # Verify all arrays have same length
            assert len(outputs) == len(secrets) == len(blinding_factors), (
                "Output arrays have mismatched lengths"
            )

    def test_send_and_change_output_ordering(self, wallet):
        """Test that send and change outputs are properly ordered together."""
        send_amount = 15  # Will create [1, 2, 4, 8] outputs
        change_amount = 35  # Will create [1, 2, 32] outputs  
        keyset_id = "test_keyset_id"

        (
            outputs,
            secrets,
            blinding_factors,
            send_outputs_count,
        ) = wallet._create_send_and_change_outputs(send_amount, change_amount, keyset_id)

        # Extract amounts from outputs
        output_amounts = [output["amount"] for output in outputs]

        # Verify all outputs are in ascending order
        assert output_amounts == sorted(output_amounts), (
            f"Combined outputs not in ascending order: {output_amounts}"
        )

        # Verify total amounts
        assert sum(output_amounts) == send_amount + change_amount, (
            "Total output amount doesn't match send + change"
        )

        # Verify we can identify send outputs count
        assert isinstance(send_outputs_count, int), "Send outputs count must be integer"
        assert 0 <= send_outputs_count <= len(outputs), "Invalid send outputs count"

        # Test that we have correct denominations
        expected_send_denoms = [1, 2, 4, 8]  # 15 = 8 + 4 + 2 + 1
        expected_change_denoms = [1, 2, 32]  # 35 = 32 + 2 + 1
        expected_all_denoms = sorted(expected_send_denoms + expected_change_denoms)
        
        assert output_amounts == expected_all_denoms, (
            f"Output amounts {output_amounts} don't match expected {expected_all_denoms}"
        )

    def test_swap_amount_validation(self, wallet):
        """Test swap amount validation helper."""
        # Test valid case
        inputs = [
            Proof(id="test", amount=10, secret="secret1", C="C1"),
            Proof(id="test", amount=5, secret="secret2", C="C2"),
        ]
        outputs = [
            BlindedMessage(amount=8, id="test", B_="B1"),
            BlindedMessage(amount=4, id="test", B_="B2"),
            BlindedMessage(amount=2, id="test", B_="B3"),
            BlindedMessage(amount=1, id="test", B_="B4"),
        ]

        assert wallet._validate_swap_amounts(inputs, outputs), (
            "Valid swap amounts should pass validation"
        )

        # Test invalid case
        invalid_outputs = [
            BlindedMessage(amount=8, id="test", B_="B1"),
            BlindedMessage(amount=4, id="test", B_="B2"),
        ]

        assert not wallet._validate_swap_amounts(inputs, invalid_outputs), (
            "Invalid swap amounts should fail validation"
        )

    @pytest.mark.asyncio
    async def test_robust_swap_validation(self, wallet):
        """Test the robust swap method with validation."""
        mock_mint = AsyncMock()
        mock_mint.swap.return_value = {"signatures": []}

        # Test validation failure
        inputs = [Proof(id="test", amount=10, secret="secret", C="C")]
        outputs = [BlindedMessage(amount=5, id="test", B_="B")]  # Wrong amount

        with pytest.raises(SwapError, match="Swap amount mismatch"):
            await wallet._robust_swap(mock_mint, inputs, outputs)

        # Test empty outputs
        with pytest.raises(SwapError, match="Cannot perform swap with no outputs"):
            await wallet._robust_swap(mock_mint, inputs, [])

        # Test empty inputs
        with pytest.raises(SwapError, match="Cannot perform swap with no inputs"):
            await wallet._robust_swap(mock_mint, [], outputs)

    def test_denomination_efficiency(self, wallet):
        """Test that denomination selection is efficient."""
        test_cases = [
            (1, [1]),
            (3, [1, 2]),
            (7, [1, 2, 4]),
            (15, [1, 2, 4, 8]),
            (100, [4, 32, 64]),
            (127, [1, 2, 4, 8, 16, 32, 64]),
        ]

        keyset_id = "test_keyset"

        for amount, expected_denoms in test_cases:
            outputs, _, _ = wallet._create_outputs_with_privacy(amount, keyset_id)
            output_amounts = sorted([output["amount"] for output in outputs])
            
            assert output_amounts == expected_denoms, (
                f"For amount {amount}, expected {expected_denoms}, got {output_amounts}"
            )

    def test_privacy_pattern_analysis(self, wallet):
        """Test that output patterns don't leak transaction information."""
        keyset_id = "test_keyset"
        
        # Create outputs for similar amounts and verify they have different patterns
        # when sorted, making it harder to correlate
        test_amounts = [23, 24, 25]  # Close amounts
        patterns = []

        for amount in test_amounts:
            outputs, _, _ = wallet._create_outputs_with_privacy(amount, keyset_id)
            # Get the pattern of denomination sizes
            pattern = tuple(output["amount"] for output in outputs)
            patterns.append(pattern)

        # The patterns should be ascending and deterministic for same amount
        for pattern in patterns:
            assert pattern == tuple(sorted(pattern)), (
                f"Pattern {pattern} is not in ascending order"
            )

        # Different amounts should produce different patterns
        assert len(set(patterns)) == len(patterns), (
            "Similar amounts should produce different patterns"
        )

    def test_no_predictable_ordering_leaked(self, wallet):
        """Test that the old predictable descending order is not present."""
        keyset_id = "test_keyset"
        amount = 127  # Creates multiple denominations

        outputs, _, _ = wallet._create_outputs_with_privacy(amount, keyset_id)
        output_amounts = [output["amount"] for output in outputs]

        # Verify it's NOT in descending order (the old problematic pattern)
        descending_order = sorted(output_amounts, reverse=True)
        assert output_amounts != descending_order, (
            "Outputs are in descending order - this leaks privacy information!"
        )

        # Verify it IS in ascending order (the required NUT-03 pattern)
        ascending_order = sorted(output_amounts)
        assert output_amounts == ascending_order, (
            "Outputs must be in ascending order per NUT-03 specification"
        )

    @pytest.mark.asyncio
    async def test_integration_privacy_preserved(self, wallet):
        """Integration test to ensure privacy is preserved in real operations."""
        with patch.object(wallet, '_get_mint') as mock_get_mint:
            mock_mint = AsyncMock()
            mock_get_mint.return_value = mock_mint
            
            # Mock the mint responses
            mock_mint.get_keys.return_value = {
                "keysets": [{"id": "test_keyset", "keys": {"1": "key1", "2": "key2"}}]
            }
            mock_mint.swap.return_value = {
                "signatures": [
                    {"id": "test_keyset", "amount": 1, "C_": "sig1"},
                    {"id": "test_keyset", "amount": 2, "C_": "sig2"},
                ]
            }

            # Test that the redeem operation uses privacy-preserving outputs
            # We'll check that the swap is called with properly ordered outputs
            token = "cashuAeyJ0b2tlbiI6W3sibWludCI6Imh0dHBzOi8vdGVzdG51dC5jYXNodS5zcGFjZSIsInByb29mcyI6W3siaWQiOiIwMGFkMjY4YzRkMWY1ODI2IiwiYW1vdW50IjozLCJzZWNyZXQiOiI0ZDZiOWZlZmU5MmE2YzY5MzIzYTQ2ZjQ3ZjAxOTdjZjVlODI3ZWNjODAwZjJhZmU4ZTc5YzQwOWVjN2IyM2I0IiwiQyI6IjAyZjc2NGIwMTM5ZWI3ZDE4MTAwODU0MmI5MDkzOWJkN2FjNWVkZTE5M2YwNDVlNzQ0YmY4OTEzOGVkOWVhOTQwNyJ9XX1dfQ"
            
            with patch.object(wallet, '_parse_cashu_token') as mock_parse:
                mock_parse.return_value = (
                    "https://testnut.cashu.space",
                    "sat", 
                    [{"id": "test_keyset", "amount": 3, "secret": "secret", "C": "C", "mint": "test"}]
                )
                
                with patch.object(wallet, 'publish_token_event') as mock_publish:
                    mock_publish.return_value = "event_id"
                    with patch.object(wallet, 'publish_spending_history') as mock_history:
                        mock_history.return_value = "history_id"
                        
                        # This should use the privacy-preserving output creation
                        await wallet.redeem(token)

                        # Verify swap was called
                        mock_mint.swap.assert_called_once()
                        
                        # Get the outputs that were passed to swap
                        call_args = mock_mint.swap.call_args
                        outputs = call_args.kwargs['outputs']
                        
                        # Verify outputs are in ascending order
                        output_amounts = [output['amount'] for output in outputs]
                        assert output_amounts == sorted(output_amounts), (
                            "Integration test: outputs not in ascending order"
                        )


class TestSwapErrorHandling:
    """Test swap-specific error handling."""

    @pytest.fixture
    def wallet(self):
        """Create a test wallet instance."""
        return Wallet(
            nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
            mint_urls=["https://testnut.cashu.space"],
        )

    @pytest.mark.asyncio
    async def test_swap_error_inheritance(self, wallet):
        """Test that SwapError inherits from WalletError."""
        from sixty_nuts.wallet import WalletError
        
        # SwapError should inherit from WalletError
        assert issubclass(SwapError, WalletError)
        
        # Should be catchable as WalletError
        try:
            raise SwapError("test error")
        except WalletError:
            pass  # This should work
        except Exception:
            pytest.fail("SwapError should be catchable as WalletError")

    @pytest.mark.asyncio 
    async def test_mint_swap_failure_handling(self, wallet):
        """Test handling of mint swap failures."""
        mock_mint = AsyncMock()
        mock_mint.swap.side_effect = Exception("Mint is down")

        inputs = [Proof(id="test", amount=10, secret="secret", C="C")]
        outputs = [BlindedMessage(amount=10, id="test", B_="B")]

        with pytest.raises(SwapError, match="Swap operation failed: Mint is down"):
            await wallet._robust_swap(mock_mint, inputs, outputs)