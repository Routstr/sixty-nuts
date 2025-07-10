"""Dynamic denomination system for Cashu mints."""

from __future__ import annotations


from .types import KeysetInfo


class DenominationSystem:
    """Manages dynamic denomination calculation based on keyset information."""

    @staticmethod
    def get_keyset_denominations(keyset_info: KeysetInfo) -> list[int]:
        """Extract denominations from keyset keys.

        Args:
            keyset_info: Keyset information containing keys

        Returns:
            Sorted list of denominations (ascending order)
        """
        denominations = []

        # Keys are stored as dict with amount strings as keys
        if isinstance(keyset_info.keys, dict):
            for amount_str in keyset_info.keys:
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
            # Fallback to default powers of 2
            return DenominationSystem._default_split(amount)

        denominations = {}
        remaining = amount

        # Sort in descending order for greedy algorithm
        for denom in sorted(available_denominations, reverse=True):
            if remaining >= denom:
                count = remaining // denom
                denominations[denom] = count
                remaining -= denom * count

        # If we couldn't split perfectly, add smallest denomination
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
        denominations = {}
        remaining = amount

        # Standard powers of 2
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

    @staticmethod
    def validate_denominations(
        keyset_info: KeysetInfo, requested_denominations: dict[int, int]
    ) -> tuple[bool, str | None]:
        """Validate if requested denominations are available in keyset.

        Args:
            keyset_info: Keyset information to validate against
            requested_denominations: Dict of denomination -> count

        Returns:
            Tuple of (is_valid, error_message)
        """
        available_denoms = DenominationSystem.get_keyset_denominations(keyset_info)
        available_set = set(available_denoms)

        for denom in requested_denominations:
            if denom not in available_set:
                return False, f"Denomination {denom} not available in keyset"

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
