"""Sixty Nuts CLI - NIP-60 Cashu Wallet Implementation."""

import asyncio
import os
import shutil
import time
import json  # Add missing import
from typing import Annotated, Optional, cast

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.panel import Panel

try:
    import qrcode  # type: ignore
    import qrcode.constants  # type: ignore

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from .types import WalletError
from .wallet import (
    Wallet,
    CurrencyUnit,
    Proof,
)
from .mint import (
    get_mints_from_env,
    validate_mint_url,
    POPULAR_MINTS,
    set_mints_in_env,
    clear_mints_from_env,
)
from .crypto import nip44_decrypt  # Add missing import
from .temp import redeem_to_lnurl
from .relay import prompt_user_for_relays, RELAYS_ENV_VAR, EventKind

# Update the environment variable name to match what's in mint.py
MINTS_ENV_VAR = "CASHU_MINTS"

app = typer.Typer(
    name="nuts",
    help="Sixty Nuts - NIP-60 Cashu Wallet CLI",
    rich_markup_mode="markdown",
)
console = Console()

# Environment variable for NSEC is "NSEC"


def get_nsec_from_env() -> str | None:
    """Get NSEC from environment variable or .env file.

    Expected format: hex private key or nsec1... format
    Example: NSEC="nsec1..." or NSEC="hex_private_key"

    Priority order:
    1. Environment variable NSEC
    2. .env file in current working directory

    Returns:
        NSEC from environment or .env file, None if not set
    """
    # First check environment variable
    env_nsec = os.getenv("NSEC")
    if env_nsec:
        return env_nsec.strip()

    # Then check .env file in current working directory
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("NSEC="):
                    # Extract value after the equals sign
                    value = line.split("=", 1)[1]
                    # Remove quotes if present
                    value = value.strip("\"'")
                    if value:
                        return value
    except Exception:
        # If reading .env file fails, continue
        pass

    return None


def set_nsec_in_env(nsec: str) -> None:
    """Set NSEC in .env file for persistent caching.

    Args:
        nsec: NSEC to cache
    """
    if not nsec:
        return

    from pathlib import Path

    env_file = Path.cwd() / ".env"
    env_line = f'NSEC="{nsec}"\n'

    try:
        if env_file.exists():
            # Check if NSEC already exists in the file
            content = env_file.read_text()
            lines = content.splitlines()

            # Look for existing NSEC line
            nsec_line_found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith("NSEC="):
                    # Replace existing NSEC line
                    new_lines.append(env_line.rstrip())
                    nsec_line_found = True
                else:
                    new_lines.append(line)

            if not nsec_line_found:
                # Add new NSEC line at the end
                new_lines.append(env_line.rstrip())

            # Write back to file
            env_file.write_text("\n".join(new_lines) + "\n")
        else:
            # Create new .env file
            env_file.write_text(env_line)

        # Set secure permissions (read only for user)
        env_file.chmod(0o600)

    except Exception as e:
        # If writing to .env file fails, fall back to environment variable
        print(f"Warning: Could not write to .env file: {e}")
        print("Falling back to session environment variable")
        os.environ["NSEC"] = nsec


def clear_nsec_from_env() -> bool:
    """Clear NSEC from .env file and environment variable.

    Returns:
        True if NSEC was cleared, False if none was set
    """
    cleared = False

    # Clear from environment variable
    if "NSEC" in os.environ:
        del os.environ["NSEC"]
        cleared = True

    # Clear from .env file
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            lines = content.splitlines()

            # Remove NSEC line
            new_lines = []
            for line in lines:
                if not line.strip().startswith("NSEC="):
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


def get_nsec() -> str:
    """Get NSEC from environment, .env file, or prompt user."""
    # Try .env approach first
    nsec = get_nsec_from_env()
    if nsec:
        return nsec

    # Prompt user
    console.print("\n[yellow]NSEC (Nostr private key) not found.[/yellow]")
    console.print("You need a Nostr private key to use this wallet.")
    console.print("Format: nsec1... or hex private key")

    nsec = Prompt.ask("Enter your NSEC")

    if not nsec:
        console.print("[red]NSEC is required![/red]")
        raise typer.Exit(1)

    # Ask if user wants to store it
    store_choice = Prompt.ask(
        "Store NSEC for future use?", choices=["env", "file", "no"], default="env"
    )

    if store_choice == "env":
        set_nsec_in_env(nsec)
        console.print("[green]✅ NSEC stored in .env file[/green]")
    elif store_choice == "file":
        console.print("\n[green]Add this to your shell profile:[/green]")
        console.print(f"export NSEC={nsec}")
        console.print("\nOr run:")
        console.print(f"echo 'export NSEC={nsec}' >> ~/.bashrc")

    return nsec


async def prompt_user_for_mints() -> list[str]:
    """Prompt user to select mint URLs interactively.

    Returns:
        List of selected mint URLs
    """
    console.print("\n[cyan]🏦 Mint Configuration[/cyan]")
    console.print("No mint URLs are configured. Please select mints to use:")

    # Check environment variable and .env file first
    env_mints = get_mints_from_env()
    if env_mints:
        console.print(
            f"\n[green]✅ Found {len(env_mints)} cached mints (env var or .env file):[/green]"
        )
        for i, mint in enumerate(env_mints, 1):
            status = "✅ Valid" if validate_mint_url(mint) else "❌ Invalid"
            console.print(f"  {i}. {mint} - {status}")

        console.print(
            "\n[yellow]Options:[/yellow] [dim](use, clear-cache, select-new)[/dim]"
        )
        choice = Prompt.ask(
            "Use cached mints, clear cache, or select new",
            choices=["use", "clear-cache", "select-new"],
            default="use",
        )

        if choice == "use":
            return env_mints
        elif choice == "clear-cache":
            cleared = clear_mints_from_env()
            if cleared:
                console.print("[green]🗑️ Cleared mint cache[/green]")
            else:
                console.print("[yellow]ℹ️ No mint cache to clear[/yellow]")
            # Continue to selection below
        # If "select-new", continue to manual selection

    console.print("\n[yellow]📋 Popular Cashu Mints:[/yellow]")

    # Create table for popular mints
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Mint URL", style="green")
    table.add_column("Status", style="blue")

    for i, mint_url in enumerate(POPULAR_MINTS, 1):
        status = "🟢 Available"
        table.add_row(str(i), mint_url, status)

    console.print(table)

    selected_mints: list[str] = []

    while True:
        console.print(f"\n[cyan]Current selection: {len(selected_mints)} mints[/cyan]")
        if selected_mints:
            for i, mint in enumerate(selected_mints, 1):
                console.print(f"  {i}. {mint}")

        console.print(
            "\n[yellow]Options: [dim](number, url, 'done', 'clear', 'env')[/dim][/yellow]"
        )

        choice = Prompt.ask("Choice").strip()

        if choice.lower() == "done":
            if selected_mints:
                break
            else:
                console.print("[red]⚠️  Please select at least one mint[/red]")
                continue

        elif choice.lower() == "clear":
            selected_mints = []
            console.print("[yellow]Selection cleared[/yellow]")
            continue

        elif choice.lower() == "env":
            if selected_mints:
                mint_str = ",".join(selected_mints)
                console.print("\n[green]Add this to your shell profile:[/green]")
                console.print(f"export {MINTS_ENV_VAR}={mint_str}")
                console.print("\nOr run:")
                console.print(f"echo 'export {MINTS_ENV_VAR}={mint_str}' >> ~/.bashrc")
            else:
                console.print("[yellow]No mints selected to save[/yellow]")
            continue

        elif choice.isdigit():
            choice_num = int(choice)
            if 1 <= choice_num <= len(POPULAR_MINTS):
                mint_url = POPULAR_MINTS[choice_num - 1]
                if mint_url not in selected_mints:
                    selected_mints.append(mint_url)
                    console.print(f"[green]✅ Added: {mint_url}[/green]")
                else:
                    console.print(f"[yellow]Already selected: {mint_url}[/yellow]")
            else:
                console.print(
                    f"[red]Invalid choice. Please enter 1-{len(POPULAR_MINTS)}[/red]"
                )

        elif choice.startswith("http"):
            # Custom mint URL
            if validate_mint_url(choice):
                if choice not in selected_mints:
                    selected_mints.append(choice)
                    console.print(f"[green]✅ Added custom mint: {choice}[/green]")
                else:
                    console.print(f"[yellow]Already selected: {choice}[/yellow]")
            else:
                console.print(f"[red]Invalid mint URL format: {choice}[/red]")
                console.print(
                    "[dim]URLs should start with http:// or https:// and not end with /[/dim]"
                )

        else:
            console.print(f"[red]Invalid choice: {choice}[/red]")

    console.print(f"\n[green]✅ Selected {len(selected_mints)} mints:[/green]")
    for i, mint in enumerate(selected_mints, 1):
        console.print(f"  {i}. {mint}")

    # Automatically save to .env file for future use
    try:
        set_mints_in_env(selected_mints)
        console.print("[blue]💾 Cached mints to .env file for future use[/blue]")
    except Exception as e:
        console.print(f"[yellow]⚠️ Could not cache mints: {e}[/yellow]")

    return selected_mints


async def create_wallet_with_mint_selection(
    nsec: str,
    *,
    mint_urls: list[str] | None = None,
    prompt_for_relays: bool = True,
) -> Wallet:
    """Create a wallet, handling mint selection if needed.

    Args:
        nsec: Nostr private key
        mint_urls: Optional mint URLs (if None, will try discovery/prompting)
        prompt_for_relays: Whether to prompt for relays if needed

    Returns:
        Initialized wallet instance

    Raises:
        typer.Exit: If user cancels mint selection
    """
    try:
        # Try to create wallet normally first
        wallet = await Wallet.create(
            nsec=nsec,
            mint_urls=mint_urls,
            prompt_for_relays=prompt_for_relays,
        )
        return wallet

    except WalletError as e:
        if "No mint URLs configured" in str(e):
            # Mint selection needed
            try:
                selected_mints = await prompt_user_for_mints()
                console.print("[blue]Creating wallet and saving to Nostr...[/blue]")

                # Create wallet with selected mints
                wallet = await Wallet.create(
                    nsec=nsec,
                    mint_urls=selected_mints,
                    prompt_for_relays=prompt_for_relays,
                )

                # Automatically save to Nostr wallet event
                try:
                    async with wallet:
                        # Generate a new wallet privkey if not already set
                        if wallet.wallet_privkey is None:
                            wallet.wallet_privkey = wallet._generate_wallet_privkey()

                        await wallet.event_manager.initialize_wallet(
                            wallet.wallet_privkey, force=True
                        )
                    console.print(
                        f"[green]✅ Wallet configured with {len(selected_mints)} mints and saved to Nostr![/green]"
                    )
                except Exception as e:
                    console.print(
                        f"[yellow]⚠️  Wallet created but failed to save to Nostr: {e}[/yellow]"
                    )

                return wallet

            except KeyboardInterrupt:
                console.print("\n[yellow]Mint selection cancelled[/yellow]")
                raise typer.Exit(1)
        else:
            # Re-raise other wallet errors
            raise


def get_terminal_size() -> tuple[int, int]:
    """Get terminal size (width, height)."""
    try:
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except Exception:
        return 80, 24  # Default fallback


