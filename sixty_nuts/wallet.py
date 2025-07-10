from __future__ import annotations

from typing import cast
import base64
import os
import json
import secrets
import time
import asyncio
from pathlib import Path

import httpx
from coincurve import PrivateKey, PublicKey

from .mint import (
    Mint,
    ProofComplete,
)
from .relay import (
    RelayManager,
    EventKind,
    NostrEvent,
)
from .crypto import (
    unblind_signature,
    hash_to_curve,
    create_blinded_message_with_secret,
    get_mint_pubkey_for_amount,
    decode_nsec,
    generate_privkey,
    get_pubkey,
    nip44_decrypt,
)
from .lnurl import (
    get_lnurl_data,
    get_lnurl_invoice,
    parse_lightning_invoice_amount,
    LNURLError,
)
from .types import (
    ProofDict,
    WalletError,
    EnhancedWalletState,
    KeysetInfo,
    CurrencyUnit,
    BlindedMessage,
    Proof,
)
from .events import EventManager

try:
    import cbor2
except ModuleNotFoundError:  # pragma: no cover – allow runtime miss
    cbor2 = None  # type: ignore


# Environment variable for mint URLs
MINTS_ENV_VAR = "CASHU_MINTS"

# Popular public mints for user selection
POPULAR_MINTS = [
    # "https://mint.routstr.com"  # coming soon
    "https://mint.minibits.cash/Bitcoin",
    "https://mint.cubabitcoin.org",
    "https://stablenut.umint.cash",
    "https://mint.macadamia.cash",
]


def get_mints_from_env() -> list[str]:
    """Get mint URLs from environment variable or .env file.

    Expected format: comma-separated URLs
    Example: CASHU_MINTS="https://mint1.com,https://mint2.com"

    Priority order:
    1. Environment variable CASHU_MINTS
    2. .env file in current working directory

    Returns:
        List of mint URLs from environment or .env file, empty list if not set
    """
    # First check environment variable
    env_mints = os.getenv(MINTS_ENV_VAR)
    if env_mints:
        # Split by comma and clean up
        mints = [mint.strip() for mint in env_mints.split(",")]
        # Filter out empty strings
        mints = [mint for mint in mints if mint]
        return mints

    # Then check .env file in current working directory
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith(f"{MINTS_ENV_VAR}="):
                    # Extract value after the equals sign
                    value = line.split("=", 1)[1]
                    # Remove quotes if present
                    value = value.strip("\"'")
                    if value:
                        # Split by comma and clean up
                        mints = [mint.strip() for mint in value.split(",")]
                        # Filter out empty strings
                        mints = [mint for mint in mints if mint]
                        return mints
    except Exception:
        # If reading .env file fails, continue
        pass

    return []


def set_mints_in_env(mints: list[str]) -> None:
    """Set mint URLs in .env file for persistent caching.

    Args:
        mints: List of mint URLs to cache
    """
    if not mints:
        return

    from pathlib import Path

    mint_str = ",".join(mints)
    env_file = Path.cwd() / ".env"
    env_line = f'{MINTS_ENV_VAR}="{mint_str}"\n'

    try:
        if env_file.exists():
            # Check if CASHU_MINTS already exists in the file
            content = env_file.read_text()
            lines = content.splitlines()

            # Look for existing CASHU_MINTS line
            mint_line_found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{MINTS_ENV_VAR}="):
                    # Replace existing CASHU_MINTS line
                    new_lines.append(env_line.rstrip())
                    mint_line_found = True
                else:
                    new_lines.append(line)

            if not mint_line_found:
                # Add new CASHU_MINTS line at the end
                new_lines.append(env_line.rstrip())

            # Write back to file
            env_file.write_text("\n".join(new_lines) + "\n")
        else:
            # Create new .env file
            env_file.write_text(env_line)

    except Exception as e:
        # If writing to .env file fails, fall back to environment variable
        print(f"Warning: Could not write to .env file: {e}")
        print("Falling back to session environment variable")
        os.environ[MINTS_ENV_VAR] = mint_str


def clear_mints_from_env() -> bool:
    """Clear mint URLs from .env file and environment variable.

    Returns:
        True if mints were cleared, False if none were set
    """
    cleared = False

    # Clear from environment variable
    if MINTS_ENV_VAR in os.environ:
        del os.environ[MINTS_ENV_VAR]
        cleared = True

    # Clear from .env file
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            lines = content.splitlines()

            # Remove CASHU_MINTS line
            new_lines = []
            for line in lines:
                if not line.strip().startswith(f"{MINTS_ENV_VAR}="):
                    new_lines.append(line)
                else:
                    cleared = True

            if new_lines:
                # Write back remaining lines
                env_file.write_text("\n".join(new_lines) + "\n")
            else:
                # If file would be empty, remove it
                env_file.unlink()

    except Exception:
        # If clearing from .env file fails, that's okay
        pass

    return cleared


def validate_mint_url(url: str) -> bool:
    """Validate that a mint URL has the correct format.

    Args:
        url: Mint URL to validate

    Returns:
        True if URL appears valid, False otherwise
    """
    if not url:
        return False

    # Basic URL validation - should start with http:// or https://
    if not (url.startswith("http://") or url.startswith("https://")):
        return False

    # Should not end with slash for consistency
    if url.endswith("/"):
        return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Keyset Management System
# ──────────────────────────────────────────────────────────────────────────────


class KeysetManager:
    """Centralized keyset management for multi-currency support."""

    def __init__(self) -> None:
        self.keysets: dict[str, KeysetInfo] = {}  # keyset_id -> KeysetInfo
        self.mint_keysets: dict[str, list[str]] = {}  # mint_url -> [keyset_ids]
        self.currency_keysets: dict[
            CurrencyUnit, list[str]
        ] = {}  # currency -> [keyset_ids]
        self._cache_expiry = 3600  # 1 hour cache
        self._cache_timestamps: dict[str, float] = {}  # keyset_id -> timestamp

    def add_keyset(self, keyset_info: KeysetInfo) -> None:
        """Add or update a keyset."""
        keyset_id = keyset_info.id
        mint_url = keyset_info.mint_url
        currency = keyset_info.unit

        # Store keyset
        self.keysets[keyset_id] = keyset_info
        self._cache_timestamps[keyset_id] = time.time()

        # Update mint index
        if mint_url not in self.mint_keysets:
            self.mint_keysets[mint_url] = []
        if keyset_id not in self.mint_keysets[mint_url]:
            self.mint_keysets[mint_url].append(keyset_id)

        # Update currency index
        if currency not in self.currency_keysets:
            self.currency_keysets[currency] = []
        if keyset_id not in self.currency_keysets[currency]:
            self.currency_keysets[currency].append(keyset_id)

    def get_keyset(self, keyset_id: str) -> KeysetInfo | None:
        """Get a keyset by ID."""
        return self.keysets.get(keyset_id)

    def get_mint_keysets(
        self, mint_url: str, *, active_only: bool = True
    ) -> list[KeysetInfo]:
        """Get all keysets for a mint."""
        keyset_ids = self.mint_keysets.get(mint_url, [])
        keysets = [self.keysets[kid] for kid in keyset_ids if kid in self.keysets]

        if active_only:
            keysets = [ks for ks in keysets if ks.active]

        return keysets

    def get_currency_keysets(
        self, currency: CurrencyUnit, *, active_only: bool = True
    ) -> list[KeysetInfo]:
        """Get all keysets for a currency."""
        keyset_ids = self.currency_keysets.get(currency, [])
        keysets = [self.keysets[kid] for kid in keyset_ids if kid in self.keysets]

        if active_only:
            keysets = [ks for ks in keysets if ks.active]

        return keysets

    def find_keyset(
        self, mint_url: str, currency: CurrencyUnit, *, active_only: bool = True
    ) -> KeysetInfo | None:
        """Find the best keyset for a mint and currency."""
        mint_keysets = self.get_mint_keysets(mint_url, active_only=active_only)
        currency_keysets = [ks for ks in mint_keysets if ks.unit == currency]

        if not currency_keysets:
            return None

        # Prefer keysets with lower fees
        currency_keysets.sort(key=lambda ks: ks.input_fee_ppk)
        return currency_keysets[0]

    def is_cached(self, keyset_id: str) -> bool:
        """Check if keyset is cached and not expired."""
        if keyset_id not in self._cache_timestamps:
            return False

        age = time.time() - self._cache_timestamps[keyset_id]
        return age < self._cache_expiry

    def clear_cache(self) -> None:
        """Clear all cached keysets."""
        self.keysets.clear()
        self.mint_keysets.clear()
        self.currency_keysets.clear()
        self._cache_timestamps.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Wallet implementation
# ──────────────────────────────────────────────────────────────────────────────


