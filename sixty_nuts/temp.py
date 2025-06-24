from .crypto import PrivateKey, bech32_decode, convertbits
from .mint import CurrencyUnit
from .wallet import Wallet, WalletError


class TempWallet(Wallet):
    """Temporary wallet that generates a new random private key without storing it.

    This wallet creates a new random Nostr private key on initialization and
    operates entirely in memory. The private key is not stored or persisted
    anywhere, making it suitable for ephemeral operations.
    """

    def __init__(
        self,
        *,
        mint_urls: list[str] | None = None,
        currency: CurrencyUnit = "sat",
        wallet_privkey: str | None = None,
        relays: list[str] | None = None,
    ) -> None:
        """Initialize temporary wallet with a new random private key.

        Args:
            mint_urls: Cashu mint URLs (defaults to minibits mint)
            currency: Currency unit (sat, msat, or usd)
            wallet_privkey: Private key for P2PK operations (generated if not provided)
            relays: Nostr relay URLs to use
        """
        # Generate a new random private key
        temp_privkey = PrivateKey()
        temp_nsec = self._encode_nsec(temp_privkey)

        # Initialize parent with generated nsec
        super().__init__(
            nsec=temp_nsec,
            mint_urls=mint_urls,
            currency=currency,
            wallet_privkey=wallet_privkey,
            relays=relays,
        )

    def _encode_nsec(self, privkey: PrivateKey) -> str:
        """Encode private key as nsec (bech32) format.

        Args:
            privkey: The private key to encode

        Returns:
            nsec-encoded private key string
        """
        # Try to use bech32 encoding if available
        if bech32_decode is not None and convertbits is not None:
            from bech32 import bech32_encode  # type: ignore

            # Convert private key bytes to 5-bit groups for bech32
            key_bytes = privkey.secret
            converted = convertbits(key_bytes, 8, 5, pad=True)
            if converted is not None:
                encoded = bech32_encode("nsec", converted)
                if encoded:
                    return encoded

        # Fallback to hex encoding with nsec prefix
        return f"nsec_{privkey.to_hex()}"

    @classmethod
    async def create(  # type: ignore[override]
        cls,
        *,
        mint_urls: list[str] | None = None,
        currency: CurrencyUnit = "sat",
        wallet_privkey: str | None = None,
        relays: list[str] | None = None,
        auto_init: bool = True,
    ) -> "TempWallet":
        """Create and optionally check for existing temporary wallet events.

        Args:
            mint_urls: Cashu mint URLs
            currency: Currency unit
            wallet_privkey: Private key for P2PK operations
            relays: Nostr relay URLs
            auto_init: If True, check for existing wallet state (but don't create new events)

        Returns:
            Temporary wallet instance (call initialize_wallet() to create events if needed)
        """
        wallet = cls(
            mint_urls=mint_urls,
            currency=currency,
            wallet_privkey=wallet_privkey,
            relays=relays,
        )

        if auto_init:
            try:
                # Try to connect to relays and check for existing state
                await wallet.relay_manager.get_relay_connections()
                # Try to fetch existing wallet state if it exists
                await wallet.fetch_wallet_state(check_proofs=False)
            except Exception:
                # If no wallet exists or fetch fails, that's fine
                # User can call initialize_wallet() explicitly if needed
                pass

        return wallet


async def redeem_to_lnurl(token: str, lnurl: str, *, mint_fee_reserve: int = 1) -> int:
    """Redeem a token to an LNURL address and return the amount sent.

    This function automatically handles fees by reducing the send amount if needed.

    Args:
        token: Cashu token to redeem
        lnurl: LNURL/Lightning Address to send to
        mint_fee_reserve: Expected mint fee reserve (default: 1 sat)

    Returns:
        Amount actually sent (after fees)

    Raises:
        WalletError: If redeemed amount is too small (<=1 sat)
    """
    async with TempWallet() as wallet:
        amount, unit = await wallet.redeem(token)

        # Check if amount is too small
        if amount <= mint_fee_reserve:
            raise WalletError(
                f"Redeemed amount ({amount} {unit}) is too small. "
                f"After fees, nothing would be left to send."
            )

        # Try to send the full amount first
        try:
            paid = await wallet.send_to_lnurl(lnurl, amount)
            return paid
        except WalletError as e:
            # If insufficient balance due to fees, automatically adjust
            if "Insufficient balance" in str(e) and amount > mint_fee_reserve:
                # Send amount minus fee reserve
                adjusted_amount = amount - mint_fee_reserve
                paid = await wallet.send_to_lnurl(lnurl, adjusted_amount)
                return paid
            raise