def display_qr_code(data: str, title: str = "QR Code") -> None:
    """Display QR code in terminal if qrcode library is available.

    Automatically animates with multiple QR code variations if too big for terminal.

    Args:
        data: Data to encode in QR code
        title: Title for the QR code display
    """
    if not HAS_QRCODE:
        console.print("[dim]💡 Install 'qrcode' package for QR code display[/dim]")
        return

    try:
        # Start with small QR code for better terminal fit
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(data)
        qr.make(fit=True)

        matrix = qr.get_matrix()
        qr_width = len(matrix[0])
        qr_height = len(matrix)

        # Get terminal size
        term_width, term_height = get_terminal_size()
        max_display_width = term_width - 6  # Account for borders and padding
        max_display_height = (
            term_height - 8
        )  # Account for title, borders, and other content

        # Check if QR code fits in terminal
        display_height = (qr_height + 1) // 2  # We use half-blocks, so height is halved

        if qr_width <= max_display_width and display_height <= max_display_height:
            # QR code fits, display normally
            qr_text = _matrix_to_text(matrix)
            console.print(
                Panel(
                    qr_text.rstrip(),
                    title=f"[cyan]📱 {title}[/cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )
            console.print("[dim]📱 Scan with your wallet app[/dim]\n")

        else:
            # QR code is too big - automatically animate with multiple variations
            console.print(
                f"[cyan]📱 {title} (Animated - QR too large for terminal)[/cyan]"
            )
            console.print(
                "[dim]📱 Cycling through QR codes - Press Ctrl+C to stop...[/dim]"
            )
            _animate_multiple_qr_codes(
                data, title, max_display_width, max_display_height
            )

    except Exception as e:
        console.print(f"[dim]⚠️ Could not generate QR code: {e}[/dim]")


def _generate_qr_variations(
    data: str, max_width: int, max_height: int
) -> list[tuple[str, list[list[bool]]]]:
    """Generate multiple QR code variations of the same data.

    Returns list of (description, matrix) tuples.
    """
    variations = []

    # Define different QR code configurations
    configs = [
        ("Error Correction L", qrcode.constants.ERROR_CORRECT_L, 1, 1),
        ("Error Correction M", qrcode.constants.ERROR_CORRECT_M, 1, 1),
        ("Error Correction Q", qrcode.constants.ERROR_CORRECT_Q, 1, 1),
        ("Error Correction H", qrcode.constants.ERROR_CORRECT_H, 1, 1),
        ("Version 2 + L", qrcode.constants.ERROR_CORRECT_L, 2, 1),
        ("Version 2 + M", qrcode.constants.ERROR_CORRECT_M, 2, 1),
        ("Version 3 + L", qrcode.constants.ERROR_CORRECT_L, 3, 1),
        ("Compact", qrcode.constants.ERROR_CORRECT_L, 1, 0),
    ]

    for desc, error_correction, version, border in configs:
        try:
            qr = qrcode.QRCode(
                version=version,
                error_correction=error_correction,
                box_size=1,
                border=border,
            )
            qr.add_data(data)
            qr.make(fit=True)

            matrix = qr.get_matrix()
            qr_width = len(matrix[0])
            qr_height = len(matrix)
            display_height = (qr_height + 1) // 2

            # Only include variations that fit in terminal
            if qr_width <= max_width and display_height <= max_height:
                variations.append((desc, matrix))

        except Exception:
            # Skip this variation if it fails
            continue

    # If no variations fit, create a minimal one
    if not variations:
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=1,
                border=0,
            )
            qr.add_data(data)
            qr.make(fit=True)
            variations.append(("Minimal", qr.get_matrix()))
        except Exception:
            pass

    return variations


def _animate_multiple_qr_codes(
    data: str, title: str, max_width: int, max_height: int
) -> None:
    """Animate multiple different QR codes of the same data continuously."""
    # Generate QR code variations
    variations = _generate_qr_variations(data, max_width, max_height)

    if not variations:
        console.print(
            "[red]❌ Could not generate any QR codes that fit in terminal[/red]"
        )
        return

    # If only one variation fits, just display it statically (no animation needed)
    if len(variations) == 1:
        desc, matrix = variations[0]
        qr_text = _matrix_to_text(matrix)
        console.print(
            Panel(
                qr_text.rstrip(),
                title=f"[cyan]📱 {title}[/cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
        console.print("[dim]📱 Scan with your wallet app[/dim]\n")
        return

    console.print(f"[dim]Generated {len(variations)} QR code variations[/dim]")
    console.print("[dim]Each QR code encodes the same token data[/dim]\n")

    try:
        qr_index = 0
        while True:  # Cycle continuously until interrupted
            desc, matrix = variations[qr_index]

            # Clear screen for smooth animation
            console.clear()

            # Convert matrix to text
            qr_text = _matrix_to_text(matrix)

            # Display QR code with variation info
            variation_title = f"{title} - {desc} ({qr_index + 1}/{len(variations)})"
            console.print(
                Panel(
                    qr_text.rstrip(),
                    title=f"[cyan]📱 {variation_title}[/cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )

            # Show cycling indicator
            progress_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            spinner = progress_chars[qr_index % len(progress_chars)]
            console.print(
                f"[dim]{spinner} Cycling QR codes - Scan any variation to redeem[/dim]"
            )
            console.print("[dim]Press Ctrl+C to stop animation[/dim]")

            # Wait before next QR code (faster cycling)
            time.sleep(0.8)  # Faster cycling - 0.8 seconds per QR

            # Move to next variation
            qr_index = (qr_index + 1) % len(variations)

    except KeyboardInterrupt:
        console.clear()
        console.print("\n[dim]Animation stopped.[/dim]")
        # Show the first QR code statically
        if variations:
            _, matrix = variations[0]
            qr_text = _matrix_to_text(matrix)
            console.print(
                Panel(
                    qr_text.rstrip(),
                    title=f"[cyan]📱 {title}[/cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )
        console.print("[dim]📱 Scan with your wallet app[/dim]\n")


def _matrix_to_text(matrix: list[list[bool]]) -> str:
    """Convert QR code matrix to text using Unicode block characters."""
    qr_text = ""
    for i in range(0, len(matrix), 2):
        line = ""
        for j in range(len(matrix[i])):
            # Check current and next row
            top = matrix[i][j] if i < len(matrix) else False
            bottom = matrix[i + 1][j] if i + 1 < len(matrix) else False

            # Use Unicode half-block characters
            if top and bottom:
                line += "█"  # Full block
            elif top and not bottom:
                line += "▀"  # Upper half block
            elif not top and bottom:
                line += "▄"  # Lower half block
            else:
                line += " "  # Empty space
        qr_text += line + "\n"
    return qr_text


def handle_wallet_error(e: Exception) -> None:
    """Handle common wallet errors with user-friendly messages."""
    if isinstance(e, WalletError):
        if "insufficient balance" in str(e).lower():
            console.print(f"[red]💰 {e}[/red]")
        elif "already spent" in str(e).lower():
            console.print("[red]🚫 Token has already been spent![/red]")
        elif "invalid token" in str(e).lower():
            console.print("[red]❌ Invalid token format![/red]")
        else:
            console.print(f"[red]❌ {e}[/red]")
    else:
        console.print(f"[red]❌ Error: {e}[/red]")


async def prompt_user_for_mint_and_keyset(
    wallet,
    mint_unit: "CurrencyUnit",
) -> tuple[str, str]:
    """Prompt user to select mint when multiple options are available.

    Args:
        wallet: Wallet instance
        mint_unit: Currency unit to mint

    Returns:
        Tuple of (target_mint_url, keyset_id) - keyset_id is now just a placeholder

    Raises:
        typer.Exit: If user cancels selection or no valid options
    """
    # Since keysets are no longer exposed in the wallet, we'll just select a mint
    console.print(f"\n[cyan]🏦 Select a mint for {mint_unit.upper()}[/cyan]")

    # Get mints from wallet
    available_mints = wallet.mint_urls

    if not available_mints:
        console.print("[red]No mints configured[/red]")
        raise typer.Exit(1)

    # If only one mint, use it automatically
    if len(available_mints) == 1:
        return available_mints[0], "default"

    # Multiple mints - prompt user to choose
    console.print(f"Found {len(available_mints)} mints:")

    # Create selection table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Mint URL", style="green")

    for i, mint_url in enumerate(available_mints, 1):
        table.add_row(str(i), mint_url)

    console.print(table)

    # Prompt for selection
    while True:
        try:
            choice = Prompt.ask(
                f"\nSelect mint for {mint_unit.upper()} minting", default="1"
            )

            if choice.isdigit():
                choice_num = int(choice)
                if 1 <= choice_num <= len(available_mints):
                    selected_mint = available_mints[choice_num - 1]
                    console.print(f"[green]✅ Selected: {selected_mint}[/green]")
                    return selected_mint, "default"
                else:
                    console.print(
                        f"[red]Invalid choice. Please enter 1-{len(available_mints)}[/red]"
                    )
            else:
                console.print("[red]Please enter a number[/red]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Selection cancelled[/yellow]")
            raise typer.Exit(1)


async def _debug_nostr_state(wallet: Wallet) -> None:
    """Debug Nostr relay state and proof storage."""
    from datetime import datetime
    import json

    console.print("\n[cyan]🔍 Nostr Relay Debugging[/cyan]")
    console.print("=" * 50)

    # 1. Show wallet configuration
    console.print("\n[yellow]📋 Wallet Configuration:[/yellow]")
    console.print(f"  Public Key: {wallet._get_pubkey()}")
    console.print(f"  Configured Relays: {len(wallet.relay_urls)}")
    for i, relay in enumerate(wallet.relay_urls):
        console.print(f"    {i + 1}. {relay}")

    # 2. Check relay connectivity
    console.print("\n[yellow]🌐 Relay Connectivity:[/yellow]")
    try:
        relay_connections = await wallet.relay_manager.get_relay_connections()
        console.print(f"  Connected Relays: {len(relay_connections)}")

        # Show relay pool status if using queued relays
        if wallet.relay_manager.use_queued_relays and wallet.relay_manager.relay_pool:
            console.print("  Using Relay Pool: ✅")
            console.print(f"  Pool Size: {len(wallet.relay_manager.relay_pool.relays)}")
            for i, pool_relay in enumerate(wallet.relay_manager.relay_pool.relays):
                status = (
                    "🟢 Connected"
                    if hasattr(pool_relay, "ws")
                    and pool_relay.ws
                    and pool_relay.ws.close_code is None
                    else "🔴 Disconnected"
                )
                console.print(f"    {i + 1}. {pool_relay.url} - {status}")
        else:
            console.print("  Using Individual Relays: ✅")
            for i, individual_relay in enumerate(relay_connections):
                status = (
                    "🟢 Connected"
                    if hasattr(individual_relay, "ws")
                    and individual_relay.ws
                    and individual_relay.ws.close_code is None
                    else "🔴 Disconnected"
                )
                console.print(f"    {i + 1}. {individual_relay.url} - {status}")

    except Exception as e:
        console.print(f"  ❌ Relay connection error: {e}")

    # 3. Fetch raw events from relays
    console.print("\n[yellow]📡 Raw Nostr Events:[/yellow]")
    try:
        # Get relay connections
        relays = await wallet.relay_manager.get_relay_connections()
        all_events = []

        for relay_conn in relays:
            try:
                console.print(f"\n  Fetching from {relay_conn.url}...")

                # Fetch wallet events using the correct API
                wallet_events = await relay_conn.fetch_wallet_events(
                    wallet._get_pubkey(), kinds=[17375]
                )

                # Fetch token events
                token_events = await relay_conn.fetch_wallet_events(
                    wallet._get_pubkey(), kinds=[7375]
                )

                # Fetch history events
                history_events = await relay_conn.fetch_wallet_events(
                    wallet._get_pubkey(), kinds=[7376]
                )

                events_found = (
                    len(wallet_events) + len(token_events) + len(history_events)
                )
                console.print(
                    f"    Found {events_found} events (wallet: {len(wallet_events)}, tokens: {len(token_events)}, history: {len(history_events)})"
                )

                all_events.extend(wallet_events)
                all_events.extend(token_events)
                all_events.extend(history_events)

            except Exception as e:
                console.print(f"    ❌ Error fetching from {relay_conn.url}: {e}")

        # 4. Analyze events
        if all_events:
            console.print("\n[yellow]📊 Event Analysis:[/yellow]")

            # Group by kind
            events_by_kind: dict[str, list] = {}
            for event in all_events:
                kind = event.get("kind", "unknown")
                kind_str = str(kind)
                if kind_str not in events_by_kind:
                    events_by_kind[kind_str] = []
                events_by_kind[kind_str].append(event)

            for kind_str, events in events_by_kind.items():
                kind = int(kind_str) if isinstance(kind_str, str) else kind_str
                kind_name = {17375: "Wallet", 7375: "Token", 7376: "History"}.get(
                    kind, f"Kind {kind}"
                )
                console.print(f"  {kind_name} Events: {len(events)}")

                # Show recent events
                events.sort(key=lambda e: e.get("created_at", 0), reverse=True)
                for i, event in enumerate(events[:3]):  # Show most recent 3
                    timestamp = datetime.fromtimestamp(event.get("created_at", 0))
                    event_id = event.get("id", "unknown")[:16] + "..."
                    console.print(f"    {i + 1}. {event_id} at {timestamp}")

                    # Try to decode content for token events
                    if kind == 7375:  # Token events
                        try:
                            content = event.get("content", "")
                            if content:
                                # Try to decrypt if encrypted
                                try:
                                    decrypted = nip44_decrypt(content, wallet._privkey)
                                    token_data = json.loads(decrypted)
                                    proofs = token_data.get("proofs", [])
                                    mint_url = token_data.get("mint", "unknown")

                                    # Group by unit to show proper amounts
                                    unit_amounts: dict[str, int] = {}
                                    for p in proofs:
                                        unit = p.get("unit", p.get("u", "sat"))
                                        amount = p.get("amount", 0)
                                        unit_amounts[unit] = (
                                            unit_amounts.get(unit, 0) + amount
                                        )

                                    # Format unit display
                                    unit_parts = []
                                    for unit, amount in sorted(unit_amounts.items()):
                                        if unit in [
                                            "usd",
                                            "eur",
                                            "gbp",
                                            "cad",
                                            "chf",
                                            "aud",
                                            "jpy",
                                            "cny",
                                            "inr",
                                            "usdt",
                                            "usdc",
                                            "dai",
                                        ]:
                                            display_amount = amount / 100
                                            unit_parts.append(
                                                f"{display_amount:.2f} {unit}"
                                            )
                                        else:
                                            unit_parts.append(f"{amount} {unit}")
                                    amount_str = (
                                        ", ".join(unit_parts) if unit_parts else "0"
                                    )

                                    console.print(
                                        f"       → {len(proofs)} proofs, {amount_str} from {mint_url}"
                                    )
                                except Exception:
                                    console.print(
                                        f"       → Encrypted content ({len(content)} chars)"
                                    )
                        except Exception as e:
                            console.print(f"       → Parse error: {e}")
        else:
            console.print("  No events found on any relay")
    except Exception as e:
        console.print(f"  ❌ Error fetching events: {e}")


@app.command()
def balance(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    validate: Annotated[
        bool, typer.Option("--validate/--no-validate", help="Validate proofs with mint")
    ] = True,
    details: Annotated[
        bool, typer.Option("--details", "-d", help="Show detailed breakdown")
    ] = False,
    nostr_debug: Annotated[
        bool,
        typer.Option("--nostr-debug", help="Show detailed Nostr relay debugging info"),
    ] = False,
    unit: Annotated[
        Optional[str],
        typer.Option(
            "--unit", "-u", help="Filter by currency unit (sat, usd, eur, etc.)"
        ),
    ] = None,
) -> None:
    """Check wallet balance across all currencies and keysets."""

    async def _balance() -> None:
        nonlocal unit  # Explicitly declare we're using the outer scope variable
        nsec = get_nsec()
        # Use create_wallet_with_mint_selection for automatic mint discovery and selection
        wallet = await create_wallet_with_mint_selection(nsec=nsec, mint_urls=mint_urls)
        async with wallet:
            console.print("[blue]Checking balance...[/blue]")

            if nostr_debug:
                await _debug_nostr_state(wallet)

            # Get the enhanced wallet state with keyset info
            state = await wallet.fetch_wallet_state(check_proofs=validate)

            # Filter by unit if specified
            filter_unit = unit  # Capture outer scope variable
            if filter_unit:
                unit_cast = cast(CurrencyUnit, filter_unit)
                if unit_cast in state.balance_by_unit:
                    unit_balance = state.balance_by_unit[unit_cast]
                    # Convert from base units to user-friendly units for display
                    if unit_cast in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        # For fiat and stablecoins, convert from cents to dollars
                        display_balance = unit_balance / 100
                        console.print(
                            f"[green]✅ {unit_cast.upper()} Balance: {display_balance:.2f} {unit_cast}[/green]"
                        )
                    else:
                        console.print(
                            f"[green]✅ {unit_cast.upper()} Balance: {unit_balance} {unit_cast}[/green]"
                        )
                else:
                    console.print(f"[yellow]No balance in {unit_cast.upper()}[/yellow]")
                    return
            else:
                # Show all currency balances
                console.print("[green]✅ Balance by Currency:[/green]")

                if not state.balance_by_unit:
                    console.print("[yellow]No balance found[/yellow]")
                    return

                # Create currency table
                currency_table = Table(title="Currency Balances")
                currency_table.add_column("Currency", style="cyan")
                currency_table.add_column("Balance", style="green")
                currency_table.add_column("Approx USD", style="yellow")

                for currency, balance in state.balance_by_unit.items():
                    # Simple USD approximation (in production, use real exchange rates)
                    usd_value = ""
                    if currency == "sat":
                        # Assume 1 BTC = $50,000 for example
                        usd_value = f"~${balance * 50000 / 100_000_000:.2f}"
                    elif currency == "usd":
                        usd_value = f"${balance / 100:.2f}"  # Assuming cents
                    elif currency == "eur":
                        usd_value = (
                            f"~${balance * 1.1 / 100:.2f}"  # Assuming EUR/USD = 1.1
                        )

                    # Convert from base units to user-friendly display
                    if currency in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        # For fiat and stablecoins, show as dollars/euros/etc with 2 decimal places
                        display_balance = balance / 100
                        currency_table.add_row(
                            currency.upper(),
                            f"{display_balance:.2f} {currency}",
                            usd_value,
                        )
                    else:
                        currency_table.add_row(
                            currency.upper(), f"{balance} {currency}", usd_value
                        )

                console.print(currency_table)

            if details:
                # Create mint balance table
                mint_table = Table(title="Mint Details")
                mint_table.add_column("Mint", style="blue")
                mint_table.add_column("Balance", style="green")
                mint_table.add_column("Proofs", style="magenta")

                # Group proofs by mint and currency
                mint_currency_balances: dict[str, dict[str, tuple[int, int]]] = {}
                for proof in state.proofs:
                    mint_url = proof["mint"]
                    unit = cast(CurrencyUnit, proof["unit"])
                    amount = proof["amount"]

                    if mint_url not in mint_currency_balances:
                        mint_currency_balances[mint_url] = {}
                    if unit not in mint_currency_balances[mint_url]:
                        mint_currency_balances[mint_url][unit] = (0, 0)

                    balance, count = mint_currency_balances[mint_url][unit]
                    mint_currency_balances[mint_url][unit] = (
                        balance + amount,
                        count + 1,
                    )

                # Display mint details
                for mint_url, currency_balances in mint_currency_balances.items():
                    # Truncate mint URL for display
                    mint_display = (
                        mint_url[:40] + "..." if len(mint_url) > 43 else mint_url
                    )

                    # Format balance string with all currencies
                    balance_parts = []
                    total_proofs = 0
                    for currency_unit, (balance, count) in currency_balances.items():
                        balance_parts.append(f"{balance} {currency_unit}")
                        total_proofs += count

                    balance_str = ", ".join(balance_parts)

                    mint_table.add_row(
                        mint_display,
                        balance_str,
                        str(total_proofs),
                    )

                console.print("\n")
                console.print(mint_table)

                # Show denomination breakdown per mint if requested
                if typer.confirm("\nShow denomination breakdown?", default=False):
                    for mint_url, mint_proofs in state.proofs_by_mint.items():
                        if mint_proofs:
                            console.print(f"\n[cyan]Mint {mint_url[:40]}...:[/cyan]")

                            # Group by currency first
                            currency_groups: dict[str, list[Proof]] = {}
                            for proof in mint_proofs:
                                proof_unit = cast(CurrencyUnit, proof["unit"])
                                if proof_unit not in currency_groups:
                                    currency_groups[proof_unit] = []
                                currency_groups[proof_unit].append(proof)

                            # Show denominations for each currency
                            for currency_unit, unit_proofs in currency_groups.items():
                                if len(currency_groups) > 1:
                                    console.print(
                                        f"  [yellow]{currency_unit.upper()}:[/yellow]"
                                    )

                                denominations: dict[int, int] = {}
                                for proof in unit_proofs:
                                    amount = proof["amount"]
                                    denominations[amount] = (
                                        denominations.get(amount, 0) + 1
                                    )

                                for denom in sorted(denominations.keys()):
                                    count = denominations[denom]
                                    if len(currency_groups) > 1:
                                        console.print(
                                            f"    {denom} × {count} = {denom * count}"
                                        )
                                    else:
                                        console.print(
                                            f"  {denom} × {count} = {denom * count}"
                                        )

    try:
        asyncio.run(_balance())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def send(
    amount: Annotated[
        int, typer.Argument(help="Amount to send (in specified unit or sats)")
    ],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    no_qr: Annotated[
        bool, typer.Option("--no-qr", help="Don't display QR code")
    ] = False,
    to_lnurl: Annotated[
        Optional[str],
        typer.Option("--to-lnurl", help="Send directly to LNURL or Lightning address"),
    ] = None,
    unit: Annotated[
        Optional[str],
        typer.Option(
            "--unit", "-u", help="Currency unit to send from (sat, usd, eur, etc.)"
        ),
    ] = None,
    keyset: Annotated[
        Optional[str],
        typer.Option("--keyset", "-k", help="Specific keyset ID to send from"),
    ] = None,
) -> None:
    """Send tokens from wallet.

    Examples:
        Send 100 sats: nuts send 100
        Send 50 USD: nuts send 50 --unit usd
        Send from specific keyset: nuts send 100 --keyset abc123...
        Send to Lightning: nuts send 100 --to-lnurl user@getalby.com
    """

    async def _send() -> None:
        nsec = get_nsec()
        # Use create_wallet_with_mint_selection for automatic mint discovery and selection
        wallet = await create_wallet_with_mint_selection(nsec=nsec, mint_urls=mint_urls)

        async with wallet:
            # Get wallet state to check balances
            state = await wallet.fetch_wallet_state(check_proofs=False)

            # Determine which unit to use (default to "sat" if not specified)
            send_unit: CurrencyUnit = cast(CurrencyUnit, unit) if unit else "sat"

            # Check if we have balance in that unit
            if send_unit not in state.balance_by_unit:
                console.print(f"[red]No balance in {send_unit.upper()}[/red]")
                console.print("\nAvailable currencies:")
                for curr, bal in state.balance_by_unit.items():
                    console.print(f"  {curr.upper()}: {bal} {curr}")
                return

            unit_balance = state.balance_by_unit[send_unit]

            if to_lnurl:
                # Send directly to Lightning address
                console.print(
                    f"[blue]Sending {amount} {send_unit} to {to_lnurl}...[/blue]"
                )

                # Check balance first (need extra for fees)
                send_amount_base = wallet._convert_to_base_unit(amount, send_unit)
                if unit_balance <= send_amount_base:
                    # Convert balance to display units for error message
                    display_balance = wallet._convert_from_base_unit(
                        unit_balance, send_unit
                    )
                    console.print(
                        f"[red]Insufficient balance! Need >{amount}, have {display_balance:.2f} {send_unit}[/red]"
                    )
                    console.print(
                        "[dim]Lightning payments require fees (typically 1%)[/dim]"
                    )
                    return

                # Note: keyset-specific sending is no longer supported in the refactored wallet
                if keyset:
                    console.print(
                        "[yellow]Warning: Keyset-specific sending is no longer supported[/yellow]"
                    )

                actual_paid = await wallet.send_to_lnurl(
                    to_lnurl, send_amount_base, unit=send_unit
                )

                console.print("[green]✅ Successfully sent![/green]")
                console.print(f"Total paid (including fees): {actual_paid} {send_unit}")

                # Show remaining balance
                new_state = await wallet.fetch_wallet_state(check_proofs=False)
                new_balance = new_state.balance_by_unit.get(send_unit, 0)
                console.print(f"Remaining balance: {new_balance} {send_unit}")

            else:
                # Create Cashu token
                console.print(
                    f"[blue]Creating token for {amount} {send_unit}...[/blue]"
                )

                # Check balance (convert amount to base units for comparison)
                send_amount_base = wallet._convert_to_base_unit(amount, send_unit)
                if unit_balance < send_amount_base:
                    # Convert balance to display units for error message
                    display_balance = wallet._convert_from_base_unit(
                        unit_balance, send_unit
                    )
                    console.print(
                        f"[red]Insufficient balance! Need {amount}, have {display_balance:.2f} {send_unit}[/red]"
                    )
                    return

                # Note: keyset-specific sending is no longer supported in the refactored wallet
                if keyset:
                    console.print(
                        "[yellow]Warning: Keyset-specific sending is no longer supported[/yellow]"
                    )

                # Convert amount to base units if needed
                send_amount = wallet._convert_to_base_unit(amount, send_unit)

                # Use the first mint URL if provided via command line
                target_mint_url = mint_urls[0] if mint_urls else None
                token = await wallet.send(
                    send_amount, mint_url=target_mint_url, unit=send_unit
                )

                console.print(
                    f"\n[green]✅ Cashu Token Created ({amount} {send_unit}):[/green]"
                )
                # Display token without line wrapping for easy copying
                console.print(token, soft_wrap=True, no_wrap=True)

                # Display QR code unless disabled
                if not no_qr:
                    display_qr_code(token, f"Cashu Token ({amount} {send_unit})")
                else:
                    console.print()

                # Show remaining balance
                new_state = await wallet.fetch_wallet_state(check_proofs=False)
                new_balance = new_state.balance_by_unit.get(send_unit, 0)
                console.print(
                    f"[dim]Remaining balance: {new_balance} {send_unit}[/dim]"
                )

    try:
        asyncio.run(_send())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def redeem(
    token: Annotated[str, typer.Argument(help="Cashu token to redeem")],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    auto_swap: Annotated[
        bool,
        typer.Option(
            "--auto-swap/--no-auto-swap", help="Auto-swap from untrusted mints"
        ),
    ] = True,
    to_lnurl: Annotated[
        Optional[str],
        typer.Option("--to-lnurl", help="Forward to LNURL or Lightning address"),
    ] = None,
) -> None:
    """Redeem a Cashu token - add to wallet or forward to Lightning address.

    Redeem to wallet:
        nuts redeem cashuA...

    Redeem and forward to Lightning address:
        nuts redeem --to-lnurl user@getalby.com cashuA...
    """

    async def _redeem():
        if to_lnurl:
            # Redeem token and forward to Lightning address
            console.print(
                "[blue]Redeeming token and sending to Lightning address...[/blue]"
            )

            amount_sent = await redeem_to_lnurl(token, to_lnurl)

            console.print(
                f"[green]✅ Successfully sent {amount_sent} sats to {to_lnurl}![/green]"
            )

        else:
            # Normal redeem to wallet
            nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet = await create_wallet_with_mint_selection(
                nsec=nsec, mint_urls=mint_urls
            )
            async with wallet:
                console.print("[blue]Redeeming token...[/blue]")

                # Check balance before
                balance_before = await wallet.get_balance()

                amount, unit = await wallet.redeem(token, auto_swap=auto_swap)

                console.print(
                    f"[green]✅ Successfully redeemed {amount} {unit}![/green]"
                )

                # Check balance after
                await asyncio.sleep(0.5)  # Give relays time to update
                balance_after = await wallet.get_balance()
                added = balance_after - balance_before

                console.print(
                    f"[green]Balance: {balance_before} → {balance_after} sats (+{added})[/green]"
                )

    try:
        asyncio.run(_redeem())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def pay(
    invoice: Annotated[str, typer.Argument(help="Lightning invoice (bolt11)")],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
) -> None:
    """Pay a Lightning invoice."""

    async def _pay():
        nsec = get_nsec()
        # Use create_wallet_with_mint_selection for automatic mint discovery and selection
        wallet = await create_wallet_with_mint_selection(nsec=nsec, mint_urls=mint_urls)
        async with wallet:
            console.print("[blue]Paying Lightning invoice...[/blue]")

            # Check balance first
            balance = await wallet.get_balance()
            console.print(f"Current balance: {balance} sats")

            await wallet.melt(invoice)

            console.print("[green]✅ Payment successful![/green]")

            # Show remaining balance
            balance = await wallet.get_balance()
            console.print(f"Remaining balance: {balance} sats")

    try:
        asyncio.run(_pay())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def mint(
    amount: Annotated[
        int, typer.Argument(help="Amount to mint (in specified unit or sats)")
    ],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", "-t", help="Payment timeout in seconds")
    ] = 300,
    no_qr: Annotated[
        bool, typer.Option("--no-qr", help="Don't display QR code")
    ] = False,
    unit: Annotated[
        Optional[str],
        typer.Option(
            "--unit", "-u", help="Currency unit to mint (sat, usd, eur, etc.)"
        ),
    ] = None,
    keyset: Annotated[
        Optional[str],
        typer.Option("--keyset", "-k", help="Specific keyset ID to mint with"),
    ] = None,
) -> None:
    """Create Lightning invoice to mint new tokens.

    Examples:
        Mint 1000 sats: nuts mint 1000
        Mint 50 USD: nuts mint 50 --unit usd
        Mint with specific keyset: nuts mint 100 --keyset abc123...
    """

    async def _mint() -> None:
        nsec = get_nsec()
        # Use create_wallet_with_mint_selection for automatic mint discovery and selection
        wallet = await create_wallet_with_mint_selection(nsec=nsec, mint_urls=mint_urls)

        # Determine which unit to mint (default to "sat" if not specified)
        mint_unit: CurrencyUnit = cast(CurrencyUnit, unit) if unit else "sat"

        async with wallet:
            # If keyset specified, show warning that this is no longer supported
            if keyset:
                console.print(
                    "[yellow]Warning: Keyset-specific minting is no longer supported in the refactored wallet[/yellow]"
                )
                console.print(
                    "[dim]The wallet will automatically select the best mint for your request[/dim]"
                )

            console.print(f"[blue]Checking which mints support {mint_unit}...[/blue]")

            # Find mints that support the requested currency unit
            supporting_mints = await wallet.get_mints_supporting_unit(mint_unit)

            if not supporting_mints:
                console.print(
                    f"[red]❌ No configured mints support {mint_unit.upper()}![/red]"
                )
                console.print("\nConfigured mints:")
                for mint in wallet.mint_urls:
                    console.print(f"  • {mint}")
                console.print(
                    f"\n[yellow]💡 Try adding a mint that supports {mint_unit.upper()}[/yellow]"
                )
                return

            # Determine which mint to use
            target_mint_url = None

            # If --mint was specified, check if it supports the unit
            if mint_urls:
                specified_mint = mint_urls[0]
                if specified_mint in supporting_mints:
                    target_mint_url = specified_mint
                else:
                    console.print(
                        f"[red]❌ {specified_mint} does not support {mint_unit.upper()}![/red]"
                    )
                    console.print(f"\nMints that support {mint_unit.upper()}:")
                    for mint in supporting_mints:
                        console.print(f"  • {mint}")
                    return
            elif len(supporting_mints) == 1:
                # Only one mint supports this unit, use it automatically
                target_mint_url = supporting_mints[0]
                console.print(
                    f"[dim]Using mint: {target_mint_url} (only mint supporting {mint_unit.upper()})[/dim]"
                )
            else:
                # Multiple mints support this unit, prompt user to select
                console.print(
                    f"\n[cyan]Multiple mints support {mint_unit.upper()}:[/cyan]"
                )

                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("#", style="dim", width=3)
                table.add_column("Mint URL", style="green")

                for i, mint_url in enumerate(supporting_mints, 1):
                    table.add_row(str(i), mint_url)

                console.print(table)

                while True:
                    choice = Prompt.ask(
                        f"\nSelect mint for {mint_unit.upper()} minting", default="1"
                    )

                    if choice.isdigit():
                        choice_num = int(choice)
                        if 1 <= choice_num <= len(supporting_mints):
                            target_mint_url = supporting_mints[choice_num - 1]
                            console.print(
                                f"[green]✅ Selected: {target_mint_url}[/green]"
                            )
                            break
                        else:
                            console.print(
                                f"[red]Invalid choice. Please enter 1-{len(supporting_mints)}[/red]"
                            )
                    else:
                        console.print("[red]Please enter a number[/red]")

            console.print(
                f"\n[blue]Creating invoice for {amount} {mint_unit}...[/blue]"
            )

            try:
                # Create mint quote with the specified currency
                invoice, task = await wallet.mint_async(
                    amount,
                    mint_url=target_mint_url,
                    currency=mint_unit,
                    timeout=timeout,
                )

                # Display invoice for easy copying
                console.print(
                    f"\n[yellow]⚡ Lightning Invoice ({amount} {mint_unit}):[/yellow]"
                )
                # Display invoice without line wrapping so it can be copied completely
                console.print(invoice, soft_wrap=True, no_wrap=True)

                # Display QR code unless disabled
                if not no_qr:
                    display_qr_code(
                        invoice, f"Lightning Invoice ({amount} {mint_unit})"
                    )
                else:
                    console.print()

                console.print("[blue]Waiting for payment...[/blue]")
                console.print("[dim]Press Ctrl+C to cancel and return to CLI[/dim]")

                try:
                    paid = await task

                    if paid:
                        console.print(
                            f"[green]✅ Payment received! {amount} {mint_unit} minted.[/green]"
                        )

                        # Show updated balance
                        state = await wallet.fetch_wallet_state(check_proofs=False)
                        if mint_unit in state.balance_by_unit:
                            new_balance = state.balance_by_unit[mint_unit]
                            console.print(
                                f"New {mint_unit.upper()} balance: {new_balance} {mint_unit}"
                            )
                    else:
                        console.print(
                            f"[red]❌ Payment timeout after {timeout} seconds[/red]"
                        )
                        console.print(
                            "[yellow]Invoice is still valid - you can pay it later[/yellow]"
                        )

                except Exception as payment_error:
                    console.print(
                        f"[red]❌ Payment polling error: {payment_error}[/red]"
                    )
                    console.print(
                        "[yellow]Invoice may still be valid - check with your Lightning wallet[/yellow]"
                    )
            except Exception as e:
                console.print(f"[red]❌ Failed to create invoice: {e}[/red]")
                raise

    try:
        asyncio.run(_mint())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def info(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
) -> None:
    """Show comprehensive wallet information including currencies."""

    async def _info():
        nsec = get_nsec()
        # Use create_wallet_with_mint_selection for automatic mint discovery and selection
        wallet = await create_wallet_with_mint_selection(nsec=nsec, mint_urls=mint_urls)
        async with wallet:
            console.print("[blue]Fetching wallet information...[/blue]")

            # Get wallet state
            state = await wallet.fetch_wallet_state(check_proofs=False)

            # Create info table
            table = Table(title="Wallet Information")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")

            # Basic info
            table.add_row("Public Key", wallet._get_pubkey())
            table.add_row("Total Proofs", str(len(state.proofs)))

            # Currency breakdown
            if state.balance_by_unit:
                table.add_row("", "")  # Empty row for spacing
                table.add_row("[bold]Currency Balances[/bold]", "")
                for currency, balance in sorted(state.balance_by_unit.items()):
                    # Format based on currency type
                    if currency in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        display_balance = balance / 100
                        table.add_row(
                            f"  {currency.upper()}", f"{display_balance:.2f} {currency}"
                        )
                    else:
                        table.add_row(f"  {currency.upper()}", f"{balance} {currency}")

            # Mint info
            table.add_row("", "")  # Empty row for spacing
            table.add_row("[bold]Mints[/bold]", "")
            table.add_row("Configured Mints", str(len(wallet.mint_urls)))

            for i, mint_url in enumerate(wallet.mint_urls):
                table.add_row(f"  Mint {i + 1}", mint_url)

            # Relay info
            table.add_row("", "")  # Empty row for spacing
            table.add_row("[bold]Relays[/bold]", "")
            table.add_row("Configured Relays", str(len(wallet.relay_urls)))
            for i, relay_url in enumerate(wallet.relay_urls):
                table.add_row(f"  Relay {i + 1}", relay_url)

            console.print(table)

    try:
        asyncio.run(_info())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


def version_callback(value: bool) -> None:
    """Handle version flag."""
    if value:
        console.print("Sixty Nuts v0.0.8")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=version_callback, help="Show version"),
    ] = None,
) -> None:
    """Sixty Nuts - NIP-60 Cashu Wallet CLI.

    🌐 RELAY CONFIGURATION (auto-cached for speed):
    Relays are discovered in priority order:
    1. Environment variable: RELAYS="wss://relay1.com,wss://relay2.com" (fastest)
    2. Auto-discovery from your Nostr profile (NIP-65) - cached in .env file
    3. Interactive prompt (first run) - cached in .env file

    💡 Performance tip: Set RELAYS env var or let discovery populate .env file
    Visit https://nostr.watch to find more relays

    📝 NSEC CONFIGURATION:
    Your Nostr private key can be set via:
    • Environment variable: NSEC="nsec1..."
    • Stored in cwd/.env file: NSEC="nsec1..."
    • Interactive prompt (secure input)
    """
    pass


@app.command()
def relays(
    list_configured: Annotated[
        bool, typer.Option("--list", help="List currently configured relays")
    ] = False,
    test_connectivity: Annotated[
        bool, typer.Option("--test", help="Test connectivity to configured relays")
    ] = False,
    discover: Annotated[
        bool, typer.Option("--discover", help="Discover relays from your Nostr profile")
    ] = False,
    configure: Annotated[
        bool, typer.Option("--configure", help="Configure relays interactively")
    ] = False,
    clear_cache: Annotated[
        bool, typer.Option("--clear-cache", help="Clear relay cache (.env file)")
    ] = False,
) -> None:
    """Manage Nostr relay configuration.

    Examples:
        nuts relays --list                    # Show current relays
        nuts relays --test                    # Test relay connectivity
        nuts relays --discover               # Find relays from your profile
        nuts relays --configure              # Interactive relay setup
        nuts relays --clear-cache            # Clear session cache
        nuts relays --clear-cache --discover # Force fresh discovery
    """

    async def _relays():
        from .relay import (
            get_relays_from_env,
            discover_relays_from_nip65,
            validate_relay_url,
            clear_relays_from_env,
        )
        from .crypto import decode_nsec, get_pubkey

        # Handle cache clearing first
        if clear_cache:
            cleared = clear_relays_from_env()
            if cleared:
                console.print(
                    "[green]🗑️ Cleared relay cache from environment and .env file[/green]"
                )
                console.print("[dim]Next relay operation will do fresh discovery[/dim]")
            else:
                console.print("[yellow]ℹ️ No relay cache to clear[/yellow]")

            # If only clearing cache, exit early
            if not any([list_configured, test_connectivity, discover, configure]):
                return

        # If no flags specified, show current configuration
        show_list = list_configured
        if not any([list_configured, test_connectivity, discover, configure]):
            show_list = True

        if show_list:
            console.print("\n[cyan]🌐 Current Relay Configuration[/cyan]")

            # Check environment variable and .env file
            env_relays = get_relays_from_env()
            if env_relays:
                console.print(
                    f"[green]✅ Found {len(env_relays)} relays (env var or .env file):[/green]"
                )
                for i, relay in enumerate(env_relays, 1):
                    status = "✅ Valid" if validate_relay_url(relay) else "❌ Invalid"
                    console.print(f"  {i}. {relay} - {status}")
                console.print("[dim]💡 Using fast path - no Nostr queries needed[/dim]")
                console.print(
                    "[dim]Run 'nuts relays --clear-cache' to force fresh discovery[/dim]"
                )
            else:
                console.print(
                    "[yellow]⚠️ No relays in environment variable or .env file[/yellow]"
                )
                console.print(
                    f'   Set with: export {RELAYS_ENV_VAR}="wss://relay1.com,wss://relay2.com"'
                )
                console.print(
                    "[dim]💡 Will auto-cache discovered relays in .env file[/dim]"
                )

        if discover:
            console.print("\n[cyan]🔍 Discovering Relays from Nostr Profile[/cyan]")

            # Check if we already have cached relays to inform user (unless cache was just cleared)
            env_relays = get_relays_from_env()
            if env_relays and not clear_cache:
                console.print(
                    "[blue]ℹ️ Cached relays found - skipping Nostr discovery for speed[/blue]"
                )
                console.print(
                    "Use --list to see current relays or --clear-cache to force discovery"
                )
                return

            try:
                nsec = get_nsec()
                privkey = decode_nsec(nsec)
                pubkey = get_pubkey(privkey)

                console.print(f"Looking up relays for: {pubkey}")
                # Enable debug mode for discovery
                discovered = await discover_relays_from_nip65(pubkey, debug=True)

                if discovered:
                    console.print(f"[green]✅ Found {len(discovered)} relays:[/green]")
                    for i, relay in enumerate(discovered, 1):
                        console.print(f"  {i}. {relay}")

                    # Cache them in .env file
                    from .relay import set_relays_in_env

                    set_relays_in_env(discovered)
                    console.print("[blue]💾 Cached relays in .env file[/blue]")
                else:
                    console.print(
                        "[yellow]⚠️ No relays found in your Nostr profile[/yellow]"
                    )
                    console.print(
                        "   Consider publishing a relay list (NIP-65) with your Nostr client"
                    )
                    console.print(
                        "   Or run 'nuts relays --configure' to set up and publish relays"
                    )

            except Exception as e:
                console.print(f"[red]❌ Discovery failed: {e}[/red]")

        if test_connectivity:
            console.print("\n[cyan]🔗 Testing Relay Connectivity[/cyan]")

            # Get relays to test
            env_relays = get_relays_from_env()
            test_relays = env_relays

            if not test_relays:
                try:
                    nsec = get_nsec()
                    privkey = decode_nsec(nsec)
                    pubkey = get_pubkey(privkey)
                    test_relays = await discover_relays_from_nip65(pubkey)
                except Exception:
                    pass

            if not test_relays:
                console.print(
                    "[yellow]⚠️ No relays to test. Use --configure to set up relays first.[/yellow]"
                )
                return

            from .relay import Relay

            for i, relay_url in enumerate(test_relays, 1):
                console.print(f"  {i}. Testing {relay_url}...")
                try:
                    relay = Relay(relay_url)
                    await relay.connect()
                    console.print("     [green]✅ Connected successfully[/green]")
                    await relay.disconnect()
                except Exception as e:
                    console.print(f"     [red]❌ Failed: {e}[/red]")

        if configure:
            console.print("\n[cyan]⚙️ Interactive Relay Configuration[/cyan]")
            try:
                nsec = get_nsec()
                privkey = decode_nsec(nsec)
                selected_relays = await prompt_user_for_relays(privkey)
                console.print(
                    f"\n[green]✅ Configuration complete with {len(selected_relays)} relays[/green]"
                )

                # Test discovery after configuration
                console.print("\n[blue]🧪 Testing NIP-65 discovery...[/blue]")
                pubkey = get_pubkey(privkey)
                discovered = await discover_relays_from_nip65(pubkey, debug=True)

                if discovered:
                    console.print(
                        f"[green]🎉 Discovery test successful! Found {len(discovered)} relays[/green]"
                    )
                else:
                    console.print(
                        "[yellow]⚠️ Discovery test failed - you may need to wait a moment for relay propagation[/yellow]"
                    )
                    console.print(
                        "[dim]Try running 'nuts relays --discover' in a few seconds[/dim]"
                    )

            except Exception as e:
                console.print(f"[red]❌ Configuration failed: {e}[/red]")

    try:
        asyncio.run(_relays())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def status(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    init: Annotated[
        bool, typer.Option("--init", help="Initialize if not initialized")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force create new initialization")
    ] = False,
) -> None:
    """Show initialization status and configuration.

    By default shows current initialization status and configuration.
    Use --init to initialize if not already initialized.
    Use --force with --init to force re-initialization (overwrites existing).
    """

    async def _status():
        try:
            nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet_obj = await create_wallet_with_mint_selection(
                nsec=nsec, mint_urls=mint_urls
            )
            async with wallet_obj:
                # Check if wallet exists
                (
                    exists,
                    existing_event,
                ) = await wallet_obj.event_manager.check_wallet_event_exists()

                # Handle initialization if requested
                if init:
                    if exists and existing_event and not force:
                        from datetime import datetime

                        created_time = datetime.fromtimestamp(
                            existing_event["created_at"]
                        )
                        console.print(
                            f"[green]✅ Wallet already exists (created at {created_time})[/green]"
                        )
                        console.print(f"   Event ID: {existing_event['id'][:16]}...")
                        console.print("   Use --force to create a new wallet event")
                    else:
                        # Initialize wallet (create event)
                        # Generate a new wallet privkey if not already set
                        if wallet_obj.wallet_privkey is None:
                            wallet_obj.wallet_privkey = (
                                wallet_obj._generate_wallet_privkey()
                            )

                        if force:
                            console.print("🔄 Force creating new wallet event...")
                            created = await wallet_obj.event_manager.initialize_wallet(
                                wallet_obj.wallet_privkey, force=True
                            )
                        else:
                            console.print("🔄 Creating wallet event...")
                            created = await wallet_obj.event_manager.initialize_wallet(
                                wallet_obj.wallet_privkey, force=False
                            )

                        if created:
                            console.print(
                                "[green]✅ Wallet initialized successfully![/green]"
                            )
                            # Refresh wallet event info
                            (
                                exists,
                                existing_event,
                            ) = await wallet_obj.event_manager.check_wallet_event_exists()
                        else:
                            console.print("[yellow]ℹ️ Wallet already existed[/yellow]")
                else:
                    console.print("🔄 Checking wallet status...")

                # Show wallet status
                if exists and existing_event:
                    from datetime import datetime

                    created_time = datetime.fromtimestamp(existing_event["created_at"])
                    console.print("[green]✅ Wallet is initialized[/green]")
                    console.print(f"   Created: {created_time}")
                    console.print(f"   Event ID: {existing_event['id'][:16]}...")

                    # Try to decrypt wallet content to show configuration
                    try:
                        content = nip44_decrypt(
                            existing_event["content"], wallet_obj._privkey
                        )
                        import json

                        wallet_data = json.loads(content)

                        console.print("\n[yellow]📋 Wallet Configuration:[/yellow]")
                        console.print(f"  Public Key: {wallet_obj._get_pubkey()}")

                        mint_count = sum(1 for item in wallet_data if item[0] == "mint")
                        console.print(f"  Configured Mints: {mint_count}")
                        for item in wallet_data:
                            if item[0] == "mint":
                                console.print(f"    • {item[1]}")

                        has_privkey = any(item[0] == "privkey" for item in wallet_data)
                        console.print(
                            f"  P2PK Key: {'✅ Configured' if has_privkey else '❌ Not set'}"
                        )

                        console.print(f"  Relays: {len(wallet_obj.relay_urls)}")
                        for i, relay in enumerate(wallet_obj.relay_urls):
                            console.print(f"    {i + 1}. {relay}")

                    except Exception as e:
                        console.print(
                            f"[dim]Could not decrypt wallet configuration: {e}[/dim]"
                        )

                else:
                    console.print("[red]❌ Wallet not initialized[/red]")
                    console.print("   Run 'nuts status --init' to initialize")

        except WalletError as e:
            if "already exists" in str(e):
                console.print(
                    "[yellow]ℹ️ Wallet already exists. Use --force to override.[/yellow]"
                )
            else:
                handle_wallet_error(e)
        except Exception as e:
            handle_wallet_error(e)

    asyncio.run(_status())


@app.command()
def erase(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    wallet: Annotated[
        bool, typer.Option("--wallet", help="Delete wallet configuration events")
    ] = False,
    history: Annotated[
        bool, typer.Option("--history", help="Delete transaction history events")
    ] = False,
    tokens: Annotated[
        bool,
        typer.Option(
            "--tokens", help="Delete token storage events (⚠️ AFFECTS BALANCE)"
        ),
    ] = False,
    nsec: Annotated[
        bool, typer.Option("--nsec", help="Clear locally stored NSEC")
    ] = False,
    all_events: Annotated[
        bool,
        typer.Option(
            "--all", help="Delete wallet, history, and token events (⚠️ NUCLEAR)"
        ),
    ] = False,
    confirm: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Erase wallet data stored on Nostr relays and local storage.

    Use --wallet to delete wallet configuration (requires re-initialization)
    Use --history to delete transaction history (keeps wallet intact)
    Use --tokens to delete token storage (⚠️  WILL AFFECT YOUR BALANCE!)
    Use --nsec to clear locally stored NSEC key
    Use --all to delete everything (⚠️  NUCLEAR OPTION!)

    Note: --all only affects Nostr data, not local NSEC. Use --nsec separately.
    """

    async def _erase():
        try:
            # Validate options
            if not any([wallet, history, tokens, nsec, all_events]):
                console.print("[red]❌ Please specify what to clean up:[/red]")
                console.print("  --wallet   Delete wallet configuration events")
                console.print("  --history  Delete transaction history events")
                console.print(
                    "  --tokens   Delete token storage events [red](⚠️  AFFECTS BALANCE!)[/red]"
                )
                console.print("  --nsec     Clear locally stored NSEC key")
                console.print(
                    "  --all      Delete everything [red](⚠️  NUCLEAR OPTION!)[/red]"
                )
                console.print("\nExample: nuts erase --history")
                return

            if all_events and (wallet or history or tokens):
                console.print(
                    "[red]❌ Cannot use --all with other specific flags (except --nsec)[/red]"
                )
                console.print("Use either --all by itself, or combine specific flags")
                console.print("Note: --nsec can be combined with --all")
                return

            # Set flags for what to clean
            clean_wallet = wallet or all_events
            clean_history = history or all_events
            clean_tokens = tokens or all_events
            clean_nsec = nsec

            # Handle NSEC clearing first (doesn't require wallet connection)
            nsec_existed = False
            if clean_nsec:
                # Check .env file
                env_nsec = get_nsec_from_env()
                if env_nsec:
                    nsec_existed = True
                    if not confirm:
                        console.print(
                            "\n[yellow]⚠️  Will delete local NSEC storage:[/yellow]"
                        )
                        console.print("   🗂️  NSEC from .env file")
                        if os.getenv("NSEC"):
                            console.print(
                                "   [dim]Note: Environment variable NSEC will remain set[/dim]"
                            )

                elif not any([clean_wallet, clean_history, clean_tokens]):
                    # Only NSEC requested but no file exists
                    console.print("[yellow]ℹ️ No stored NSEC file found[/yellow]")
                    if os.getenv("NSEC"):
                        console.print(
                            "[yellow]Environment variable NSEC is still set[/yellow]"
                        )
                        console.print("Unset it with: unset NSEC")
                    return

            # Skip wallet operations if only NSEC cleaning and no other operations
            if clean_nsec and not any([clean_wallet, clean_history, clean_tokens]):
                if not confirm and nsec_existed:
                    confirm_delete = Prompt.ask(
                        "\nProceed with NSEC deletion?",
                        choices=["yes", "no"],
                        default="no",
                    )
                    if confirm_delete != "yes":
                        console.print("[yellow]❌ NSEC deletion cancelled[/yellow]")
                        return

                # Clear NSEC from .env file
                if nsec_existed:
                    cleared_env = clear_nsec_from_env()
                    if cleared_env:
                        console.print("[green]🗑️ Cleared NSEC from .env file[/green]")
                    if os.getenv("NSEC"):
                        console.print(
                            "[yellow]Environment variable NSEC is still set[/yellow]"
                        )
                        console.print("Unset it with: unset NSEC")
                return

            # Get NSEC for wallet operations (this will prompt if needed and file was just deleted)
            wallet_nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet_obj = await create_wallet_with_mint_selection(
                nsec=wallet_nsec, mint_urls=mint_urls
            )
            async with wallet_obj:
                console.print("🔄 Scanning for events to erase...")

                # Check what exists
                wallet_exists = False
                history_count = 0
                token_count = 0

                if clean_wallet:
                    (
                        exists,
                        _,
                    ) = await wallet_obj.event_manager.check_wallet_event_exists()
                    wallet_exists = exists

                if clean_history:
                    history_entries = (
                        await wallet_obj.event_manager.fetch_spending_history()
                    )
                    history_count = len(history_entries)

                if clean_tokens:
                    token_count = await wallet_obj.event_manager.count_token_events()
                    # Get current balance to show user what they're about to lose
                    try:
                        state = await wallet_obj.fetch_wallet_state(check_proofs=False)
                        current_balance_by_unit = state.balance_by_unit
                    except Exception:
                        current_balance_by_unit = {}

                # Show what will be deleted
                if not wallet_exists and history_count == 0 and token_count == 0:
                    console.print("[yellow]ℹ️ No events found to erase[/yellow]")
                    return

                total_events = 0
                erase_summary = []

                if clean_wallet and wallet_exists:
                    erase_summary.append("🗂️  Wallet configuration events")
                    total_events += 1  # Approximate, could be more

                if clean_history and history_count > 0:
                    erase_summary.append(
                        f"📊 {history_count} transaction history events"
                    )
                    total_events += history_count

                if clean_tokens and token_count > 0:
                    # Format balance summary
                    balance_parts = []
                    for currency, balance in sorted(current_balance_by_unit.items()):
                        if currency in [
                            "usd",
                            "eur",
                            "gbp",
                            "cad",
                            "chf",
                            "aud",
                            "jpy",
                            "cny",
                            "inr",
                            "usdt",
                            "usdc",
                            "dai",
                        ]:
                            display_balance = balance / 100
                            balance_parts.append(f"{display_balance:.2f} {currency}")
                        else:
                            balance_parts.append(f"{balance} {currency}")
                    balance_str = ", ".join(balance_parts) if balance_parts else "0"

                    erase_summary.append(
                        f"💰 {token_count} token storage events [red](containing {balance_str}!)[/red]"
                    )
                    total_events += token_count

                if clean_nsec and nsec_existed:
                    erase_summary.append("🗂️  Local NSEC storage file")

                # Show warning and confirm
                if not confirm:
                    console.print(
                        f"\n[yellow]⚠️  Will delete {total_events}+ events:[/yellow]"
                    )
                    for item in erase_summary:
                        console.print(f"   {item}")

                    console.print("\n[dim]What stays safe:[/dim]")
                    console.print("   ✅ Your private keys (stored locally)")
                    console.print("   ✅ Your NSEC key")

                    if clean_tokens:
                        console.print(
                            f"\n[red]💀 DANGER: You will lose {balance_str} stored on Nostr![/red]"
                        )
                        console.print(
                            "[red]   This deletes your actual token storage, not just metadata![/red]"
                        )
                        console.print(
                            "[red]   Only do this if you want to completely reset your wallet![/red]"
                        )
                    else:
                        console.print("   ✅ Your tokens and balance")

                    if clean_wallet:
                        console.print(
                            "\n[yellow]Note: After deleting wallet config, run 'nuts init-wallet' to recreate it[/yellow]"
                        )

                    # Extra confirmation for dangerous operations
                    if clean_tokens:
                        # For confirmation, use a simplified balance string
                        confirm_balance_str = balance_str.upper().replace(",", " AND")
                        confirm_msg = f"\n[red]Type 'DELETE {confirm_balance_str}' to confirm token deletion[/red]"
                        expected_response = f"DELETE {confirm_balance_str}"
                        user_response = Prompt.ask(confirm_msg)

                        if user_response != expected_response:
                            console.print(
                                "[yellow]❌ Token deletion cancelled (confirmation failed)[/yellow]"
                            )
                            return

                    confirm_delete = Prompt.ask(
                        "\nProceed with erase?", choices=["yes", "no"], default="no"
                    )

                    if confirm_delete != "yes":
                        console.print("[yellow]❌ Erase cancelled[/yellow]")
                        return

                # Perform erase operations
                total_deleted = 0

                if clean_wallet and wallet_exists:
                    console.print("🗑️ Deleting wallet configuration events...")
                    wallet_deleted = (
                        await wallet_obj.event_manager.delete_all_wallet_events()
                    )
                    total_deleted += wallet_deleted
                    console.print(f"   ✅ Deleted {wallet_deleted} wallet event(s)")

                if clean_history and history_count > 0:
                    console.print("🗑️ Deleting transaction history events...")
                    history_deleted = (
                        await wallet_obj.event_manager.clear_spending_history()
                    )
                    total_deleted += history_deleted
                    console.print(f"   ✅ Deleted {history_deleted} history event(s)")

                if clean_tokens and token_count > 0:
                    console.print(f"🗑️ Deleting token storage events ({balance_str})...")
                    tokens_deleted = (
                        await wallet_obj.event_manager.clear_all_token_events()
                    )
                    total_deleted += tokens_deleted
                    console.print(
                        f"   💀 Deleted {tokens_deleted} token event(s) containing {balance_str}"
                    )

                if clean_nsec and nsec_existed:
                    console.print("🗑️ Deleting local NSEC storage...")
                    # Clear from .env file and environment
                    cleared_env = clear_nsec_from_env()
                    if cleared_env:
                        console.print(
                            "   ✅ Cleared NSEC from .env file and environment"
                        )
                    else:
                        console.print("   ℹ️ No NSEC found to clear")

                # Summary
                console.print(
                    f"\n[green]🎉 Erase complete! Deleted {total_deleted} event(s) in optimized batches[/green]"
                )

                if clean_tokens:
                    console.print(
                        "\n[red]⚠️  Your Nostr balance is now 0 in all currencies[/red]"
                    )
                    console.print(
                        "   Any tokens you had are no longer accessible from Nostr relays"
                    )

                if clean_wallet:
                    console.print("\n[cyan]💡 Next steps:[/cyan]")
                    console.print(
                        "   Run 'nuts init-wallet' to recreate wallet configuration"
                    )
                    if not clean_tokens:
                        console.print("   Your tokens and balance are unaffected")

        except Exception as e:
            handle_wallet_error(e)

        asyncio.run(_erase())


@app.command()
def cleanup(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Show what would be cleaned up without making changes"
        ),
    ] = False,
    confirm: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Clean up wallet state by consolidating old/corrupted token events.

    This command identifies undecryptable or empty token events and consolidates
    all valid proofs into fresh events. This is useful for wallets that have
    accumulated many old events due to encryption key changes or corrupted data.

    The cleanup uses the NIP-60 'del' field mechanism to mark old events as
    superseded, making it work even on relays that don't support deletion events.
    """

    async def _cleanup():
        try:
            nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet = await create_wallet_with_mint_selection(
                nsec=nsec, mint_urls=mint_urls
            )
            async with wallet:
                # Get current state for confirmation
                state = await wallet.fetch_wallet_state(check_proofs=False)
                token_count = await wallet.event_manager.count_token_events()

                console.print("[cyan]🧹 Wallet Cleanup Tool[/cyan]")
                console.print("Current balance:")
                for currency, balance in sorted(state.balance_by_unit.items()):
                    if currency in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        display_balance = balance / 100
                        console.print(
                            f"  {currency.upper()}: {display_balance:.2f} {currency}"
                        )
                    else:
                        console.print(f"  {currency.upper()}: {balance} {currency}")
                console.print(f"Current token events: {token_count}")

                if not dry_run and not confirm:
                    console.print(
                        "\n[yellow]This will consolidate old/corrupted events into fresh ones.[/yellow]"
                    )
                    console.print(
                        "[yellow]Old events will be marked as superseded using NIP-60 'del' fields.[/yellow]"
                    )
                    console.print(
                        "[yellow]Your balance and proofs will remain unchanged.[/yellow]"
                    )

                    confirm_cleanup = Prompt.ask(
                        "\nProceed with cleanup?", choices=["yes", "no"], default="no"
                    )

                    if confirm_cleanup != "yes":
                        console.print("[yellow]❌ Cleanup cancelled[/yellow]")
                        return

                # Perform cleanup
                print("🧹 Starting wallet state cleanup...")

                # Get current state
                state = await wallet.fetch_wallet_state(
                    check_proofs=True, check_local_backups=False
                )

                # Fetch all events to analyze
                all_events = await wallet.relay_manager.fetch_wallet_events(
                    wallet.pubkey
                )
                token_events = [e for e in all_events if e["kind"] == EventKind.Token]

                # Categorize events
                valid_events = []
                undecryptable_events = []
                empty_events = []

                for event in token_events:
                    try:
                        decrypted = nip44_decrypt(event["content"], wallet._privkey)
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
                    "balance_by_unit": state.balance_by_unit,
                    "events_consolidated": 0,
                    "events_marked_superseded": 0,
                }

                # Format balance string
                balance_parts = []
                for currency, balance in sorted(state.balance_by_unit.items()):
                    if currency in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        display_balance = balance / 100
                        balance_parts.append(f"{display_balance:.2f} {currency}")
                    else:
                        balance_parts.append(f"{balance} {currency}")
                total_balance_str = ", ".join(balance_parts) if balance_parts else "0"

                print(f"📊 Analysis: {stats['total_events']} total events")
                print(f"   ✅ Valid: {stats['valid_events']}")
                print(f"   ❌ Undecryptable: {stats['undecryptable_events']}")
                print(f"   📭 Empty: {stats['empty_events']}")
                print(
                    f"   💰 Valid proofs: {stats['valid_proofs']} ({total_balance_str})"
                )

                if dry_run:
                    print("🔍 Dry run - no changes will be made")
                    return stats

                # Only consolidate if we have significant cleanup opportunity
                cleanup_threshold = max(
                    5, len(token_events) // 3
                )  # At least 5 events or 1/3 of total
                events_to_cleanup = undecryptable_events + empty_events

                if len(events_to_cleanup) < cleanup_threshold:
                    print(
                        f"🎯 No significant cleanup needed (threshold: {cleanup_threshold})"
                    )
                    return stats

                if not state.proofs:
                    print("⚠️  No valid proofs found - skipping consolidation")
                    return stats

                print(
                    f"🔄 Consolidating {len(state.proofs)} proofs into fresh events..."
                )

                # Create fresh consolidated events
                new_event_ids = []
                # Group proofs by mint
                proofs_by_mint: dict[str, list] = {}
                for proof in state.proofs:
                    mint_url = proof.get("mint", "unknown")
                    if mint_url not in proofs_by_mint:
                        proofs_by_mint[mint_url] = []
                    proofs_by_mint[mint_url].append(proof)

                for mint_url, mint_proofs in proofs_by_mint.items():
                    try:
                        new_id = await wallet.event_manager.publish_token_event(
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
                            await wallet.event_manager.delete_token_event(event_id)
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
                        # For consolidation, we use "sat" as default unit since it's a net-zero operation
                        await wallet.event_manager.publish_spending_history(
                            direction="in",  # Consolidation is like receiving all proofs again
                            amount=0,  # No net change in balance
                            unit="sat",  # Default unit for consolidation
                            created_token_ids=new_event_ids,
                            destroyed_token_ids=events_to_cleanup,
                        )
                        print("   📋 Created consolidation history")
                    except Exception as e:
                        print(f"   ⚠️  Could not create history: {e}")

                print(
                    f"🎉 Cleanup complete! Consolidated {stats['events_consolidated']} events"
                )

                # Show results
                console.print("\n[green]📋 Cleanup Results:[/green]")
                console.print(f"   Total events analyzed: {stats['total_events']}")
                console.print(f"   Valid events: {stats['valid_events']}")
                console.print(
                    f"   Undecryptable events: {stats['undecryptable_events']}"
                )
                console.print(f"   Empty events: {stats['empty_events']}")
                console.print(f"   Valid proofs: {stats['valid_proofs']}")

                # Show balance by currency
                console.print("   Balance by currency:")
                for currency, balance in sorted(stats["balance_by_unit"].items()):
                    if currency in [
                        "usd",
                        "eur",
                        "gbp",
                        "cad",
                        "chf",
                        "aud",
                        "jpy",
                        "cny",
                        "inr",
                        "usdt",
                        "usdc",
                        "dai",
                    ]:
                        display_balance = balance / 100
                        console.print(
                            f"     {currency.upper()}: {display_balance:.2f} {currency}"
                        )
                    else:
                        console.print(f"     {currency.upper()}: {balance} {currency}")

                if not dry_run:
                    console.print(
                        f"   Events consolidated: {stats['events_consolidated']}"
                    )
                    console.print(
                        f"   Events marked superseded: {stats['events_marked_superseded']}"
                    )

                    if stats["events_consolidated"] > 0:
                        console.print(
                            "\n[green]🎉 Successfully consolidated wallet state![/green]"
                        )
                        console.print(
                            "[cyan]Your wallet should now have cleaner state and faster syncing.[/cyan]"
                        )
                    else:
                        console.print(
                            "\n[green]✅ No cleanup needed - wallet state is already optimized.[/green]"
                        )
                else:
                    console.print(
                        "\n[cyan]ℹ️  This was a dry run - no changes were made.[/cyan]"
                    )
                    if stats["undecryptable_events"] + stats["empty_events"] >= 5:
                        console.print(
                            "[yellow]Run without --dry-run to perform actual cleanup.[/yellow]"
                        )

        except Exception as e:
            handle_wallet_error(e)

    asyncio.run(_cleanup())


@app.command()
def backup(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    list_backups: Annotated[
        bool, typer.Option("--list", "-l", help="List all backup files")
    ] = False,
    scan: Annotated[
        bool, typer.Option("--scan", "-s", help="Scan for proofs missing from Nostr")
    ] = False,
    recover: Annotated[
        bool, typer.Option("--recover", "-r", help="Recover missing proofs to Nostr")
    ] = False,
    clean: Annotated[
        bool, typer.Option("--clean", "-c", help="Clean up verified backup files")
    ] = False,
    confirm: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Manage local proof backups and recovery.

    Local backups ensure proofs are never lost due to relay issues.
    Use --scan to check for missing proofs and --recover to restore them.
    """

    async def _backup():
        nsec = get_nsec()
        async with await create_wallet_with_mint_selection(
            nsec, mint_urls=mint_urls
        ) as wallet:
            from pathlib import Path

            backup_dir = Path.cwd() / "proof_backups"

            if list_backups:
                console.print("\n[cyan]📁 Local Backup Files[/cyan]")

                if not backup_dir.exists():
                    console.print("[yellow]No backup directory found[/yellow]")
                    return

                backup_files = list(backup_dir.glob("proofs_*.json"))
                if not backup_files:
                    console.print("[yellow]No backup files found[/yellow]")
                    return

                total_size = 0
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("File", style="green")
                table.add_column("Size", style="blue")
                table.add_column("Modified", style="yellow")
                table.add_column("Proofs", style="magenta")

                for bf in sorted(
                    backup_files, key=lambda x: x.stat().st_mtime, reverse=True
                ):
                    size = bf.stat().st_size
                    total_size += size
                    mtime = time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(bf.stat().st_mtime)
                    )

                    # Try to read proof count
                    try:
                        import json

                        with open(bf, "r") as f:
                            data = json.load(f)
                        proof_count = len(data.get("proofs", []))
                    except Exception:
                        proof_count = "?"

                    table.add_row(bf.name, f"{size:,} bytes", mtime, str(proof_count))

                console.print(table)
                console.print(
                    f"\n[blue]Total: {len(backup_files)} files, {total_size:,} bytes[/blue]"
                )

            elif scan or recover:
                console.print("\n[cyan]🔍 Scanning local proof backups...[/cyan]")

                if not backup_dir.exists():
                    console.print("[yellow]No backup directory found[/yellow]")
                    console.print(
                        "[dim]Backups are created automatically when storing proofs[/dim]"
                    )
                    return

                # Scan for missing proofs
                stats = await wallet.scan_and_recover_local_proofs(auto_publish=False)

                console.print("\n[cyan]📊 Backup Scan Results[/cyan]")
                console.print(
                    f"Total backup files: [blue]{stats['total_backup_files']}[/blue]"
                )
                console.print(
                    f"Total proofs in backups: [blue]{stats['total_proofs_in_backups']}[/blue]"
                )
                console.print(
                    f"Missing from Nostr: [yellow]{stats['missing_from_nostr']}[/yellow]"
                )

                if stats["missing_from_nostr"] == 0:
                    console.print(
                        "\n[green]✅ All proofs are already backed up on Nostr![/green]"
                    )
                    return

                if scan and not recover:
                    console.print(
                        f"\n[yellow]ℹ️  Found {stats['missing_from_nostr']} missing proofs[/yellow]"
                    )
                    console.print("[dim]Use --recover to restore them to Nostr[/dim]")
                    return

                # Recover missing proofs
                if recover:
                    if not confirm:
                        console.print(
                            f"\n[yellow]⚠️  Found {stats['missing_from_nostr']} proofs not on Nostr[/yellow]"
                        )
                        if not typer.confirm("Recover these proofs?"):
                            console.print("[yellow]Recovery cancelled[/yellow]")
                            return

                    console.print("\n[blue]🚀 Recovering missing proofs...[/blue]")
                    recovery_stats = await wallet.scan_and_recover_local_proofs(
                        auto_publish=True
                    )

                    console.print("\n[cyan]📊 Recovery Results[/cyan]")
                    console.print(
                        f"Recovered: [green]{recovery_stats['recovered']}[/green] proofs"
                    )
                    console.print(
                        f"Failed: [red]{recovery_stats['failed']}[/red] proofs"
                    )

                    if recovery_stats["recovered"] > 0:
                        console.print("\n[green]✅ Recovery successful![/green]")

                        # Check new balance
                        state = await wallet.fetch_wallet_state(check_proofs=False)
                        console.print("\n💰 Current balance:")
                        for currency, balance in sorted(state.balance_by_unit.items()):
                            if currency in [
                                "usd",
                                "eur",
                                "gbp",
                                "cad",
                                "chf",
                                "aud",
                                "jpy",
                                "cny",
                                "inr",
                                "usdt",
                                "usdc",
                                "dai",
                            ]:
                                display_balance = balance / 100
                                console.print(
                                    f"   {currency.upper()}: [green]{display_balance:.2f} {currency}[/green]"
                                )
                            else:
                                console.print(
                                    f"   {currency.upper()}: [green]{balance} {currency}[/green]"
                                )
                    else:
                        console.print("\n[red]❌ No proofs were recovered[/red]")

            elif clean:
                console.print("\n[cyan]🧹 Cleaning up verified backups...[/cyan]")

                if not backup_dir.exists():
                    console.print("[yellow]No backup directory found[/yellow]")
                    return

                backup_files = list(backup_dir.glob("proofs_*.json"))
                if not backup_files:
                    console.print("[yellow]No backup files to clean[/yellow]")
                    return

                console.print(f"Found [blue]{len(backup_files)}[/blue] backup file(s)")

                if not confirm:
                    console.print(
                        "\n[yellow]⚠️  This will delete backup files that are verified on Nostr[/yellow]"
                    )
                    if not typer.confirm("Continue with cleanup?"):
                        console.print("[yellow]Cleanup cancelled[/yellow]")
                        return

                # Run recovery which will clean up verified backups
                console.print("\n[blue]Verifying and cleaning up backups...[/blue]")
                stats = await wallet.scan_and_recover_local_proofs(auto_publish=True)

                # Check remaining backups
                remaining = list(backup_dir.glob("proofs_*.json"))
                cleaned = len(backup_files) - len(remaining)

                if cleaned > 0:
                    console.print(
                        f"\n[green]✅ Cleaned up {cleaned} backup file(s)[/green]"
                    )
                else:
                    console.print(
                        "\n[yellow]ℹ️  No backups were cleaned (may still be needed)[/yellow]"
                    )

                if remaining:
                    console.print(f"[blue]Remaining backups: {len(remaining)}[/blue]")
            else:
                # Default: show backup status
                console.print("\n[cyan]📁 Local Backup Status[/cyan]")

                if backup_dir.exists():
                    backup_files = list(backup_dir.glob("proofs_*.json"))
                    if backup_files:
                        console.print(f"Backup files: [blue]{len(backup_files)}[/blue]")
                        total_size = sum(bf.stat().st_size for bf in backup_files)
                        console.print(f"Total size: [blue]{total_size:,} bytes[/blue]")

                        # Quick scan to check if any might be missing
                        console.print("\n[dim]Checking for missing proofs...[/dim]")
                        stats = await wallet.scan_and_recover_local_proofs(
                            auto_publish=False
                        )

                        if stats["missing_from_nostr"] > 0:
                            console.print(
                                f"\n[yellow]⚠️  {stats['missing_from_nostr']} proofs not on Nostr![/yellow]"
                            )
                            console.print(
                                "[dim]Use 'nuts backup --recover' to restore them[/dim]"
                            )
                        else:
                            console.print(
                                "\n[green]✅ All proofs are backed up on Nostr[/green]"
                            )
                    else:
                        console.print(
                            "[green]No backup files (all proofs are on Nostr)[/green]"
                        )
                else:
                    console.print(
                        "[green]No backup directory (no backups needed yet)[/green]"
                    )

                console.print("\n[dim]Commands:[/dim]")
                console.print("  nuts backup --list     # List all backup files")
                console.print("  nuts backup --scan     # Scan for missing proofs")
                console.print("  nuts backup --recover  # Recover missing proofs")
                console.print("  nuts backup --clean    # Clean up verified backups")

    try:
        asyncio.run(_backup())
    except KeyboardInterrupt:
        console.print("\n[yellow]Backup operation cancelled[/yellow]")
        raise typer.Exit()
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def history(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="Maximum number of entries to show")
    ] = 20,
) -> None:
    """Show spending history from encrypted Nostr events."""

    async def _history():
        try:
            nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet = await create_wallet_with_mint_selection(
                nsec=nsec, mint_urls=mint_urls
            )
            async with wallet:
                console.print("🔄 Fetching spending history...")

                # Fetch history from event manager
                history_entries = await wallet.event_manager.fetch_spending_history()

                if not history_entries:
                    console.print("[yellow]ℹ️ No spending history found[/yellow]")
                    return

                # Show summary
                console.print(
                    f"\n[cyan]📊 Spending History ({len(history_entries)} entries)[/cyan]"
                )

                # Create table
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Date", style="dim")
                table.add_column("Direction", justify="center")
                table.add_column("Amount", justify="right", style="green")
                table.add_column("Event ID", style="dim")

                # Show entries (limited)
                for entry in history_entries[:limit]:
                    from datetime import datetime

                    timestamp = entry.get("timestamp", 0)
                    date_str = datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M"
                    )

                    direction = entry.get("direction", "unknown")
                    direction_emoji = (
                        "📥"
                        if direction == "in"
                        else "📤"
                        if direction == "out"
                        else "❓"
                    )
                    direction_display = f"{direction_emoji} {direction}"

                    amount = entry.get("amount", "0")
                    unit = entry.get("unit", "sat")
                    amount_display = f"{amount} {unit}"

                    event_id = entry.get("event_id", "unknown")
                    event_short = (
                        event_id[:16] + "..." if len(event_id) > 16 else event_id
                    )

                    table.add_row(
                        date_str, direction_display, amount_display, event_short
                    )

                console.print(table)

                if len(history_entries) > limit:
                    console.print(
                        f"\n[dim]Showing {limit} of {len(history_entries)} entries. Use --limit to show more.[/dim]"
                    )

        except Exception as e:
            handle_wallet_error(e)

    asyncio.run(_history())


@app.command()
def debug(
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    history: Annotated[
        bool, typer.Option("--history", help="Debug history decryption issues")
    ] = False,
    balance: Annotated[
        bool, typer.Option("--balance", help="Debug balance and proof validation")
    ] = False,
    nostr: Annotated[
        bool, typer.Option("--nostr", help="Debug Nostr relay connectivity and events")
    ] = False,
    proofs: Annotated[
        bool, typer.Option("--proofs", help="Debug proof state and validation")
    ] = False,
    wallet: Annotated[
        bool, typer.Option("--wallet", help="Debug wallet configuration and keys")
    ] = False,
) -> None:
    """Debug wallet functionality and connectivity issues.

    Run without flags to debug everything, or use specific flags to debug particular areas.
    """

    async def _debug():
        try:
            # If no specific flags, debug everything
            debug_all = not any([history, balance, nostr, proofs, wallet])

            nsec = get_nsec()
            # Use create_wallet_with_mint_selection for automatic mint discovery and selection
            wallet_obj = await create_wallet_with_mint_selection(
                nsec=nsec, mint_urls=mint_urls
            )
            async with wallet_obj:
                console.print("🔍 [cyan]Wallet Debug Report[/cyan]")
                console.print("=" * 60)

                # Debug wallet configuration and keys
                if wallet or debug_all:
                    await _debug_wallet_config(wallet_obj)

                # Debug Nostr relay connectivity
                if nostr or debug_all:
                    await _debug_nostr_relays(wallet_obj)

                # Debug balance and proof validation
                if balance or debug_all:
                    await _debug_balance_proofs(wallet_obj)

                # Debug proof state specifically
                if proofs or debug_all:
                    await _debug_proof_state(wallet_obj)

                # Debug history decryption
                if history or debug_all:
                    await _debug_history_decryption(wallet_obj)

        except Exception as e:
            handle_wallet_error(e)

    async def _debug_wallet_config(wallet_obj: Wallet) -> None:
        """Debug wallet configuration and keys."""
        console.print("\n[yellow]🗂️  Wallet Configuration[/yellow]")
        console.print(f"  Nostr Public Key: {wallet_obj._get_pubkey()}")
        if wallet_obj.wallet_privkey:
            console.print(
                f"  Wallet Private Key: {wallet_obj.wallet_privkey[:8]}...{wallet_obj.wallet_privkey[-8:]}"
            )
        else:
            console.print(
                "  Wallet Private Key: [yellow]Not loaded (no wallet event)[/yellow]"
            )
        console.print(f"  Configured Mints: {len(wallet_obj.mint_urls)}")
        for i, mint_url in enumerate(wallet_obj.mint_urls):
            console.print(f"    {i + 1}. {mint_url}")

        # Check wallet events
        # Note: check_wallet_event_exists is no longer available in refactored wallet
        # We'll fetch events directly instead
        pubkey = wallet_obj._get_pubkey()
        all_events = await wallet_obj.relay_manager.fetch_wallet_events(pubkey)
        wallet_events = [e for e in all_events if e["kind"] == EventKind.Wallet]

        exists = len(wallet_events) > 0
        current_event = (
            max(wallet_events, key=lambda e: e["created_at"]) if wallet_events else None
        )

        if exists and current_event:
            from datetime import datetime

            created_time = datetime.fromtimestamp(current_event["created_at"])
            console.print(
                f"  Current Wallet Event: {current_event['id'][:16]}... (created {created_time})"
            )

            # Check for multiple wallet events
            relays = await wallet_obj.relay_manager.get_relay_connections()
            pubkey = wallet_obj._get_pubkey()

            all_events = []
            event_ids_seen = set()

            for relay in relays:
                try:
                    events = await relay.fetch_wallet_events(pubkey)
                    for event in events:
                        if event["id"] not in event_ids_seen:
                            all_events.append(event)
                            event_ids_seen.add(event["id"])
                except Exception:
                    continue

            wallet_events = [e for e in all_events if e["kind"] == 17375]
            if len(wallet_events) > 1:
                console.print(
                    f"  [yellow]⚠️  Found {len(wallet_events)} wallet events (should be 1)[/yellow]"
                )
                for i, event in enumerate(wallet_events):
                    created_time = datetime.fromtimestamp(event["created_at"])
                    is_current = event["id"] == current_event["id"]
                    status = "CURRENT" if is_current else "OLD"
                    console.print(
                        f"    {i + 1}. {event['id'][:16]}... ({status}, created {created_time})"
                    )
        else:
            console.print("  [red]❌ No wallet event found[/red]")

    async def _debug_nostr_relays(wallet_obj: Wallet) -> None:
        """Debug Nostr relay connectivity and events."""
        console.print("\n[yellow]🌐 Nostr Relay Status[/yellow]")
        console.print(f"  Configured Relays: {len(wallet_obj.relay_urls)}")
        for i, relay in enumerate(wallet_obj.relay_urls):
            console.print(f"    {i + 1}. {relay}")

        # Check relay connectivity
        try:
            relay_connections = await wallet_obj.relay_manager.get_relay_connections()
            console.print(f"  Connected Relays: {len(relay_connections)}")

            # Show relay pool status if using queued relays
            if (
                wallet_obj.relay_manager.use_queued_relays
                and wallet_obj.relay_manager.relay_pool
            ):
                console.print("  Using Relay Pool: ✅")
                console.print(
                    f"  Pool Size: {len(wallet_obj.relay_manager.relay_pool.relays)}"
                )
                for i, pool_relay in enumerate(
                    wallet_obj.relay_manager.relay_pool.relays
                ):
                    status = (
                        "🟢 Connected"
                        if hasattr(pool_relay, "ws")
                        and pool_relay.ws
                        and pool_relay.ws.close_code is None
                        else "🔴 Disconnected"
                    )
                    console.print(f"    {i + 1}. {pool_relay.url} - {status}")
            else:
                console.print("  Using Individual Relays: ✅")
                for i, individual_relay in enumerate(relay_connections):
                    status = (
                        "🟢 Connected"
                        if hasattr(individual_relay, "ws")
                        and individual_relay.ws
                        and individual_relay.ws.close_code is None
                        else "🔴 Disconnected"
                    )
                    console.print(f"    {i + 1}. {individual_relay.url} - {status}")
        except Exception as e:
            console.print(f"  ❌ Relay connection error: {e}")

        # Event counts by relay
        relays = await wallet_obj.relay_manager.get_relay_connections()
        pubkey = wallet_obj._get_pubkey()

        console.print("\n  Event Counts by Relay:")
        for relay_conn in relays:
            try:
                events = await relay_conn.fetch_wallet_events(pubkey)
                wallet_events = [e for e in events if e["kind"] == 17375]
                token_events = [e for e in events if e["kind"] == 7375]
                history_events = [e for e in events if e["kind"] == 7376]

                total = len(wallet_events) + len(token_events) + len(history_events)
                console.print(
                    f"    {relay_conn.url}: {total} events (W:{len(wallet_events)} T:{len(token_events)} H:{len(history_events)})"
                )
            except Exception as e:
                console.print(f"    {relay_conn.url}: ❌ Error: {e}")

    async def _debug_balance_proofs(wallet_obj):
        """Debug balance calculation and proof validation."""
        console.print("\n[yellow]💰 Balance & Proof Validation[/yellow]")

        try:
            # Get balance without validation first (faster)
            state_unvalidated = await wallet_obj.fetch_wallet_state(check_proofs=False)
            console.print(f"  Raw Proof Count: {len(state_unvalidated.proofs)}")

            # Show raw balance by currency
            console.print("  Raw Balance by Currency (unvalidated):")
            for currency, balance in sorted(state_unvalidated.balance_by_unit.items()):
                if currency in [
                    "usd",
                    "eur",
                    "gbp",
                    "cad",
                    "chf",
                    "aud",
                    "jpy",
                    "cny",
                    "inr",
                    "usdt",
                    "usdc",
                    "dai",
                ]:
                    display_balance = balance / 100
                    console.print(
                        f"    {currency.upper()}: {display_balance:.2f} {currency}"
                    )
                else:
                    console.print(f"    {currency.upper()}: {balance} {currency}")

            # Get balance with validation (slower but accurate)
            console.print("\n  Validating proofs with mints...")
            state_validated = await wallet_obj.fetch_wallet_state(check_proofs=True)
            console.print(f"  Valid Proof Count: {len(state_validated.proofs)}")

            # Show validated balance by currency
            console.print("  Validated Balance by Currency:")
            for currency, balance in sorted(state_validated.balance_by_unit.items()):
                if currency in [
                    "usd",
                    "eur",
                    "gbp",
                    "cad",
                    "chf",
                    "aud",
                    "jpy",
                    "cny",
                    "inr",
                    "usdt",
                    "usdc",
                    "dai",
                ]:
                    display_balance = balance / 100
                    console.print(
                        f"    {currency.upper()}: {display_balance:.2f} {currency}"
                    )
                else:
                    console.print(f"    {currency.upper()}: {balance} {currency}")

            # Show difference if any
            proof_diff = len(state_unvalidated.proofs) - len(state_validated.proofs)

            if proof_diff > 0:
                console.print("\n  [red]⚠️  Found spent/invalid proofs:[/red]")
                console.print(f"    Invalid Proofs: {proof_diff}")

                # Calculate balance differences by currency
                console.print("    Lost Balance by Currency:")
                all_currencies = set(state_unvalidated.balance_by_unit.keys())
                for currency in sorted(all_currencies):
                    unval_balance = state_unvalidated.balance_by_unit.get(currency, 0)
                    val_balance = state_validated.balance_by_unit.get(currency, 0)
                    diff = unval_balance - val_balance

                    if diff > 0:
                        if currency in [
                            "usd",
                            "eur",
                            "gbp",
                            "cad",
                            "chf",
                            "aud",
                            "jpy",
                            "cny",
                            "inr",
                            "usdt",
                            "usdc",
                            "dai",
                        ]:
                            display_diff = diff / 100
                            console.print(
                                f"      {currency.upper()}: {display_diff:.2f} {currency}"
                            )
                        else:
                            console.print(
                                f"      {currency.upper()}: {diff} {currency}"
                            )
            else:
                console.print("\n  [green]✅ All proofs valid[/green]")

            # Show proof breakdown by mint and currency
            if state_validated.proofs:
                console.print("\n  Proof Breakdown by Mint and Currency:")

                # Group proofs by mint and currency
                proofs_by_mint_currency: dict[str, dict[str, list]] = {}
                for proof in state_validated.proofs:
                    mint_url = proof.get("mint", "unknown")
                    currency = proof.get("unit", "sat")

                    if mint_url not in proofs_by_mint_currency:
                        proofs_by_mint_currency[mint_url] = {}
                    if currency not in proofs_by_mint_currency[mint_url]:
                        proofs_by_mint_currency[mint_url][currency] = []
                    proofs_by_mint_currency[mint_url][currency].append(proof)

                for mint_url, currency_proofs in proofs_by_mint_currency.items():
                    # Show mint URL (truncated if too long)
                    mint_display = (
                        mint_url[:50] + "..." if len(mint_url) > 53 else mint_url
                    )
                    console.print(f"    {mint_display}:")

                    for currency, proofs in sorted(currency_proofs.items()):
                        balance = sum(p["amount"] for p in proofs)

                        # Group by denomination
                        denominations = {}
                        for proof in proofs:
                            amount = proof["amount"]
                            denominations[amount] = denominations.get(amount, 0) + 1

                        denom_str = ", ".join(
                            f"{amount}×{count}"
                            for amount, count in sorted(denominations.items())
                        )

                        # Format display based on currency type
                        if currency in [
                            "usd",
                            "eur",
                            "gbp",
                            "cad",
                            "chf",
                            "aud",
                            "jpy",
                            "cny",
                            "inr",
                            "usdt",
                            "usdc",
                            "dai",
                        ]:
                            # For fiat/stablecoins, show as dollars/euros with 2 decimal places
                            display_balance = balance / 100
                            console.print(
                                f"      {currency.upper()}: {display_balance:.2f} {currency} ({len(proofs)} proofs: {denom_str})"
                            )
                        else:
                            # For crypto currencies, show as is
                            console.print(
                                f"      {currency.upper()}: {balance} {currency} ({len(proofs)} proofs: {denom_str})"
                            )

        except Exception as e:
            console.print(f"  ❌ Balance validation error: {e}")

    async def _debug_proof_state(wallet_obj):
        """Debug proof state and validation details."""
        console.print("\n[yellow]🔐 Proof State Details[/yellow]")

        try:
            state = await wallet_obj.fetch_wallet_state(check_proofs=False)
            if not state.proofs:
                console.print("  No proofs found")
                return

            # Show cache status
            console.print(
                f"  Proof State Cache: {len(wallet_obj._proof_state_cache)} entries"
            )
            console.print(
                f"  Known Spent Proofs: {len(wallet_obj._known_spent_proofs)} proofs"
            )

            # Sample a few proofs for detailed analysis
            sample_proofs = state.proofs[:5]  # First 5 proofs
            console.print(
                f"\n  Sample Proof Analysis (showing {len(sample_proofs)}/{len(state.proofs)}):"
            )

            for i, proof in enumerate(sample_proofs):
                proof_id = f"{proof['secret']}:{proof['C']}"
                mint_url = proof.get("mint", "unknown")
                currency = proof.get("unit", "sat")

                # Check cache status
                is_cached, cached_state = wallet_obj._is_proof_state_cached(proof_id)
                cache_status = f"cached ({cached_state})" if is_cached else "not cached"

                # Format amount display based on currency
                if currency in [
                    "usd",
                    "eur",
                    "gbp",
                    "cad",
                    "chf",
                    "aud",
                    "jpy",
                    "cny",
                    "inr",
                    "usdt",
                    "usdc",
                    "dai",
                ]:
                    display_amount = proof["amount"] / 100
                    amount_str = f"{display_amount:.2f} {currency}"
                else:
                    amount_str = f"{proof['amount']} {currency}"

                console.print(f"    {i + 1}. {amount_str} from {mint_url[:30]}...")
                console.print(f"       ID: {proof['id'][:16]}...")
                console.print(f"       Secret: {proof['secret'][:16]}...")
                console.print(f"       Cache: {cache_status}")

        except Exception as e:
            console.print(f"  ❌ Proof state error: {e}")

    async def _debug_history_decryption(wallet_obj: Wallet) -> None:
        """Debug history decryption issues."""
        import json

        console.print("\n[yellow]📊 History Decryption Analysis[/yellow]")

        try:
            # Get all wallet events to find different keys
            relays = await wallet_obj.relay_manager.get_relay_connections()
            pubkey = wallet_obj._get_pubkey()

            all_events = []
            event_ids_seen = set()

            for relay in relays:
                try:
                    events = await relay.fetch_wallet_events(pubkey)
                    for event in events:
                        if event["id"] not in event_ids_seen:
                            all_events.append(event)
                            event_ids_seen.add(event["id"])
                except Exception:
                    continue

            # Find unique wallet private keys
            wallet_events = [e for e in all_events if e["kind"] == 17375]
            wallet_keys = set()

            for event in wallet_events:
                try:
                    decrypted = nip44_decrypt(event["content"], wallet_obj._privkey)
                    wallet_data = json.loads(decrypted)

                    for item in wallet_data:
                        if item[0] == "privkey":
                            wallet_keys.add(item[1])
                            break
                except Exception:
                    continue

            console.print(f"  Unique Private Keys Found: {len(wallet_keys)}")
            if wallet_obj.wallet_privkey:
                console.print(
                    f"  Current Key: {wallet_obj.wallet_privkey[:8]}...{wallet_obj.wallet_privkey[-8:]}"
                )
            else:
                console.print("  Current Key: [yellow]Not loaded[/yellow]")

            # Test history decryption
            history_events = [e for e in all_events if e["kind"] == 7376]
            console.print(f"  Total History Events: {len(history_events)}")

            if history_events:
                # Test first few events
                sample_size = min(5, len(history_events))
                console.print(f"  Testing decryption on {sample_size} recent events:")

                success_count = 0
                for i, event in enumerate(history_events[:sample_size]):
                    try:
                        decrypted = nip44_decrypt(event["content"], wallet_obj._privkey)
                        history_data = json.loads(decrypted)
                        direction = next(
                            (
                                item[1]
                                for item in history_data
                                if item[0] == "direction"
                            ),
                            "unknown",
                        )
                        amount = next(
                            (item[1] for item in history_data if item[0] == "amount"),
                            "unknown",
                        )
                        unit = next(
                            (item[1] for item in history_data if item[0] == "unit"),
                            "sat",
                        )
                        console.print(
                            f"    {i + 1}. ✅ Success: {direction} {amount} {unit}"
                        )
                        success_count += 1
                    except Exception as e:
                        console.print(f"    {i + 1}. ❌ Failed: {str(e)[:50]}...")

                success_rate = (success_count / sample_size) * 100
                console.print(
                    f"  Decryption Success Rate: {success_rate:.1f}% ({success_count}/{sample_size})"
                )

                if success_rate < 100 and len(wallet_keys) > 1:
                    console.print(
                        "  [yellow]💡 Tip: Multiple keys detected. Some history may be from old keys.[/yellow]"
                    )

        except Exception as e:
            console.print(f"  ❌ History analysis error: {e}")

    asyncio.run(_debug())


def cli() -> None:
    """Entry point for the CLI."""
    app()


@app.command()
def swap(
    amount: Annotated[int, typer.Argument(help="Amount to swap")],
    from_unit: Annotated[
        str, typer.Argument(help="Source currency (sat, usd, eur, etc.)")
    ],
    to_unit: Annotated[
        str, typer.Argument(help="Target currency (sat, usd, eur, etc.)")
    ],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    same_mint: Annotated[
        bool,
        typer.Option(
            "--same-mint",
            help="Require swap within same mint (default: prefer same mint)",
        ),
    ] = False,
    confirm: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Exchange tokens between different currencies/keysets.

    This command allows you to swap tokens from one currency to another,
    either within the same mint (if it supports both currencies) or
    between different mints.

    Examples:
        Swap 100 USD to EUR: nuts swap 100 usd eur
        Swap 1000 sats to USD: nuts swap 1000 sat usd
        Force same mint: nuts swap 50 eur sat --same-mint
    """

    async def _swap():
        console.print(
            "[yellow]⚠️  The swap command is temporarily unavailable after the keyset refactor.[/yellow]"
        )
        console.print(
            "[dim]Currency swaps will be re-implemented in a future update.[/dim]"
        )
        return

    try:
        asyncio.run(_swap())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


if __name__ == "__main__":
    cli()