class Wallet:
    """Lightweight stateless Cashu wallet implementing NIP-60."""

    def __init__(
        self,
        nsec: str,  # nostr private key
        *,
        mint_urls: list[str] | None = None,  # cashu mint urls (can have multiple)
        currency: CurrencyUnit = "sat",  # TODO remove bc multi currency by default
        wallet_privkey: str | None = None,  # TODO remove contained in wallet event
        relays: list[str] | None = None,  # nostr relays to use
    ) -> None:
        self.nsec = nsec
        self._privkey = decode_nsec(nsec)

        # Initialize mint URLs as a set that accumulates from all sources
        self.mint_urls: set[str] = set(mint_urls) if mint_urls else set()

        self.currency: CurrencyUnit = currency
        # Validate currency unit is supported
        self._validate_currency_unit(currency)

        # Generate wallet privkey if not provided
        if wallet_privkey is None:
            wallet_privkey = generate_privkey()
        self.wallet_privkey = wallet_privkey
        self._wallet_privkey_obj = PrivateKey(bytes.fromhex(wallet_privkey))

        # Store relays - will be determined later if not provided
        self.relays: list[str] = relays or []

        # Mint instances
        self.mints: dict[str, Mint] = {}

        # Keyset manager for multi-currency support
        self.keyset_manager = KeysetManager()

        # Relay manager - will be initialized with proper relays later
        self.relay_manager = RelayManager(
            relay_urls=self.relays,  # May be empty initially
            privkey=self._privkey,  # Already a PrivateKey object
            use_queued_relays=True,
            min_relay_interval=1.0,
        )

        # Event manager - will be initialized with mint URLs later
        self.event_manager: EventManager | None = None

        # Track minted quotes to prevent double-minting
        self._minted_quotes: set[str] = set()

        # Shared HTTP client reused by all Mint objects
        self.mint_client = httpx.AsyncClient()

        # Cache for proof validation results to prevent re-checking spent proofs
        self._proof_state_cache: dict[
            str, dict[str, str]
        ] = {}  # proof_id -> {state, timestamp}
        self._cache_expiry = 300  # 5 minutes

        # Track known spent proofs to avoid re-validation
        self._known_spent_proofs: set[str] = set()

    async def _initialize_mint_urls(self) -> None:
        """Initialize mint URLs from various sources.

        Accumulates mint URLs from all available sources:
        - Constructor arguments (already added)
        - Environment variables
        - Existing NIP-60 wallet event
        """
        # 1. Constructor mints are already in self.mint_urls

        # 2. Add from environment variables
        env_mints = get_mints_from_env()
        if env_mints:
            self.mint_urls.update(env_mints)

        # 3. Add from existing wallet event
        try:
            exists, wallet_event = await self.check_wallet_event_exists()
            if exists and wallet_event:
                content = nip44_decrypt(wallet_event["content"], self._privkey)
                wallet_data = json.loads(content)

                # Extract mint URLs from wallet event
                event_mints = []
                for item in wallet_data:
                    if item[0] == "mint":
                        event_mints.append(item[1])

                if event_mints:
                    self.mint_urls.update(event_mints)
        except Exception:
            # Failed to decrypt or parse wallet event - continue
            pass

        # 4. Check if we have any mint URLs
        if not self.mint_urls:
            raise WalletError(
                "No mint URLs configured. Please provide mint URLs via:\n"
                f'  - Environment variable: {MINTS_ENV_VAR}="https://mint1.com,https://mint2.com"\n'
                f'  - .env file in current directory: {MINTS_ENV_VAR}="https://mint1.com,https://mint2.com"\n'
                '  - Constructor argument: mint_urls=["https://mint1.com"]\n'
                "  - Or use the CLI to select from popular mints"
            )

    async def _initialize_event_manager(self) -> None:
        """Initialize event manager after mint URLs are determined."""
        if not self.mint_urls:
            raise WalletError("Cannot initialize event manager without mint URLs")

        self.event_manager = EventManager(
            relay_manager=self.relay_manager,
            privkey=self._privkey,
            mint_urls=list(self.mint_urls),  # Convert set to list for EventManager
        )

    async def _ensure_event_manager(self) -> EventManager:
        """Ensure event manager is initialized and return it."""
        if self.event_manager is None:
            if not self.mint_urls:
                await self._initialize_mint_urls()
            await self._initialize_event_manager()

        assert self.event_manager is not None  # For type checker
        return self.event_manager

    @classmethod
    async def create(
        cls,
        nsec: str,
        *,
        mint_urls: list[str] | None = None,
        currency: CurrencyUnit = "sat",
        wallet_privkey: str | None = None,
        relays: list[str] | None = None,
        auto_init: bool = True,
        prompt_for_relays: bool = True,
    ) -> "Wallet":
        """Create and optionally check for existing wallet events.

        Args:
            nsec: Nostr private key
            mint_urls: Cashu mint URLs
            currency: Currency unit
            wallet_privkey: Private key for P2PK operations
            relays: Nostr relay URLs (if None, will discover automatically)
            auto_init: If True, check for existing wallet state (but don't create new events)
            prompt_for_relays: If True, prompt user for relays if none found

        Returns:
            Wallet instance (call initialize_wallet() to create wallet events if needed)
        """
        # Import here to avoid circular imports
        from .relay import get_relays_for_wallet

        # If no relays provided, discover them
        if not relays:
            privkey = decode_nsec(nsec)
            relays = await get_relays_for_wallet(
                privkey, prompt_if_needed=prompt_for_relays
            )

        wallet = cls(
            nsec=nsec,
            mint_urls=mint_urls,
            currency=currency,
            wallet_privkey=wallet_privkey,
            relays=relays,
        )

        # Initialize mint URLs from various sources
        try:
            await wallet._initialize_mint_urls()
        except WalletError:
            # If this is CLI usage, we'll handle mint selection there
            # For non-CLI usage, re-raise the error
            raise

        # Initialize event manager now that we have mint URLs
        await wallet._initialize_event_manager()

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

    # ─────────────────────────────── Receive ──────────────────────────────────

    async def redeem(self, token: str, *, auto_swap: bool = True) -> tuple[int, str]:
        """Redeem a Cashu token into the wallet balance.

        If the token is from an untrusted mint (not in wallet's mint_urls),
        it will automatically be swapped to the wallet's primary mint.

        Args:
            token: Cashu token to redeem
            auto_swap: If True, automatically swap tokens from untrusted mints

        Returns:
            Tuple of (amount, unit) added to wallet
        """
        # Parse token
        mint_url, unit, proofs = self._parse_cashu_token(token)

        # Check if this is a trusted mint
        if auto_swap and self.mint_urls and mint_url not in self.mint_urls:
            # Token is from untrusted mint - swap to our primary mint
            proofs = await self.transfer_proofs(proofs, self._primary_mint_url())

        # Proceed with normal redemption for trusted mints
        # Calculate total amount
        total_amount = sum(p["amount"] for p in proofs)

        # Get mint instance to calculate fees
        mint = self._get_mint(mint_url)

        # Calculate input fees for these proofs
        input_fees = await self.calculate_total_input_fees(mint, proofs)

        # Calculate optimal denominations for the amount after fees
        output_amount = total_amount - input_fees
        optimal_denoms = self._calculate_optimal_denominations(output_amount)

        # Use the abstracted swap method to get new proofs
        new_proofs = await self._swap_proof_denominations(
            proofs, optimal_denoms, mint_url
        )

        # Publish new token event
        event_manager = await self._ensure_event_manager()
        token_event_id = await event_manager.publish_token_event(new_proofs)  # type: ignore

        # Publish spending history
        await event_manager.publish_spending_history(
            direction="in",
            amount=output_amount,  # Use actual amount added after fees
            created_token_ids=[token_event_id],
        )

        return output_amount, unit  # Return actual amount added to wallet after fees

    async def mint_async(
        self, amount: int, *, timeout: int = 300, mint_url: str | None = None
    ) -> tuple[str, asyncio.Task[bool]]:
        """Create a Lightning invoice and return a task that completes when paid.

        This returns immediately with the invoice and a background task that
        polls for payment.

        Args:
            amount: Amount in the wallet's currency unit
            timeout: Maximum seconds to wait for payment (default: 5 minutes)
            mint_url: Specific mint URL to use (defaults to primary mint)

        Returns:
            Tuple of (lightning_invoice, payment_task)
            The payment_task returns True when paid, False on timeout

        Example:
            invoice, task = await wallet.mint_async(100)
            print(f"Pay: {invoice}")
            # Do other things...
            paid = await task  # Wait for payment
        """
        target_mint_url = mint_url or self._primary_mint_url()
        ## TODO maybe better self._get_mint(mint_url) # returns primary mint if None
        ## then mint.create_quote(amount)
        invoice, quote_id = await self.create_quote(amount, target_mint_url)
        mint = self._get_mint(target_mint_url)

        async def poll_payment() -> bool:
            start_time = time.time()
            poll_interval = 1.0

            while (time.time() - start_time) < timeout:
                # Check quote status and mint if paid
                quote_status, new_proofs = await mint.check_quote_status_and_mint(
                    quote_id,
                    amount,
                    minted_quotes=self._minted_quotes,
                    mint_url=target_mint_url,  # TODO this should not be needed, mint instance should contain url
                )

                # If new proofs were minted, publish wallet events
                if new_proofs:
                    # Convert dict proofs to ProofDict
                    proof_dicts: list[ProofDict] = []
                    for proof in new_proofs:
                        proof_dicts.append(
                            ProofDict(
                                id=proof["id"],
                                amount=proof["amount"],
                                secret=proof["secret"],
                                C=proof["C"],
                                mint=proof["mint"],
                            )
                        )

                    # Publish token event
                    event_manager = await self._ensure_event_manager()
                    token_event_id = await event_manager.publish_token_event(
                        proof_dicts
                    )

                    # Publish spending history
                    mint_amount = sum(p["amount"] for p in new_proofs)
                    await event_manager.publish_spending_history(
                        direction="in",
                        amount=mint_amount,
                        created_token_ids=[token_event_id],
                    )

                if quote_status.get("paid"):
                    return True

                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.2, 5.0)

            return False

        # Create background task
        task = asyncio.create_task(poll_payment())
        return invoice, task

    # ─────────────────────────────── Send ─────────────────────────────────────

    async def melt(
        self,
        invoice: str,
        *,
        target_mint: str | None = None,  # TODO remove because keyset contain mint info
        target_keyset_id: str | None = None,  # TODO implement
    ) -> None:
        """Pay a Lightning invoice by melting tokens with automatic multi-mint support.

        Args:
            invoice: BOLT-11 Lightning invoice to pay
            target_mint: Target mint URL (defaults to primary mint)

        Raises:
            WalletError: If insufficient balance or payment fails

        Example:
            await wallet.melt("lnbc100n1...")
        """
        try:
            invoice_amount = parse_lightning_invoice_amount(invoice, self.currency)
        except LNURLError as e:
            raise WalletError(f"Invalid Lightning invoice: {e}") from e

        # Get current state and check balance
        state = await self.fetch_wallet_state(check_proofs=True)
        total_needed = int(invoice_amount * 1.01)
        self.raise_if_insufficient_balance(state.total_balance_sat, total_needed)

        if target_mint is None:
            mint_balances = state.mint_balances
            if not mint_balances:
                raise WalletError("No mints available")
            target_mint = max(mint_balances, key=lambda k: mint_balances[k])
            if mint_balances[target_mint] < total_needed:
                await self.rebalance_until_target(target_mint, total_needed)
                return await self.melt(invoice, target_mint=target_mint)

        # Create melt quote to get fees
        mint = self._get_mint(target_mint)
        melt_quote = await mint.create_melt_quote(unit=self.currency, request=invoice)
        fee_reserve = melt_quote.get("fee_reserve", 0)
        total_needed = invoice_amount + fee_reserve
        # TODO: check if we have enough balance in the target mint with real fee caclulation

        # Select proofs for the total amount needed (invoice + fees)
        selected_proofs, consumed_proofs = await self._select_proofs(
            state.proofs, total_needed, target_mint
        )

        # Convert selected proofs to mint format
        mint_proofs = [self._proofdict_to_mint_proof(p) for p in selected_proofs]

        # Execute the melt operation
        # Cast to ProofComplete since melt expects it (ProofComplete extends Proof with optional fields)
        melt_resp = await mint.melt(
            quote=melt_quote["quote"], inputs=cast(list[ProofComplete], mint_proofs)
        )

        # Check if payment was successful
        if not melt_resp.get("paid", False):
            raise WalletError(
                f"Lightning payment failed. State: {melt_resp.get('state', 'unknown')}"
            )

        # Handle any change returned from the mint
        change_proofs: list[ProofDict] = []
        if "change" in melt_resp and melt_resp["change"]:
            # TODO: handle change
            # Convert BlindedSignatures to ProofDict format
            # This would require unblinding logic, but for now we'll skip change handling
            # In practice, most melts shouldn't have change if amounts are selected properly
            pass

        # Mark the consumed input proofs as spent
        await self._mark_proofs_as_spent(consumed_proofs)

        # Store any change proofs
        if change_proofs:
            await self.store_proofs(change_proofs)

        # Publish spending history
        event_manager = await self._ensure_event_manager()
        await event_manager.publish_spending_history(
            direction="out",
            amount=invoice_amount,  # The actual invoice amount paid
            destroyed_token_ids=[],  # Will be handled by _mark_proofs_as_spent
        )

    async def send(
        self,
        amount: int,
        target_mint: str | None = None,  # TODO remove bc keyset contains mint info
        keyset_id: str | None = None,  # TODO implement
        *,
        token_version: int = 4,  # Default to V4 (CashuB)
        unit: CurrencyUnit
        | None = None,  # TODO remove bc keyset should handle currency
    ) -> str:
        """Create a Cashu token for sending.

        Selects proofs worth exactly the specified amount and returns a
        Cashu token string. The new proof selection automatically handles
        splitting proofs to achieve exact amounts.

        Args:
            amount: Amount to send in the specified currency unit
            target_mint: Target mint URL (defaults to primary mint)
            token_version: Token format version (3 for CashuA, 4 for CashuB)
            unit: Currency unit to send (defaults to wallet's currency)
            keyset_id: Specific keyset ID to send from

        Returns:
            Cashu token string that can be sent to another wallet

        Raises:
            WalletError: If insufficient balance or operation fails
            ValueError: If unsupported token version

        Example:
            # Send using V4 format (default)
            token = await wallet.send(100)

            # Send using V3 format
            token = await wallet.send(100, token_version=3)

            # Send USD tokens
            token = await wallet.send(50, unit="usd")

            # Send from specific keyset
            token = await wallet.send(100, keyset_id="abc123...")
        """
        if token_version not in [3, 4]:
            raise ValueError(f"Unsupported token version: {token_version}. Use 3 or 4.")

        # Use specified unit or default to wallet currency
        send_unit = unit or self.currency

        # Get wallet state and filter proofs by unit/keyset
        state = await self.fetch_wallet_state(check_proofs=True)

        # Filter proofs based on unit and keyset_id
        filtered_proofs = state.proofs
        if keyset_id:
            # If keyset_id specified, use only proofs from that keyset
            filtered_proofs = [p for p in filtered_proofs if p.get("id") == keyset_id]
            if not filtered_proofs:
                raise WalletError(f"No proofs found for keyset {keyset_id}")

            # Verify keyset matches requested unit
            keyset_info = self.keyset_manager.get_keyset(keyset_id)
            if keyset_info and keyset_info.unit != send_unit:
                raise WalletError(
                    f"Keyset {keyset_id} uses {keyset_info.unit}, but requested {send_unit}"
                )
        else:
            # Filter by currency unit
            unit_filtered = []
            for proof in filtered_proofs:
                proof_keyset = self.keyset_manager.get_keyset(proof.get("id", ""))
                if proof_keyset and proof_keyset.unit == send_unit:
                    unit_filtered.append(proof)
            filtered_proofs = unit_filtered

            if not filtered_proofs:
                raise WalletError(f"No proofs found for currency {send_unit}")

        # Calculate balance from filtered proofs
        filtered_balance = sum(p["amount"] for p in filtered_proofs)

        if target_mint is None:
            # Find mint with balance in the requested currency
            mint_urls = set(p.get("mint", "") for p in filtered_proofs)
            if not mint_urls:
                raise WalletError(f"No mints found with {send_unit} balance")

            # Use mint with highest balance
            best_mint = ""
            best_balance = 0
            for mint_url in mint_urls:
                mint_proofs = [p for p in filtered_proofs if p.get("mint") == mint_url]
                mint_balance = sum(p["amount"] for p in mint_proofs)
                if mint_balance > best_balance:
                    best_balance = mint_balance
                    best_mint = mint_url
            target_mint = best_mint

        if filtered_balance < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} {send_unit}, "
                f"but have {filtered_balance}"
            )

        selected_proofs, consumed_proofs = await self._select_proofs(
            filtered_proofs, amount, target_mint
        )

        token = self._serialize_proofs_for_token(
            selected_proofs, target_mint, token_version
        )

        # Mark the consumed input proofs as spent (not the output proofs!)
        # This creates proper NIP-60 state transitions with rollover events
        await self._mark_proofs_as_spent(consumed_proofs)

        # Note: Spending history is now created automatically in _mark_proofs_as_spent
        # TODO: store pending token somewhere to check on status and potentially undo

        return token

    async def send_to_lnurl(
        self,
        lnurl: str,
        amount: int,
        *,
        unit: CurrencyUnit | None = None,
        keyset_id: str | None = None,
    ) -> int:
        """Send funds to an LNURL address.

        Args:
            lnurl: LNURL string (can be lightning:, user@host, bech32, or direct URL)
            amount: Amount to send in the specified currency unit
            unit: Currency unit to send (defaults to wallet's currency)
            keyset_id: Specific keyset ID to send from

        Returns:
            Amount actually paid in the specified currency unit

        Raises:
            WalletError: If amount is outside LNURL limits or insufficient balance
            LNURLError: If LNURL operations fail

        Example:
            # Send 1000 sats to a Lightning Address
            paid = await wallet.send_to_lnurl("user@getalby.com", 1000)
            print(f"Paid {paid} sats")

            # Send USD to Lightning Address
            paid = await wallet.send_to_lnurl("user@getalby.com", 50, unit="usd")
        """

        # Use specified unit or default to wallet currency
        send_unit = unit or self.currency

        # Get current balance for the specified unit
        state = await self.fetch_wallet_state(check_proofs=True)

        # Get balance for the specific unit
        unit_balance = state.balance_by_currency.get(send_unit, 0)

        # Estimate fees (1% with minimum)
        estimated_fee_sats = max(amount * 0.01, 2)
        if send_unit == "msat":
            estimated_fee = estimated_fee_sats * 1000
        else:
            estimated_fee = estimated_fee_sats

        if unit_balance < amount + estimated_fee:
            raise WalletError(
                f"Insufficient balance. Need at least {amount + estimated_fee} {send_unit} "
                f"(amount: {amount} + estimated fees: {estimated_fee} {send_unit}), but have {unit_balance}"
            )

        # Get LNURL data
        lnurl_data = await get_lnurl_data(lnurl)

        # Convert amounts based on currency
        if send_unit == "sat":
            amount_msat = amount * 1000
            min_sendable_sat = lnurl_data["min_sendable"] // 1000
            max_sendable_sat = lnurl_data["max_sendable"] // 1000
            unit_str = "sat"
        elif send_unit == "msat":
            amount_msat = amount
            min_sendable_sat = lnurl_data["min_sendable"]
            max_sendable_sat = lnurl_data["max_sendable"]
            unit_str = "msat"
        else:
            raise WalletError(f"Currency {send_unit} not supported for LNURL")

        # Check amount limits
        if not (
            lnurl_data["min_sendable"] <= amount_msat <= lnurl_data["max_sendable"]
        ):
            raise WalletError(
                f"Amount {amount} {unit_str} is outside LNURL limits "
                f"({min_sendable_sat} - {max_sendable_sat} {unit_str})"
            )
        print(amount_msat, min_sendable_sat, max_sendable_sat)

        # Get Lightning invoice
        bolt11_invoice, invoice_data = await get_lnurl_invoice(
            lnurl_data["callback_url"], amount_msat
        )
        print(bolt11_invoice)

        # Pay the invoice using melt
        await self.melt(bolt11_invoice)
        return amount  # Return the amount we intended to pay

    async def roll_over_proofs(
        self,
        *,
        spent_proofs: list[ProofDict],
        unspent_proofs: list[ProofDict],
        deleted_event_ids: list[str],
    ) -> str:
        """Roll over unspent proofs after a partial spend and return new token id."""
        # TODO: Implement roll over logic
        return ""

    # ───────────────────────── Proof Management ─────────────────────────────────

    async def create_quote(self, amount: int, mint_url: str) -> tuple[str, str]:
        """Create a Lightning invoice (quote) at the mint and return the BOLT-11 string and quote ID.

        Returns:
            Tuple of (lightning_invoice, quote_id)
        """
        mint = self._get_mint(mint_url)

        # Create mint quote
        quote_resp = await mint.create_mint_quote(
            unit=self.currency,
            amount=amount,
        )

        # Optionally publish quote tracker event
        # (skipping for simplicity)

        # TODO: Implement quote tracking as per NIP-60:
        # await self.publish_quote_tracker(
        #     quote_id=quote_resp["quote"],
        #     mint_url=mint_url,
        #     expiration=int(time.time()) + 14 * 24 * 60 * 60  # 2 weeks
        # )

        return quote_resp.get("request", ""), quote_resp.get(
            "quote", ""
        )  # Return both invoice and quote_id

    async def _consolidate_proofs(
        self, proofs: list[ProofDict], target_mint: str | None = None
    ) -> None:
        """Cleanup proofs by deleting events and updating wallet state.

        Consolidates proofs into optimal denominations and ensures they are
        properly stored on Nostr.

        Args:
            proofs: Proofs to consolidate (if None, consolidates all wallet proofs)
            target_mint: If provided, only consolidate proofs for this mint
        """
        # Get current wallet state if no proofs provided
        if not proofs:
            state = await self.fetch_wallet_state(check_proofs=True)
            proofs = state.proofs

        # Process each mint
        for mint_url, mint_proofs in state.proofs_by_mints.items():
            if not mint_proofs:
                continue

            # Calculate current balance for this mint
            current_balance = sum(p["amount"] for p in mint_proofs)

            # Check if already optimally denominated
            current_denoms: dict[int, int] = {}
            for proof in mint_proofs:
                amount = proof["amount"]
                current_denoms[amount] = current_denoms.get(amount, 0) + 1

            # Calculate optimal denominations for the balance
            optimal_denoms = self._calculate_optimal_denominations(current_balance)

            # Check if current denominations match optimal
            needs_consolidation = False
            for denom, count in optimal_denoms.items():
                if current_denoms.get(denom, 0) != count:
                    needs_consolidation = True
                    break

            if not needs_consolidation:
                continue  # Already optimal

            try:
                # Use the new abstracted swap method
                new_proofs = await self._swap_proof_denominations(
                    mint_proofs, optimal_denoms, mint_url
                )

                # Store new proofs on Nostr
                await self.store_proofs(new_proofs)
            except Exception as e:
                print(f"Warning: Failed to consolidate proofs for {mint_url}: {e}")
                continue

    def _calculate_optimal_denominations(
        self, amount: int, keyset_id: str | None = None, mint_url: str | None = None
    ) -> dict[int, int]:
        """Calculate optimal denomination breakdown for an amount.

        Args:
            amount: Amount to split into denominations
            keyset_id: Specific keyset to use (optional)
            mint_url: Mint URL to find keyset from (optional)

        Returns:
            Dict of denomination -> count
        """
        from .denominations import DenominationSystem

        # Try to get keyset info for dynamic denominations
        keyset_info = None

        if keyset_id:
            keyset_info = self.keyset_manager.get_keyset(keyset_id)
        elif mint_url:
            # Find best keyset for the mint and currency
            keyset_info = self.keyset_manager.find_keyset(mint_url, self.currency)

        if keyset_info:
            # Use dynamic denominations from keyset
            available_denoms = DenominationSystem.get_keyset_denominations(keyset_info)
            return DenominationSystem.calculate_optimal_split(amount, available_denoms)
        else:
            # Fallback to default powers of 2
            return DenominationSystem._default_split(amount)

    async def _select_proofs(
        self, proofs: list[ProofDict], amount: int, target_mint: str
    ) -> tuple[list[ProofDict], list[ProofDict]]:
        """Select proofs for spending a specific amount using optimal selection.

        Uses a greedy algorithm to minimize the number of proofs and change.

        Args:
            proofs: Available proofs to select from
            amount: Amount to select

        Returns:
            Tuple of (selected_output_proofs, consumed_input_proofs)
            - selected_output_proofs: Proofs that sum to exactly the requested amount
            - consumed_input_proofs: Original proofs that were consumed in the process

        Raises:
            WalletError: If insufficient proofs available
        """
        # Validate proofs first
        valid_proofs = await self._validate_proofs_with_cache(proofs)
        valid_available = sum(p["amount"] for p in valid_proofs)

        if valid_available < amount:
            raise WalletError(
                f"Insufficient balance: need {amount}, have {valid_available}"
            )

        if target_mint is None:
            target_mint = self._get_primary_mint_url()

        # check if enough balance in proofs from target mint
        target_mint_proofs = [p for p in valid_proofs if p.get("mint") == target_mint]
        target_mint_balance = sum(p["amount"] for p in target_mint_proofs)
        if target_mint_balance < amount:
            await self.transfer_balance_to_mint(
                amount - target_mint_balance, target_mint
            )
            state = await self.fetch_wallet_state(check_proofs=True)
            return await self._select_proofs(state.proofs, amount, target_mint)

        # Use greedy algorithm to select minimum proofs needed
        target_mint_proofs.sort(key=lambda p: p["amount"], reverse=True)
        selected_input_proofs: list[ProofDict] = []
        selected_total = 0

        for proof in target_mint_proofs:
            if selected_total >= amount:
                break
            selected_input_proofs.append(proof)
            selected_total += int(proof["amount"])  # Ensure integer arithmetic

        if selected_total < amount:
            raise WalletError(
                f"Insufficient balance in target mint: need {amount}, have {target_mint_balance}"
            )

        # If we have exact amount, return the proofs
        if selected_total == amount:
            return selected_input_proofs, selected_input_proofs

        # Otherwise, we need to split proofs to get exact amount
        # Calculate expected input fees for the swap
        mint = self._get_mint(target_mint)
        input_fees = await self.calculate_total_input_fees(mint, selected_input_proofs)

        # Adjust target denominations to account for fees
        # The equation is: inputs - fees = outputs
        # So: outputs = inputs - fees = selected_total - input_fees
        output_amount = int(selected_total - input_fees)

        # Recalculate denominations for the actual output amount
        send_denoms = self._calculate_optimal_denominations(amount)
        change_amount = int(output_amount - amount)  # Ensure integer

        if change_amount < 0:
            raise WalletError(
                f"Insufficient amount after fees: need {amount}, have {output_amount} "
                f"(after {input_fees} sats in fees)"
            )

        change_denoms = self._calculate_optimal_denominations(change_amount)

        # Combine send and change denominations
        target_denoms = send_denoms.copy()
        for denom, count in change_denoms.items():
            target_denoms[denom] = target_denoms.get(denom, 0) + count

        # Swap the selected proofs for the target denominations
        new_proofs = await self._swap_proof_denominations(
            selected_input_proofs, target_denoms, target_mint
        )

        # Select exactly the amount needed for sending
        selected_proofs: list[ProofDict] = []
        change_proofs: list[ProofDict] = []
        used_proofs: set[str] = set()
        remaining_amount = amount

        # Select proofs to meet the exact amount
        for proof in sorted(new_proofs, key=lambda p: p["amount"], reverse=True):
            proof_id = f"{proof['secret']}:{proof['C']}"
            if proof_id in used_proofs:
                continue

            if remaining_amount > 0 and proof["amount"] <= remaining_amount:
                selected_proofs.append(proof)
                used_proofs.add(proof_id)
                remaining_amount -= proof["amount"]
            else:
                # This is change
                change_proofs.append(proof)

        if remaining_amount > 0:
            raise WalletError(
                f"Could not select exact amount: {remaining_amount} sats short"
            )

        # Store only the change proofs (not the ones we're sending!)
        await self.store_proofs(change_proofs)

        return selected_proofs, selected_input_proofs

    async def _swap_proof_denominations(
        self,
        proofs: list[ProofDict],
        target_denominations: dict[int, int],
        mint_url: str,
    ) -> list[ProofDict]:
        """Swap proofs to specific target denominations.

        This method abstracts the process of swapping proofs for new ones with
        specific denominations. It handles keysets, blinding, swapping, and unblinding.

        Args:
            proofs: List of proofs to swap
            target_denominations: Dict of denomination -> count
                                 e.g., {1: 5, 2: 3, 4: 1} = 5x1sat, 3x2sat, 1x4sat
            mint_url: Mint URL (defaults to first proof's mint or wallet's primary)

        Returns:
            List of new proofs with target denominations

        Raises:
            WalletError: If swap fails or amounts don't match
        """
        if not proofs:
            return []

        if not mint_url:
            raise WalletError("No mint URL available")

        # Get mint instance
        mint = self._get_mint(mint_url)

        # Calculate input fees for these proofs
        input_fees = await self.calculate_total_input_fees(mint, proofs)

        # Calculate total amounts
        input_amount = sum(p["amount"] for p in proofs)
        target_amount = sum(
            denom * count for denom, count in target_denominations.items()
        )

        # The correct balance equation is: inputs - fees = outputs
        expected_output_amount = input_amount - input_fees

        if target_amount != expected_output_amount:
            raise WalletError(
                f"Amount mismatch: input={input_amount}, fees={input_fees}, "
                f"expected_output={expected_output_amount}, target={target_amount}"
            )

        # TODO: Implement this
        # check if proofs are already in target denominations
        # return proofs if they are

        # Convert to mint proof format
        mint_proofs = [self._proofdict_to_mint_proof(p) for p in proofs]

        # Get active keyset filtered by currency unit
        keysets_resp = await mint.get_keysets()
        keysets = keysets_resp.get("keysets", [])
        active_keysets = [
            ks
            for ks in keysets
            if ks.get("active", True) and ks.get("unit") == self.currency
        ]

        if not active_keysets:
            raise WalletError(
                f"No active keysets found for currency unit '{self.currency}'"
            )

        keyset_id = str(active_keysets[0]["id"])

        # Create blinded messages for target denominations
        outputs: list[BlindedMessage] = []
        secrets: list[str] = []
        blinding_factors: list[str] = []

        for denomination, count in sorted(target_denominations.items()):
            for _ in range(count):
                secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                    denomination, keyset_id
                )
                outputs.append(blinded_msg)
                secrets.append(secret)
                blinding_factors.append(r_hex)

        # Perform swap
        # Cast to ProofComplete since swap expects it (ProofComplete extends Proof with optional fields)
        swap_resp = await mint.swap(
            inputs=cast(list[ProofComplete], mint_proofs), outputs=outputs
        )

        # Get mint keys for unblinding
        keys_resp = await mint.get_keys(keyset_id)
        mint_keysets = keys_resp.get("keysets", [])
        mint_keys = None

        for ks in mint_keysets:
            if str(ks.get("id")) == keyset_id:
                keys_data: dict[str, str] | str = ks.get("keys", {})
                if isinstance(keys_data, dict) and keys_data:
                    mint_keys = keys_data
                    break

        if not mint_keys:
            raise WalletError("Could not find mint keys for unblinding")

        # Unblind signatures to create new proofs
        new_proofs: list[ProofDict] = []
        for i, sig in enumerate(swap_resp["signatures"]):
            # Get the public key for this amount
            amount = sig["amount"]
            mint_pubkey = get_mint_pubkey_for_amount(mint_keys, amount)
            if not mint_pubkey:
                raise WalletError(f"Could not find mint public key for amount {amount}")

            # Unblind the signature
            C_ = PublicKey(bytes.fromhex(sig["C_"]))
            r = bytes.fromhex(blinding_factors[i])
            C = unblind_signature(C_, r, mint_pubkey)

            new_proofs.append(
                ProofDict(
                    id=sig["id"],
                    amount=sig["amount"],
                    secret=secrets[
                        i
                    ],  # Already hex from create_blinded_message_with_secret
                    C=C.format(compressed=True).hex(),
                    mint=mint_url,
                )
            )

        return new_proofs

    async def _mark_proofs_as_spent(self, spent_proofs: list[ProofDict]) -> None:
        """Mark proofs as spent following NIP-60 state transitions.

        This creates proper rollover events with 'del' fields to mark old events as superseded,
        ensuring wallet state consistency even on relays that don't support deletion events.

        Args:
            spent_proofs: List of proofs to mark as spent
        """
        if not spent_proofs:
            return

        # 1. Get current state to find which events contain spent proofs
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )

        if not state.proof_to_event_id:
            # No mapping available, nothing to rollover
            return

        # 2. Find which events need updating (contain spent proofs)
        spent_proof_ids = {f"{p['secret']}:{p['C']}" for p in spent_proofs}
        events_with_spent_proofs: dict[str, list[ProofDict]] = {}

        # Group all proofs by their event IDs
        for proof in state.proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            event_id = state.proof_to_event_id.get(proof_id)

            if event_id and event_id != "__pending__":
                if event_id not in events_with_spent_proofs:
                    events_with_spent_proofs[event_id] = []
                events_with_spent_proofs[event_id].append(proof)

        # 3. Process each affected event
        events_to_delete = []
        new_event_ids = []

        for event_id, event_proofs in events_with_spent_proofs.items():
            # Check if this event contains any spent proofs
            has_spent_proofs = any(
                f"{p['secret']}:{p['C']}" in spent_proof_ids for p in event_proofs
            )

            if not has_spent_proofs:
                continue

            # Find unspent proofs from this event
            unspent_proofs = [
                p
                for p in event_proofs
                if f"{p['secret']}:{p['C']}" not in spent_proof_ids
            ]

            events_to_delete.append(event_id)

            if unspent_proofs:
                # Create new event with remaining proofs
                try:
                    event_manager = await self._ensure_event_manager()
                    new_id = await event_manager.publish_token_event(
                        unspent_proofs, deleted_token_ids=[event_id]
                    )
                    new_event_ids.append(new_id)
                except Exception as e:
                    print(
                        f"Warning: Failed to create rollover event for {event_id}: {e}"
                    )
                    # Continue processing other events

        # 4. Try to delete old events (best effort - don't fail if relay doesn't support it)
        for event_id in events_to_delete:
            try:
                event_manager = await self._ensure_event_manager()
                await event_manager.delete_token_event(event_id)
            except Exception as e:
                # Deletion failed - that's okay, the 'del' field handles supersession
                print(
                    f"Note: Could not delete event {event_id} (relay may not support deletions): {e}"
                )

        # 5. Create spending history (optional but recommended)
        if events_to_delete or new_event_ids:
            try:
                event_manager = await self._ensure_event_manager()
                await event_manager.publish_spending_history(
                    direction="out",
                    amount=sum(p["amount"] for p in spent_proofs),
                    created_token_ids=new_event_ids,
                    destroyed_token_ids=events_to_delete,
                )
            except Exception as e:
                print(f"Warning: Failed to create spending history: {e}")

        # 6. Update local cache for spent proofs
        for proof in spent_proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            self._cache_proof_state(proof_id, "SPENT")

    async def store_proofs(self, proofs: list[ProofDict]) -> None:
        """Make sure proofs are stored on Nostr.

        This method ensures proofs are backed up to Nostr relays for recovery.
        It handles deduplication, retries, and temporary local backup.

        Args:
            proofs: List of proofs to store

        Raises:
            WalletError: If unable to publish to any relay after retries
        """
        if not proofs:
            return  # Nothing to store

        backup_dir = Path.cwd() / "proof_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        backup_file = backup_dir / f"proofs_{timestamp}_{secrets.token_hex(8)}.json"

        backup_data = {
            "timestamp": timestamp,
            "proofs": proofs,
            "mint_urls": list(set(p.get("mint", "") for p in proofs if p.get("mint"))),
        }

        try:
            with open(backup_file, "w") as f:
                json.dump(backup_data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to create local backup: {e}")

        # Check which proofs are already stored
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )
        existing_proofs = set()

        for proof in state.proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            existing_proofs.add(proof_id)

        # Filter out already stored proofs
        new_proofs = []
        for proof in proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            if proof_id not in existing_proofs:
                new_proofs.append(proof)

        if not new_proofs:
            return

        # Group new proofs by mint
        new_proofs_by_mint: dict[str, list[ProofDict]] = {}
        for proof in new_proofs:
            mint_url = proof.get("mint", "")
            if mint_url:
                if mint_url not in new_proofs_by_mint:
                    new_proofs_by_mint[mint_url] = []
                new_proofs_by_mint[mint_url].append(proof)

        # Publish token events for each mint
        published_count = 0
        failed_mints = []

        for mint_url, mint_proofs in new_proofs_by_mint.items():
            try:
                # Publish token event
                event_manager = await self._ensure_event_manager()
                event_id = await event_manager.publish_token_event(mint_proofs)
                published_count += len(mint_proofs)

                # Verify event was published by fetching it
                max_retries = 3
                retry_delay = 1.0

                for retry in range(max_retries):
                    await asyncio.sleep(retry_delay)

                    # Try to fetch the event we just published
                    # Note: fetch_wallet_events doesn't support filtering by ID,
                    # so we just trust the publish succeeded
                    # In production, could implement a specific fetch by ID method
                    await asyncio.sleep(retry_delay)

                    # For now, assume successful after a delay
                    if retry > 0:  # Give it at least one retry
                        break

                    retry_delay *= 2  # Exponential backoff
                else:
                    # Failed to verify after retries
                    print(
                        f"Warning: Could not verify token event {event_id} was published"
                    )

            except Exception as e:
                print(f"Error publishing proofs for mint {mint_url}: {e}")
                failed_mints.append(mint_url)

                # Spawn background task for retry
                asyncio.create_task(
                    self._retry_store_proofs(mint_proofs, mint_url, backup_file)
                )

        if published_count > 0:
            print(f"✅ Published {published_count} new proofs to Nostr")

        if failed_mints:
            print(f"⚠️  Failed to publish proofs for mints: {', '.join(failed_mints)}")
            print("   Background retry tasks have been started.")

    async def _retry_store_proofs(
        self, proofs: list[ProofDict], mint_url: str, backup_file: Path
    ) -> None:
        """Background task to retry storing proofs."""
        max_retries = 5
        base_delay = 10.0  # Start with 10 second delay

        for retry in range(max_retries):
            await asyncio.sleep(base_delay * (2**retry))  # Exponential backoff

            try:
                event_manager = await self._ensure_event_manager()
                event_id = await event_manager.publish_token_event(proofs)
                print(event_id)
                print(
                    f"✅ Successfully published proofs for {mint_url} on retry {retry + 1}"
                )

                # Try to clean up backup file
                try:
                    if backup_file.exists():
                        # Check if this was the last mint
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)

                        # Remove successfully stored proofs from backup
                        remaining_proofs = []
                        stored_ids = set(f"{p['secret']}:{p['C']}" for p in proofs)

                        for p in backup_data["proofs"]:
                            if f"{p['secret']}:{p['C']}" not in stored_ids:
                                remaining_proofs.append(p)

                        if remaining_proofs:
                            # Update backup with remaining proofs
                            backup_data["proofs"] = remaining_proofs
                            with open(backup_file, "w") as f:
                                json.dump(backup_data, f, indent=2)
                        else:
                            # All proofs stored - verify one more time before deletion
                            # Fetch state to ensure proofs are really on relays
                            await asyncio.sleep(2.0)  # Give relays time to propagate
                            try:
                                state = await self.fetch_wallet_state(
                                    check_proofs=False, check_local_backups=False
                                )
                                stored_proof_ids = set(
                                    f"{p['secret']}:{p['C']}" for p in state.proofs
                                )
                                all_stored = all(
                                    f"{p['secret']}:{p['C']}" in stored_proof_ids
                                    for p in proofs
                                )

                                if all_stored:
                                    backup_file.unlink()
                                    print(
                                        f"    🗑️  Verified and deleted backup: {backup_file.name}"
                                    )
                                else:
                                    print(
                                        f"    ⚠️  Keeping backup (verification failed): {backup_file.name}"
                                    )
                            except Exception as e:
                                print(
                                    f"    ⚠️  Keeping backup (verification error): {e}"
                                )
                except Exception:
                    pass  # Ignore backup cleanup errors

                return  # Success

            except Exception as e:
                if retry == max_retries - 1:
                    print(
                        f"❌ Failed to store proofs for {mint_url} after {max_retries} retries: {e}"
                    )
                    print(f"   Manual recovery may be needed from: {backup_file}")

    async def transfer_proofs(
        self, proofs: list[ProofDict], target_mint: str
    ) -> list[ProofDict]:
        """Transfer proofs to a specific mint by converting via tokens.

        Args:
            proofs: Proofs to transfer (can be from multiple source mints)
            target_mint: Target mint URL to transfer to

        Returns:
            New proofs at the target mint

        Raises:
            WalletError: If transfer fails or insufficient balance after fees
        """
        if not proofs:
            return []

        # Group proofs by source mint
        proofs_by_mint: dict[str, list[ProofDict]] = {}
        for proof in proofs:
            source_mint = proof.get("mint") or ""
            if not source_mint or source_mint == target_mint:
                # Already at target mint or no mint specified, no transfer needed
                continue
            if source_mint not in proofs_by_mint:
                proofs_by_mint[source_mint] = []
            proofs_by_mint[source_mint].append(proof)

        # If no proofs need transfer, return proofs from target mint
        target_mint_proofs = [p for p in proofs if p.get("mint") == target_mint]
        if not proofs_by_mint:
            return target_mint_proofs

        transferred_proofs: list[ProofDict] = []

        # Process each source mint
        for source_mint, mint_proofs in proofs_by_mint.items():
            try:
                # Create a temporary token from source proofs
                total_amount = sum(p["amount"] for p in mint_proofs)

                # Use V4 token format for efficiency
                token = self._serialize_proofs_for_token(mint_proofs, source_mint, 4)

                # Parse the token to get standardized format
                parsed_mint, parsed_unit, parsed_proofs = self._parse_cashu_token(token)

                # Calculate input fees for source proofs
                source_mint_instance = self._get_mint(source_mint)
                input_fees = await self.calculate_total_input_fees(
                    source_mint_instance, mint_proofs
                )

                # The amount we can actually mint at target is total - fees
                available_amount = total_amount - input_fees
                if available_amount <= 0:
                    raise WalletError(
                        f"Insufficient amount after fees: {total_amount} - {input_fees} = {available_amount}"
                    )

                # Calculate optimal denominations for the transfer amount
                target_denoms = self._calculate_optimal_denominations(available_amount)

                # Create new proofs at target mint using swap
                new_proofs = await self._create_proofs_at_mint(
                    target_mint, available_amount, target_denoms
                )

                # Only mark source proofs as spent AFTER the transfer succeeds
                await self._mark_proofs_as_spent(mint_proofs)

                transferred_proofs.extend(new_proofs)

                # Store new proofs
                await self.store_proofs(new_proofs)

            except Exception as e:
                # If transfer fails, the proofs are not marked as spent yet (good!)
                # Just propagate the error with more context
                if "Lightning payment infrastructure" in str(e):
                    raise WalletError(
                        "Multi-mint transfers require Lightning infrastructure which is not yet implemented. "
                        "Your proofs are safe and not consumed."
                    ) from e
                else:
                    raise WalletError(
                        f"Failed to transfer proofs from {source_mint}: {e}"
                    ) from e

        # Return both existing target mint proofs and newly transferred proofs
        return target_mint_proofs + transferred_proofs

    async def rebalance_until_target(self, target_mint: str, total_needed: int) -> None:
        """Rebalance until the target mint has at least the total needed."""
        raise NotImplementedError("Not implemented")  # TODO: Implement
        # mint_balances = await self.mint_balances()
        # if mint_balances[target_mint] >= total_needed:
        #     return
        # await self.transfer_balance_to_mint(total_needed, target_mint)

    async def _create_proofs_at_mint(
        self, mint_url: str, amount: int, denominations: dict[int, int]
    ) -> list[ProofDict]:
        """Create new proofs at a specific mint with given denominations.

        This is a simplified version that creates proofs without requiring
        Lightning infrastructure by using the mint's swap functionality.
        """
        from .crypto import (
            create_blinded_message_with_secret,
        )

        mint = self._get_mint(mint_url)

        # Get active keyset for this mint
        keysets_resp = await mint.get_keysets()
        keysets = keysets_resp.get("keysets", [])
        active_keysets = [
            ks
            for ks in keysets
            if ks.get("active", True) and ks.get("unit") == self.currency
        ]

        if not active_keysets:
            raise WalletError(f"No active keysets found for {mint_url}")

        keyset_id = str(active_keysets[0]["id"])

        # Create blinded messages for target denominations
        outputs: list[BlindedMessage] = []
        secrets: list[str] = []
        blinding_factors: list[str] = []

        for denomination, count in sorted(denominations.items()):
            for _ in range(count):
                secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                    denomination, keyset_id
                )
                outputs.append(blinded_msg)
                secrets.append(secret)
                blinding_factors.append(r_hex)

        # For this simplified implementation, we'll create a mint quote
        # and immediately mark it as paid (this requires mint cooperation)
        # In a full implementation, this would involve Lightning payments

        # Create mint quote
        quote_resp = await mint.create_mint_quote(
            unit=self.currency,
            amount=amount,
        )
        quote_id = quote_resp["quote"]
        print(f"Quote ID: {quote_id}")
        # TODO: Implement mint quote payment
        # Attempt to mint using the quote
        # Note: This will fail unless the invoice is actually paid
        # For now, we'll raise an error indicating Lightning payment is needed
        raise WalletError(
            f"Cross-mint transfers require Lightning payment infrastructure. "
            f"Please pay invoice: {quote_resp.get('request', 'No invoice available')} "
            f"to complete transfer of {amount} sats to {mint_url}"
        )

    async def transfer_balance_to_mint(self, amount: int, target_mint: str) -> None:
        """Transfer balance to a specific mint using optimal selection.

        Args:
            amount: Amount to transfer in sats
            target_mint: Target mint URL

        Raises:
            WalletError: If insufficient balance or transfer fails
        """
        # Get current wallet state
        state = await self.fetch_wallet_state(check_proofs=True)

        # Get all proofs not from target mint
        source_proofs = [p for p in state.proofs if p.get("mint") != target_mint]

        if not source_proofs:
            raise WalletError("No proofs available from other mints for transfer")

        # Group by mint and calculate available balance per mint
        mint_balances: dict[str, tuple[int, list[ProofDict]]] = {}
        for proof in source_proofs:
            mint_url = proof.get("mint") or ""
            if not mint_url:
                continue  # Skip proofs without mint URL
            if mint_url not in mint_balances:
                mint_balances[mint_url] = (0, [])
            balance, proofs_list = mint_balances[mint_url]
            mint_balances[mint_url] = (balance + proof["amount"], proofs_list + [proof])

        # Calculate transfer costs and net transferable amounts
        transfer_options: list[tuple[str, int, int, list[ProofDict]]] = []

        for mint_url, (balance, mint_proofs) in mint_balances.items():
            try:
                # Estimate transfer fees
                mint = self._get_mint(mint_url)
                estimated_fees = await self.calculate_total_input_fees(
                    mint, mint_proofs
                )
                net_transferable = balance - estimated_fees

                if net_transferable > 0:
                    transfer_options.append(
                        (mint_url, balance, net_transferable, mint_proofs)
                    )
            except Exception as e:
                print(f"Warning: Could not calculate fees for {mint_url}: {e}")
                # Assume 10% fee as fallback
                estimated_fees = balance // 10
                net_transferable = balance - estimated_fees
                if net_transferable > 0:
                    transfer_options.append(
                        (mint_url, balance, net_transferable, mint_proofs)
                    )

        # Sort by net transferable amount (descending)
        transfer_options.sort(key=lambda x: x[2], reverse=True)

        total_available = sum(option[2] for option in transfer_options)
        if total_available < amount:
            raise WalletError(
                f"Insufficient transferable balance: need {amount}, "
                f"have {total_available} (after estimated fees)"
            )

        # Select proofs to transfer, starting with largest balances
        remaining_needed = amount
        proofs_to_transfer: list[ProofDict] = []

        for mint_url, gross_balance, net_transferable, mint_proofs in transfer_options:
            if remaining_needed <= 0:
                break

            # Take the amount we need from this mint (up to what's available)
            take_amount = min(remaining_needed, net_transferable)

            # Select proofs greedily to meet take_amount
            selected_proofs = []
            selected_total = 0

            # Sort proofs by amount (descending) for greedy selection
            sorted_proofs = sorted(mint_proofs, key=lambda p: p["amount"], reverse=True)

            for proof in sorted_proofs:
                if selected_total >= take_amount:
                    break
                selected_proofs.append(proof)
                selected_total += proof["amount"]

            if selected_total < take_amount:
                # Need to split proofs to get exact amount
                # For simplicity, take all proofs from this mint if we need them
                selected_proofs = mint_proofs
                selected_total = gross_balance

            proofs_to_transfer.extend(selected_proofs)
            remaining_needed -= min(take_amount, selected_total)

        if remaining_needed > 0:
            raise WalletError(
                f"Could not select enough proofs: {remaining_needed} sats short"
            )

        # Perform the transfer
        try:
            await self.transfer_proofs(proofs_to_transfer, target_mint)
        except WalletError as e:
            if "Lightning payment infrastructure" in str(e):
                # For now, raise a more user-friendly error
                raise WalletError(
                    "Multi-mint transfers require Lightning infrastructure which is not yet implemented. "
                    "Please consolidate your funds to a single mint manually, or wait for this feature to be completed."
                ) from e
            raise

    async def summon_mint_with_balance(self, amount: int) -> str:
        """Summon a mint with at least the given amount of balance."""
        state = await self.fetch_wallet_state(check_proofs=True)
        total_balance = state.balance
        if total_balance * 0.99 < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} {self.currency} "
                f"(amount: {amount}), but have {total_balance}"
            )
        mint_balances = state.mint_balances
        target_mint = max(mint_balances, key=lambda k: mint_balances[k])
        if mint_balances[target_mint] < amount:
            await self.rebalance_until_target(target_mint, amount)
        return target_mint

    # ───────────────────────── Helper Methods ─────────────────────────────────

    def _get_mint(self, mint_url: str) -> Mint:
        """Get or create mint instance for URL."""
        if mint_url not in self.mints:
            self.mints[mint_url] = Mint(mint_url, client=self.mint_client)
        return self.mints[mint_url]

    async def _update_keysets_from_mint(self, mint_url: str) -> None:
        """Fetch and update keysets from a mint."""
        mint = self._get_mint(mint_url)

        try:
            # Get all keysets from the mint
            keysets_resp = await mint.get_keysets()
            keysets = keysets_resp.get("keysets", [])

            for keyset_data in keysets:
                keyset_id = str(keyset_data.get("id", ""))
                if not keyset_id:
                    continue

                # Get detailed keys for this keyset
                keys_resp = await mint.get_keys(keyset_id)
                keys_data = {}

                for ks in keys_resp.get("keysets", []):
                    if str(ks.get("id")) == keyset_id:
                        keys_data = ks.get("keys", {})
                        break

                # Create KeysetInfo and add to manager
                # Get unit and cast to CurrencyUnit type
                unit_str = keyset_data.get("unit", "sat")
                unit: CurrencyUnit = cast(CurrencyUnit, unit_str)

                keyset_info = KeysetInfo(
                    id=keyset_id,
                    mint_url=mint_url,
                    unit=unit,
                    active=keyset_data.get("active", True),
                    input_fee_ppk=keyset_data.get("input_fee_ppk", 0),
                    keys=keys_data if isinstance(keys_data, dict) else {},
                )

                self.keyset_manager.add_keyset(keyset_info)

        except Exception as e:
            print(f"Warning: Failed to update keysets from {mint_url}: {e}")

    def _serialize_proofs_for_token(
        self, proofs: list[ProofDict], mint_url: str, token_version: int
    ) -> str:
        """Serialize proofs into a Cashu token format (V3 or V4)."""
        if token_version == 3:
            return self._serialize_proofs_v3(proofs, mint_url)
        elif token_version == 4:
            return self._serialize_proofs_v4(proofs, mint_url)
        else:
            raise ValueError(f"Unsupported token version: {token_version}")

    def _serialize_proofs_v3(self, proofs: list[ProofDict], mint_url: str) -> str:
        """Serialize proofs into CashuA (V3) token format."""
        # Proofs are already stored with hex secrets internally
        token_proofs = []
        for proof in proofs:
            token_proofs.append(
                {
                    "id": proof["id"],
                    "amount": proof["amount"],
                    "secret": proof["secret"],  # Already hex
                    "C": proof["C"],
                }
            )

        # CashuA token format: cashuA<base64url(json)>
        token_data = {
            "token": [{"mint": mint_url, "proofs": token_proofs}],
            "unit": self.currency or "sat",
            "memo": "NIP-60 wallet transfer",
        }
        json_str = json.dumps(token_data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")
        return f"cashuA{encoded}"

    def _serialize_proofs_v4(self, proofs: list[ProofDict], mint_url: str) -> str:
        """Serialize proofs into CashuB (V4) token format using CBOR."""
        if cbor2 is None:
            raise ImportError("cbor2 library required for CashuB (V4) tokens")

        # Group proofs by keyset ID for V4 format
        proofs_by_keyset: dict[str, list[ProofDict]] = {}
        for proof in proofs:
            keyset_id = proof["id"]
            if keyset_id not in proofs_by_keyset:
                proofs_by_keyset[keyset_id] = []
            proofs_by_keyset[keyset_id].append(proof)

        # Build V4 token structure
        tokens = []
        for keyset_id, keyset_proofs in proofs_by_keyset.items():
            # Convert keyset ID from hex string to bytes
            keyset_id_bytes = bytes.fromhex(keyset_id)

            # Convert proofs to V4 format
            v4_proofs = []
            for proof in keyset_proofs:
                v4_proofs.append(
                    {
                        "a": proof["amount"],  # amount
                        "s": proof["secret"],  # secret (already hex string)
                        "c": bytes.fromhex(proof["C"]),  # C as bytes
                    }
                )

            tokens.append(
                {
                    "i": keyset_id_bytes,  # keyset id as bytes
                    "p": v4_proofs,  # proofs array
                }
            )

        # CashuB token structure
        token_data = {
            "m": mint_url,  # mint URL
            "u": self.currency or "sat",  # unit
            "t": tokens,  # tokens array
        }

        # Encode with CBOR and base64url
        cbor_bytes = cbor2.dumps(token_data)
        encoded = base64.urlsafe_b64encode(cbor_bytes).decode().rstrip("=")
        return f"cashuB{encoded}"

    def _parse_cashu_token(
        self, token: str
    ) -> tuple[str, CurrencyUnit, list[ProofDict]]:
        """Parse Cashu token and return (mint_url, unit, proofs)."""
        if not token.startswith("cashu"):
            raise ValueError("Invalid token format")

        # Check token version
        if token.startswith("cashuA"):
            # Version 3 - JSON format
            encoded = token[6:]  # Remove "cashuA"
            # Add correct padding – (-len) % 4 equals 0,1,2,3
            encoded += "=" * ((-len(encoded)) % 4)

            decoded = base64.urlsafe_b64decode(encoded).decode()
            token_data = json.loads(decoded)

            # Extract mint and proofs from JSON format
            mint_info = token_data["token"][0]
            # Safely get unit, defaulting to "sat" if not present (as per Cashu V3 common practice)
            unit_str = token_data.get("unit", "sat")
            # Cast to CurrencyUnit - validate it's a known unit
            token_unit: CurrencyUnit = cast(CurrencyUnit, unit_str)
            token_proofs = mint_info["proofs"]

            # Return proofs with hex secrets (standard Cashu format)
            parsed_proofs: list[ProofDict] = []
            for proof in token_proofs:
                parsed_proofs.append(
                    ProofDict(
                        id=proof["id"],
                        amount=proof["amount"],
                        secret=proof["secret"],  # Already hex in Cashu tokens
                        C=proof["C"],
                        mint=mint_info["mint"],
                    )
                )

            return mint_info["mint"], token_unit, parsed_proofs

        elif token.startswith("cashuB"):
            # Version 4 - CBOR format
            if cbor2 is None:
                raise ImportError("cbor2 library required for cashuB tokens")

            encoded = token[6:]  # Remove "cashuB"
            # Add padding for base64
            encoded += "=" * ((-len(encoded)) % 4)

            decoded_bytes = base64.urlsafe_b64decode(encoded)
            token_data = cbor2.loads(decoded_bytes)

            # Extract from CBOR format - different structure
            # 'm' = mint URL, 'u' = unit, 't' = tokens array
            mint_url = token_data["m"]
            unit_str = token_data["u"]
            # Cast to CurrencyUnit
            cbor_unit: CurrencyUnit = cast(CurrencyUnit, unit_str)
            proofs = []

            # Each token in 't' has 'i' (keyset id) and 'p' (proofs)
            for token_entry in token_data["t"]:
                keyset_id = token_entry["i"].hex()  # Convert bytes to hex
                for proof in token_entry["p"]:
                    # CBOR format already has hex secret
                    # Convert CBOR proof format to our ProofDict format
                    proofs.append(
                        ProofDict(
                            id=keyset_id,
                            amount=proof["a"],
                            secret=proof["s"],  # Already hex in CBOR format
                            C=proof["c"].hex(),  # Convert bytes to hex
                            mint=mint_url,
                        )
                    )

            return mint_url, cbor_unit, proofs
        else:
            raise ValueError(f"Unknown token version: {token[:7]}")

    def raise_if_insufficient_balance(self, balance: int, amount: int) -> None:
        if balance < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} {self.currency} "
                f"(amount: {amount}), but have {balance}"
            )

    # ───────────────────────── Proof Validation ────────────────────────────────

    def _compute_proof_y_values(self, proofs: list[ProofDict]) -> list[str]:
        """Compute Y values for proofs to use in check_state API.

        Args:
            proofs: List of proof dictionaries

        Returns:
            List of Y values (hex encoded compressed public keys)
        """
        y_values = []
        for proof in proofs:
            secret_hex = proof["secret"]  # Already hex internally

            # Hash to curve point using UTF-8 bytes of hex string (Cashu standard)
            secret_utf8_bytes = secret_hex.encode("utf-8")
            Y = hash_to_curve(secret_utf8_bytes)
            # Convert to compressed hex format
            y_hex = Y.format(compressed=True).hex()
            y_values.append(y_hex)
        return y_values

    def _is_proof_state_cached(self, proof_id: str) -> tuple[bool, str | None]:
        """Check if proof state is cached and still valid."""
        if proof_id in self._proof_state_cache:
            cache_entry = self._proof_state_cache[proof_id]
            timestamp = float(cache_entry.get("timestamp", 0))
            if time.time() - timestamp < self._cache_expiry:
                return True, cache_entry.get("state")
        return False, None

    def _cache_proof_state(self, proof_id: str, state: str) -> None:
        """Cache proof state with timestamp."""
        self._proof_state_cache[proof_id] = {
            "state": state,
            "timestamp": str(time.time()),
        }

        # Track spent proofs separately for faster lookup
        if state == "SPENT":
            self._known_spent_proofs.add(proof_id)

    def clear_spent_proof_cache(self) -> None:
        """Clear the spent proof cache to prevent memory growth."""
        self._proof_state_cache.clear()
        self._known_spent_proofs.clear()

    async def _validate_proofs_with_cache(
        self, proofs: list[ProofDict]
    ) -> list[ProofDict]:
        """Validate proofs using cache to avoid re-checking spent proofs."""
        valid_proofs = []
        proofs_to_check: list[ProofDict] = []

        # First pass: check cache and filter out known spent proofs
        for proof in proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"

            # Skip known spent proofs immediately
            if proof_id in self._known_spent_proofs:
                continue

            is_cached, cached_state = self._is_proof_state_cached(proof_id)
            if is_cached:
                if cached_state == "UNSPENT":
                    valid_proofs.append(proof)
                # SPENT proofs are filtered out (don't add to valid_proofs)
            else:
                proofs_to_check.append(proof)

        if proofs_to_check:
            for mint_url, mint_proofs in self._sort_proofs_by_mint(
                proofs_to_check
            ).items():
                try:
                    mint = self._get_mint(mint_url)
                    y_values = self._compute_proof_y_values(mint_proofs)
                    state_response = await mint.check_state(Ys=y_values)

                    for i, proof in enumerate(mint_proofs):
                        proof_id = f"{proof['secret']}:{proof['C']}"
                        if i < len(state_response["states"]):
                            state_info = state_response["states"][i]
                            state = state_info.get("state", "UNKNOWN")

                            # Cache the result
                            self._cache_proof_state(proof_id, state)

                            # Only include unspent proofs
                            if state == "UNSPENT":
                                valid_proofs.append(proof)

                        else:
                            # No state info - assume valid but don't cache
                            valid_proofs.append(proof)

                except Exception:
                    # If validation fails, include proofs but don't cache
                    valid_proofs.extend(mint_proofs)

        return valid_proofs

    async def fetch_wallet_state(
        self, *, check_proofs: bool = True, check_local_backups: bool = True
    ) -> EnhancedWalletState:
        """Fetch wallet, token events and compute balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning state
            check_local_backups: If True, scan local backups for missing proofs
        """
        # Clear spent proof cache to ensure fresh validation
        if check_proofs:
            self.clear_spent_proof_cache()

        # Fetch all wallet-related events
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )

        # Find the newest wallet event (replaceable events should use latest timestamp)
        wallet_events = [e for e in all_events if e["kind"] == EventKind.Wallet]
        wallet_event = None
        if wallet_events:
            # Sort by created_at timestamp and take the newest
            wallet_event = max(wallet_events, key=lambda e: e["created_at"])

        # Parse wallet metadata
        # TODO this should not always fetch the wallet event
        if wallet_event:
            try:
                decrypted = nip44_decrypt(wallet_event["content"], self._privkey)
                wallet_data = json.loads(decrypted)

                # Update mint URLs from wallet event (only if event contains mint URLs)
                event_mint_urls = []
                for item in wallet_data:
                    if item[0] == "mint":
                        event_mint_urls.append(item[1])
                    elif item[0] == "privkey":
                        self.wallet_privkey = item[1]

                # Only update mint URLs if the event actually contains some
                if event_mint_urls:
                    self.mint_urls.update(event_mint_urls)
            except Exception as e:
                # Skip wallet event if it can't be decrypted
                print(f"Warning: Could not decrypt wallet event: {e}")

        # Collect token events
        token_events = [e for e in all_events if e["kind"] == EventKind.Token]

        # Track deleted token events
        deleted_ids = set()
        for event in all_events:
            if event["kind"] == EventKind.Delete:
                for tag in event["tags"]:
                    if tag[0] == "e":
                        deleted_ids.add(tag[1])

        # Aggregate unspent proofs taking into account NIP-60 roll-overs and avoiding duplicates
        all_proofs: list[ProofDict] = []
        proof_to_event_id: dict[str, str] = {}

        # Index events newest → oldest so that when we encounter a replacement first we can ignore the ones it deletes later
        token_events_sorted = sorted(
            token_events, key=lambda e: e["created_at"], reverse=True
        )

        invalid_token_ids: set[str] = set(deleted_ids)
        proof_seen: set[str] = set()

        # Track undecryptable events for potential cleanup
        undecryptable_events = []

        for event in token_events_sorted:
            if event["id"] in invalid_token_ids:
                continue

            try:
                decrypted = nip44_decrypt(event["content"], self._privkey)
                token_data = json.loads(decrypted)
            except Exception:
                # Skip this event if it can't be decrypted - likely from old key or corrupted
                # print(f"Warning: Could not decrypt token event {event['id']}: {e}")
                undecryptable_events.append(event["id"])
                continue

            # Mark tokens referenced in the "del" field as superseded
            del_ids = token_data.get("del", [])
            if del_ids:
                for old_id in del_ids:
                    invalid_token_ids.add(old_id)
                    # Also remove from undecryptable list if it was there
                    if old_id in undecryptable_events:
                        undecryptable_events.remove(old_id)

            # Check again if this event was marked invalid by a newer event
            if event["id"] in invalid_token_ids:
                continue

            proofs = token_data.get("proofs", [])
            mint_url = token_data.get("mint")
            if not mint_url:
                raise WalletError("No mint URL found in token event")

            for proof in proofs:
                # Convert from NIP-60 format (base64) to internal format (hex)
                # NIP-60 stores secrets as base64, but internally we use hex
                secret = proof["secret"]
                try:
                    # Try to decode from base64 (NIP-60 format)
                    secret_bytes = base64.b64decode(secret)
                    hex_secret = secret_bytes.hex()
                except Exception:
                    # If it fails, assume it's already hex (backwards compatibility)
                    hex_secret = secret

                proof_id = f"{hex_secret}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)

                # Add mint URL to proof with hex secret
                proof_with_mint: ProofDict = ProofDict(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=hex_secret,  # Store as hex internally
                    C=proof["C"],
                    mint=mint_url,
                )
                all_proofs.append(proof_with_mint)
                proof_to_event_id[proof_id] = event["id"]

        # Include pending proofs from relay manager
        pending_token_data = self.relay_manager.get_pending_proofs()

        for token_data in pending_token_data:
            mint_url = token_data.get("mint")
            if not mint_url or not isinstance(mint_url, str):
                raise WalletError("No mint URL found in pending token event")

            proofs = token_data.get("proofs", [])
            if not isinstance(proofs, list):
                continue

            for proof in proofs:
                # Convert from NIP-60 format (base64) to internal format (hex)
                secret = proof["secret"]
                try:
                    # Try to decode from base64 (NIP-60 format)
                    secret_bytes = base64.b64decode(secret)
                    hex_secret = secret_bytes.hex()
                except Exception:
                    # If it fails, assume it's already hex
                    hex_secret = secret

                proof_id = f"{hex_secret}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)

                # Mark pending proofs with a special event ID
                pending_proof_with_mint: ProofDict = ProofDict(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=hex_secret,  # Store as hex internally
                    C=proof["C"],
                    mint=mint_url,
                )
                all_proofs.append(pending_proof_with_mint)
                proof_to_event_id[proof_id] = "__pending__"  # Special marker

        # Validate proofs using cache system if requested
        if check_proofs and all_proofs:
            # Don't validate pending proofs (they haven't been published yet)
            non_pending_proofs = [
                p
                for p in all_proofs
                if proof_to_event_id.get(f"{p['secret']}:{p['C']}", "") != "__pending__"
            ]
            pending_proofs = [
                p
                for p in all_proofs
                if proof_to_event_id.get(f"{p['secret']}:{p['C']}", "") == "__pending__"
            ]

            # Validate only non-pending proofs
            validated_proofs = await self._validate_proofs_with_cache(
                non_pending_proofs
            )

            # Add back pending proofs (assume they're valid)
            all_proofs = validated_proofs + pending_proofs

        # Fetch mint keysets
        mint_keysets: dict[str, list[dict[str, str]]] = {}
        for mint_url in self.mint_urls:
            mint = self._get_mint(mint_url)
            try:
                keys_resp = await mint.get_keys()
                # Convert Keyset type to dict[str, str] for wallet state
                keysets_as_dicts: list[dict[str, str]] = []
                for keyset in keys_resp.get("keysets", []):
                    # Convert each keyset to a simple dict
                    keyset_dict: dict[str, str] = {
                        "id": keyset["id"],
                        "unit": keyset["unit"],
                    }
                    # Add keys if present
                    if "keys" in keyset and isinstance(keyset["keys"], dict):
                        keyset_dict.update(keyset["keys"])
                    keysets_as_dicts.append(keyset_dict)
                mint_keysets[mint_url] = keysets_as_dicts
            except Exception:
                mint_keysets[mint_url] = []

        # Check local backups for missing proofs if requested
        if check_local_backups:
            backup_dir = Path.cwd() / "proof_backups"
            if backup_dir.exists() and any(backup_dir.glob("proofs_*.json")):
                # Check if we've recently checked backups (within last 60 seconds)
                last_check_file = backup_dir / ".last_check"
                should_check = True

                try:
                    if last_check_file.exists():
                        last_check_time = float(last_check_file.read_text().strip())
                        if time.time() - last_check_time < 60:
                            should_check = False
                except Exception:
                    pass

                if not should_check:
                    # Skip backup check if we just did it
                    return await self._build_enhanced_wallet_state(
                        all_proofs, mint_keysets, proof_to_event_id
                    )
                # Check if we have any backup files with missing proofs
                existing_proof_ids = set(f"{p['secret']}:{p['C']}" for p in all_proofs)

                # Quick scan to see if there might be missing proofs
                has_missing = False
                for backup_file in backup_dir.glob("proofs_*.json"):
                    try:
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)
                        backup_proofs = backup_data.get("proofs", [])

                        for proof in backup_proofs:
                            proof_id = f"{proof['secret']}:{proof['C']}"
                            if proof_id not in existing_proof_ids:
                                has_missing = True
                                break
                    except Exception:
                        continue

                    if has_missing:
                        break

                # If we found missing proofs, run the recovery scan
                if has_missing:
                    print("\n⚠️  Detected local proof backups not synced to Nostr")
                    recovery_stats = await self.scan_and_recover_local_proofs(
                        auto_publish=True
                    )

                    # Update last check timestamp
                    try:
                        last_check_file = backup_dir / ".last_check"
                        last_check_file.write_text(str(time.time()))
                    except Exception:
                        pass

                    # If we recovered proofs, re-fetch the state to include them
                    if recovery_stats["recovered"] > 0:
                        print("🔄 Re-fetching wallet state after recovery...")
                        # Recursive call without check_local_backups to avoid infinite loop
                        return await self.fetch_wallet_state(
                            check_proofs=check_proofs, check_local_backups=False
                        )
                    elif (
                        recovery_stats["missing_from_nostr"] > 0
                        and recovery_stats["recovered"] == 0
                    ):
                        # All missing proofs were invalid/spent - clean up backup files
                        print(
                            "🧹 All proofs in backups are spent/invalid, cleaning up..."
                        )
                        await self._cleanup_spent_proof_backups()

        return await self._build_enhanced_wallet_state(
            all_proofs, mint_keysets, proof_to_event_id
        )

    async def get_balance(self, *, check_proofs: bool = True) -> int:
        """Get current wallet balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning balance

        Returns:
            Current balance in the wallet's currency unit

        Example:
            balance = await wallet.get_balance()
            print(f"Balance: {balance} sats")
        """
        state = await self.fetch_wallet_state(
            check_proofs=check_proofs, check_local_backups=True
        )
        return state.balance

    async def get_balance_by_mint(self, mint_url: str) -> int:
        """Get balance for a specific mint."""
        state = await self.fetch_wallet_state(check_proofs=True)
        return sum(p["amount"] for p in state.proofs if p["mint"] == mint_url)

    # ─────────────────────────────── Cleanup ──────────────────────────────────

    async def aclose(self) -> None:
        """Close underlying HTTP clients."""
        await self.mint_client.aclose()

        # Close relay manager connections
        await self.relay_manager.disconnect_all()

        # Close mint clients
        for mint in self.mints.values():
            await mint.aclose()

    # ───────────────────────── Async context manager ──────────────────────────

    async def __aenter__(self) -> "Wallet":
        """Enter async context and connect to relays without auto-creating wallet events."""
        # Discover relays if none are set
        if not self.relays:
            try:
                from .relay import get_relays_for_wallet

                self.relays = await get_relays_for_wallet(
                    self._privkey, prompt_if_needed=True
                )
                # Update relay manager with discovered relays
                self.relay_manager.relay_urls = self.relays
            except Exception:
                # If relay discovery fails, continue with empty relays
                # This allows offline operations
                pass

        # Just connect to relays, don't auto-create wallet events
        # Users must explicitly call initialize_wallet() or create_wallet_event()
        try:
            await self.relay_manager.get_relay_connections()
        except Exception:
            # If we can't connect to relays, that's okay -
            # user might just want to do offline operations
            pass
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401  (simple return)
        await self.aclose()

    # ───────────────────────── Conversion Methods ──────────────────────────────

    # TODO: check why this method is needed
    def _proofdict_to_mint_proof(self, proof_dict: ProofDict) -> Proof:
        """Convert ProofDict to Proof format for mint.

        Since we store hex secrets internally, this is now a simple conversion.
        """
        return Proof(
            id=proof_dict["id"],
            amount=proof_dict["amount"],
            secret=proof_dict["secret"],  # Already hex
            C=proof_dict["C"],
        )

    # ───────────────────────── Fee Calculation ──────────────────────────────
    def calculate_input_fees(self, proofs: list[ProofDict], keyset_info: dict) -> int:
        """Calculate input fees based on number of proofs and keyset fee rate.

        Args:
            proofs: List of proofs being spent
            keyset_info: Keyset information containing input_fee_ppk

        Returns:
            Total input fees in base currency units (e.g., satoshis)

        Example:
            With input_fee_ppk=1000 (1 sat per proof) and 3 proofs:
            fee = (3 * 1000 + 999) // 1000 = 3 satoshis
        """
        input_fee_ppk = keyset_info.get("input_fee_ppk", 0)

        # Ensure input_fee_ppk is an integer (could be string from API)
        try:
            input_fee_ppk = int(input_fee_ppk)
        except (ValueError, TypeError):
            input_fee_ppk = 0

        if input_fee_ppk == 0:
            return 0

        # Sum up fees for all proofs and use ceiling division
        sum_fees = len(proofs) * input_fee_ppk
        return (sum_fees + 999) // 1000

    async def calculate_total_input_fees(
        self, mint: Mint, proofs: list[ProofDict]
    ) -> int:
        """Calculate total input fees for proofs across different keysets.

        Args:
            mint: Mint instance to query keyset information
            proofs: List of proofs being spent

        Returns:
            Total input fees for all proofs
        """
        try:
            # Get keyset information from mint
            keysets_resp = await mint.get_keysets()
            keyset_fees = {}

            # Build mapping of keyset_id -> input_fee_ppk
            for keyset in keysets_resp["keysets"]:
                keyset_fees[keyset["id"]] = keyset.get("input_fee_ppk", 0)

            # Sum fees for each proof based on its keyset
            sum_fees = 0
            for proof in proofs:
                keyset_id = proof["id"]
                fee_rate = keyset_fees.get(keyset_id, 0)
                # Ensure fee_rate is an integer (could be string from API)
                try:
                    fee_rate = int(fee_rate)
                except (ValueError, TypeError):
                    fee_rate = 0
                sum_fees += fee_rate

            # Use ceiling division to round up fees (matches mint behavior)
            return (sum_fees + 999) // 1000

        except Exception:
            # Fallback to zero fees if keyset info unavailable
            # This ensures wallet doesn't break when connecting to older mints
            return 0

    def estimate_transaction_fees(
        self,
        input_proofs: list[ProofDict],
        keyset_info: dict,
        lightning_fee_reserve: int = 0,
    ) -> tuple[int, int]:
        """Estimate total transaction fees including input fees and lightning fees.

        Args:
            input_proofs: Proofs being spent as inputs
            keyset_info: Keyset information for input fee calculation
            lightning_fee_reserve: Lightning network fee reserve from melt quote

        Returns:
            Tuple of (input_fees, total_fees)
        """
        input_fees = self.calculate_input_fees(input_proofs, keyset_info)
        total_fees = input_fees + lightning_fee_reserve

        return input_fees, total_fees

    # ───────────────────────── Currency Validation ────────────────────────────

    def _validate_currency_unit(self, unit: CurrencyUnit) -> None:
        """Validate currency unit is supported per NUT-01.

        Args:
            unit: Currency unit to validate

        Raises:
            ValueError: If currency unit is not supported
        """
        # Type checking ensures unit is valid CurrencyUnit at compile time
        # This method can be extended for runtime validation if needed
        if unit not in [
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
        ]:
            raise ValueError(f"Unsupported currency unit: {unit}")

    # ───────────────────────── Public Helper Methods ──────────────────────────

    def _get_pubkey(self) -> str:
        """Get the Nostr public key for this wallet."""
        return get_pubkey(self._privkey)

    async def _build_enhanced_wallet_state(
        self,
        all_proofs: list[ProofDict],
        mint_keysets: dict[str, list[dict[str, str]]],
        proof_to_event_id: dict[str, str] | None,
    ) -> EnhancedWalletState:
        """Build enhanced wallet state from proofs and keysets."""
        # Update keyset manager with latest data from mints
        for mint_url in self.mint_urls:
            if not self.keyset_manager.is_cached(f"{mint_url}:check"):
                await self._update_keysets_from_mint(mint_url)
                # Mark as checked (simple cache mechanism)
                self.keyset_manager._cache_timestamps[f"{mint_url}:check"] = time.time()

        # Calculate balance by keyset and currency
        balance_by_keyset: dict[str, int] = {}
        balance_by_currency: dict[CurrencyUnit, int] = {}
        keysets: dict[str, KeysetInfo] = {}

        # Get all keysets from the manager
        for keyset_id, keyset_info in self.keyset_manager.keysets.items():
            keysets[keyset_id] = keyset_info

        # Calculate balances
        for proof in all_proofs:
            keyset_id = proof["id"]
            amount = proof["amount"]

            # Update keyset balance
            if keyset_id not in balance_by_keyset:
                balance_by_keyset[keyset_id] = 0
            balance_by_keyset[keyset_id] += amount

            # Get keyset info to determine currency
            proof_keyset_info: KeysetInfo | None = self.keyset_manager.get_keyset(
                keyset_id
            )
            if proof_keyset_info:
                currency = proof_keyset_info.unit
            else:
                # Fallback to wallet currency if keyset not found
                currency = self.currency

            # Update currency balance
            if currency not in balance_by_currency:
                balance_by_currency[currency] = 0
            balance_by_currency[currency] += amount

        return EnhancedWalletState(
            balance_by_keyset=balance_by_keyset,
            balance_by_currency=balance_by_currency,
            proofs=all_proofs,
            keysets=keysets,
            proof_to_event_id=proof_to_event_id,
        )

    async def check_wallet_event_exists(self) -> tuple[bool, NostrEvent | None]:
        """Check if a wallet event already exists for this wallet.

        Returns:
            Tuple of (exists, wallet_event_dict)
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.check_wallet_event_exists()

    async def initialize_wallet(self, *, force: bool = False) -> bool:
        """Initialize wallet by checking for existing events or creating new ones.

        Args:
            force: If True, create wallet event even if one already exists

        Returns:
            True if wallet was initialized (new event created), False if already existed
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.initialize_wallet(self.wallet_privkey, force=force)

    async def delete_all_wallet_events(self) -> int:
        """Delete all wallet events for this wallet.

        Returns:
            Number of wallet events deleted
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.delete_all_wallet_events()

    async def fetch_spending_history(self) -> list[dict]:
        """Fetch and decrypt spending history events.

        Returns:
            List of spending history entries with metadata
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.fetch_spending_history()

    async def clear_spending_history(self) -> int:
        """Delete all spending history events for this wallet.

        Returns:
            Number of history events deleted
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.clear_spending_history()

    async def count_token_events(self) -> int:
        """Count the number of token events for this wallet.

        Returns:
            Number of token events found
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.count_token_events()

    async def clear_all_token_events(self) -> int:
        """Delete all token events for this wallet.

        WARNING: This will delete your actual token storage!

        Returns:
            Number of token events deleted
        """
        event_manager = await self._ensure_event_manager()
        return await event_manager.clear_all_token_events()

    def _nip44_decrypt(self, content: str) -> str:
        """Decrypt NIP-44 encrypted content.

        Args:
            content: Encrypted content to decrypt

        Returns:
            Decrypted content
        """
        return nip44_decrypt(content, self._privkey)

    async def cleanup_wallet_state(self, *, dry_run: bool = False) -> dict[str, int]:
        """Clean up wallet state by consolidating old/undecryptable events.

        This method identifies old or corrupted token events and consolidates
        all valid proofs into fresh events, marking old events as superseded.

        Args:
            dry_run: If True, only report what would be cleaned up without making changes

        Returns:
            Dictionary with cleanup statistics
        """
        print("🧹 Starting wallet state cleanup...")

        # Get current state
        state = await self.fetch_wallet_state(
            check_proofs=True, check_local_backups=False
        )

        # Fetch all events to analyze
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )
        token_events = [e for e in all_events if e["kind"] == EventKind.Token]

        # Categorize events
        valid_events = []
        undecryptable_events = []
        empty_events = []

        for event in token_events:
            try:
                decrypted = nip44_decrypt(event["content"], self._privkey)
                token_data = json.loads(decrypted)
                proofs = token_data.get("proofs", [])

                if proofs:
                    valid_events.append(event["id"])
                else:
                    empty_events.append(event["id"])

            except Exception:
                undecryptable_events.append(event["id"])

        stats = {
            "total_events": len(token_events),
            "valid_events": len(valid_events),
            "undecryptable_events": len(undecryptable_events),
            "empty_events": len(empty_events),
            "valid_proofs": len(state.proofs),
            "balance": state.balance,
            "events_consolidated": 0,
            "events_marked_superseded": 0,
        }

        print(f"📊 Analysis: {stats['total_events']} total events")
        print(f"   ✅ Valid: {stats['valid_events']}")
        print(f"   ❌ Undecryptable: {stats['undecryptable_events']}")
        print(f"   📭 Empty: {stats['empty_events']}")
        print(f"   💰 Valid proofs: {stats['valid_proofs']} ({stats['balance']} sats)")

        if dry_run:
            print("🔍 Dry run - no changes will be made")
            return stats

        # Only consolidate if we have significant cleanup opportunity
        cleanup_threshold = max(
            5, len(token_events) // 3
        )  # At least 5 events or 1/3 of total
        events_to_cleanup = undecryptable_events + empty_events

        if len(events_to_cleanup) < cleanup_threshold:
            print(f"🎯 No significant cleanup needed (threshold: {cleanup_threshold})")
            return stats

        if not state.proofs:
            print("⚠️  No valid proofs found - skipping consolidation")
            return stats

        print(f"🔄 Consolidating {len(state.proofs)} proofs into fresh events...")

        # Create fresh consolidated events
        new_event_ids = []
        for mint_url, mint_proofs in state.proofs_by_mints.items():
            try:
                event_manager = await self._ensure_event_manager()
                new_id = await event_manager.publish_token_event(
                    mint_proofs,
                    deleted_token_ids=events_to_cleanup,  # Mark all old events as superseded
                )
                new_event_ids.append(new_id)
                stats["events_consolidated"] += 1
                print(
                    f"   ✅ Created consolidated event for {mint_url}: {len(mint_proofs)} proofs"
                )
            except Exception as e:
                print(f"   ❌ Failed to consolidate {mint_url}: {e}")

        if new_event_ids:
            stats["events_marked_superseded"] = len(events_to_cleanup)

            # Try to delete old events (best effort)
            deleted_count = 0
            for event_id in events_to_cleanup:
                try:
                    event_manager = await self._ensure_event_manager()
                    await event_manager.delete_token_event(event_id)
                    deleted_count += 1
                except Exception:
                    # Deletion not supported - that's okay, 'del' field handles it
                    pass

            if deleted_count > 0:
                print(f"   🗑️  Successfully deleted {deleted_count} old events")
            else:
                print("   📝 Old events marked as superseded via 'del' field")

            # Create consolidation history
            try:
                event_manager = await self._ensure_event_manager()
                await event_manager.publish_spending_history(
                    direction="in",  # Consolidation is like receiving all proofs again
                    amount=0,  # No net change in balance
                    created_token_ids=new_event_ids,
                    destroyed_token_ids=events_to_cleanup,
                )
                print("   📋 Created consolidation history")
            except Exception as e:
                print(f"   ⚠️  Could not create history: {e}")

        print(
            f"🎉 Cleanup complete! Consolidated {stats['events_consolidated']} events"
        )
        return stats

    def _primary_mint_url(self) -> str:
        """Get the primary mint URL (first one when sorted).

        Returns:
            Primary mint URL

        Raises:
            WalletError: If no mint URLs configured
        """
        if not self.mint_urls:
            raise WalletError("No mint URLs configured")
        return sorted(self.mint_urls)[0]  # Use sorted order for consistency

    def _sort_proofs_by_mint(
        self, proofs: list[ProofDict]
    ) -> dict[str, list[ProofDict]]:
        return {
            mint_url: [proof for proof in proofs if proof["mint"] == mint_url]
            for mint_url in set(proof["mint"] for proof in proofs)
        }

    async def _cleanup_spent_proof_backups(self) -> int:
        """Clean up backup files that only contain spent/invalid proofs.

        Returns:
            Number of backup files cleaned up
        """
        backup_dir = Path.cwd() / "proof_backups"
        if not backup_dir.exists():
            return 0

        # Get current valid proofs and known spent proofs
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )
        valid_proof_ids = set(f"{p['secret']}:{p['C']}" for p in state.proofs)

        cleaned_count = 0
        backup_files = list(backup_dir.glob("proofs_*.json"))

        for backup_file in backup_files:
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)

                backup_proofs = backup_data.get("proofs", [])
                if not backup_proofs:
                    # Empty backup file, remove it
                    backup_file.unlink()
                    cleaned_count += 1
                    print(f"   🗑️  Deleted empty backup: {backup_file.name}")
                    continue

                # Check if all proofs are spent/invalid
                all_invalid = True
                for proof in backup_proofs:
                    proof_id = f"{proof['secret']}:{proof['C']}"
                    if proof_id in valid_proof_ids:
                        # At least one valid proof, keep the backup
                        all_invalid = False
                        break

                if all_invalid:
                    # All proofs are spent/invalid, check with mint to be sure
                    valid_proofs = await self._validate_proofs_with_cache(backup_proofs)
                    if not valid_proofs:
                        # Confirmed all proofs are spent/invalid
                        backup_file.unlink()
                        cleaned_count += 1
                        print(
                            f"   🗑️  Deleted backup with only spent proofs: {backup_file.name}"
                        )

            except Exception as e:
                print(f"   ⚠️  Error processing backup {backup_file.name}: {e}")

        return cleaned_count

    async def scan_and_recover_local_proofs(
        self, *, auto_publish: bool = False
    ) -> dict[str, int]:
        """Scan local proof backups and recover any missing from Nostr.

        This method checks the local proof_backups directory for backup files
        and compares them against what's stored on Nostr. Any missing proofs
        can be automatically published to Nostr.

        Args:
            auto_publish: If True, automatically publish missing proofs to Nostr

        Returns:
            Dictionary with recovery statistics:
            - total_backup_files: Number of backup files found
            - total_proofs_in_backups: Total proofs across all backups
            - missing_from_nostr: Number of proofs not found on Nostr
            - recovered: Number of proofs successfully recovered
            - failed: Number of proofs that failed to recover
        """
        stats = {
            "total_backup_files": 0,
            "total_proofs_in_backups": 0,
            "missing_from_nostr": 0,
            "recovered": 0,
            "failed": 0,
        }

        backup_dir = Path.cwd() / "proof_backups"
        if not backup_dir.exists():
            return stats

        print("🔍 Scanning local proof backups...")

        try:
            # Get current state from Nostr WITHOUT checking local backups to avoid recursion
            state = await self.fetch_wallet_state(
                check_proofs=False, check_local_backups=False
            )
            existing_proofs = set()
            for proof in state.proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                existing_proofs.add(proof_id)
        except Exception as e:
            print(f"❌ Error fetching wallet state: {e}")
            return stats

        # Scan all backup files
        backup_files = list(backup_dir.glob("proofs_*.json"))
        all_backup_proofs: dict[str, ProofDict] = {}  # proof_id -> proof
        backup_proofs_by_mint: dict[str, list[ProofDict]] = {}

        for backup_file in backup_files:
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)

                proofs = backup_data.get("proofs", [])
                for proof in proofs:
                    proof_id = f"{proof['secret']}:{proof['C']}"
                    if proof_id not in all_backup_proofs:
                        all_backup_proofs[proof_id] = proof

                        # Group by mint
                        mint_url = proof.get("mint", "")
                        if mint_url:
                            if mint_url not in backup_proofs_by_mint:
                                backup_proofs_by_mint[mint_url] = []
                            backup_proofs_by_mint[mint_url].append(proof)

            except Exception as e:
                print(f"⚠️  Error reading backup file {backup_file}: {e}")

        # Find missing proofs
        missing_proof_ids = set(all_backup_proofs.keys()) - existing_proofs
        missing_proofs: list[ProofDict] = []

        for proof_id in missing_proof_ids:
            missing_proofs.append(all_backup_proofs[proof_id])

        stats = {
            "total_backup_files": len(backup_files),
            "total_proofs_in_backups": len(all_backup_proofs),
            "missing_from_nostr": len(missing_proofs),
            "recovered": 0,
            "failed": 0,
        }

        print(f"📊 Found {len(backup_files)} backup files")
        print(f"   📦 Total proofs in backups: {len(all_backup_proofs)}")
        print(f"   ✅ Already on Nostr: {len(all_backup_proofs) - len(missing_proofs)}")
        print(f"   ❌ Missing from Nostr: {len(missing_proofs)}")

        if not missing_proofs:
            print("✨ All proofs are already backed up on Nostr!")
            return stats

        if not auto_publish:
            print("\n💡 To recover missing proofs, run with auto_publish=True")
            return stats

        # Validate missing proofs before publishing
        print("\n🔐 Validating missing proofs with mints...")
        valid_missing_proofs = await self._validate_proofs_with_cache(missing_proofs)
        invalid_count = len(missing_proofs) - len(valid_missing_proofs)

        if invalid_count > 0:
            print(f"   ⚠️  {invalid_count} proofs are already spent or invalid")

        if not valid_missing_proofs:
            print("❌ No valid proofs to recover")
            # Clean up backup files that only contain spent proofs
            if auto_publish and len(missing_proofs) > 0:
                print("🧹 Cleaning up backup files with only spent proofs...")
                cleaned = await self._cleanup_spent_proof_backups()
                if cleaned > 0:
                    print(f"   ✅ Cleaned up {cleaned} backup files")
            return stats

        # Group valid missing proofs by mint
        missing_by_mint: dict[str, list[ProofDict]] = {}
        for proof in valid_missing_proofs:
            mint_url = proof.get("mint", "")
            if mint_url:
                if mint_url not in missing_by_mint:
                    missing_by_mint[mint_url] = []
                missing_by_mint[mint_url].append(proof)

        # Publish missing proofs to Nostr
        print(f"\n📤 Publishing {len(valid_missing_proofs)} missing proofs to Nostr...")

        for mint_url, mint_proofs in missing_by_mint.items():
            try:
                event_manager = await self._ensure_event_manager()
                event_id = await event_manager.publish_token_event(mint_proofs)
                stats["recovered"] += len(mint_proofs)
                print(f"   ✅ Published {len(mint_proofs)} proofs for {mint_url}")
                print(f"      Event ID: {event_id}")

                # Also create spending history for recovery
                total_amount = sum(p["amount"] for p in mint_proofs)
                await event_manager.publish_spending_history(
                    direction="in",
                    amount=total_amount,
                    created_token_ids=[event_id],
                )

            except Exception as e:
                print(f"   ❌ Failed to publish proofs for {mint_url}: {e}")
                stats["failed"] += len(mint_proofs)

        # Clean up successfully recovered backup files (with verification)
        if stats["recovered"] > 0 and stats["failed"] == 0:
            print("\n🔍 Verifying recovered proofs before cleaning up backups...")

            # Re-fetch state to ensure all recovered proofs are really on relays
            await asyncio.sleep(2.0)  # Give relays time to propagate

            try:
                verification_state = await self.fetch_wallet_state(
                    check_proofs=False, check_local_backups=False
                )
                stored_proof_ids = set(
                    f"{p['secret']}:{p['C']}" for p in verification_state.proofs
                )

                # Check each backup file individually
                for backup_file in backup_files:
                    try:
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)

                        backup_proofs = backup_data.get("proofs", [])
                        all_verified = True

                        for proof in backup_proofs:
                            proof_id = f"{proof['secret']}:{proof['C']}"
                            if proof_id not in stored_proof_ids:
                                # Check if it's spent (which is okay)
                                if proof_id not in self._known_spent_proofs:
                                    all_verified = False
                                    break

                        if all_verified:
                            backup_file.unlink()
                            print(f"   ✅ Verified and deleted: {backup_file.name}")
                        else:
                            print(
                                f"   ⚠️  Keeping backup (not all proofs verified): {backup_file.name}"
                            )

                    except Exception as e:
                        print(f"   ⚠️  Error processing {backup_file.name}: {e}")

            except Exception as e:
                print(f"   ❌ Verification failed, keeping all backups: {e}")

        print(f"\n✨ Recovery complete! Recovered {stats['recovered']} proofs")
        return stats
