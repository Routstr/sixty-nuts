"""Sixty Nuts CLI - NIP-60 Cashu Wallet Implementation."""

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.panel import Panel

try:
    import qrcode  # type: ignore

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from .wallet import Wallet, WalletError, redeem_to_lnurl

app = typer.Typer(
    name="nuts",
    help="Sixty Nuts - NIP-60 Cashu Wallet CLI",
    rich_markup_mode="markdown",
)
console = Console()

# Environment variable for NSEC
NSEC_ENV_VAR = "SIXTY_NUTS_NSEC"
NSEC_FILE = Path.home() / ".sixty_nuts_nsec"


def get_nsec() -> str:
    """Get NSEC from environment, file, or prompt user."""
    # Try environment variable first
    nsec = os.getenv(NSEC_ENV_VAR)
    if nsec:
        return nsec

    # Try local file
    if NSEC_FILE.exists():
        nsec = NSEC_FILE.read_text().strip()
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
        "Store NSEC for future use?", choices=["env", "file", "no"], default="no"
    )

    if store_choice == "env":
        console.print("\n[green]Add this to your shell profile:[/green]")
        console.print(f"export {NSEC_ENV_VAR}={nsec}")
        console.print("\nOr run:")
        console.print(f"echo 'export {NSEC_ENV_VAR}={nsec}' >> ~/.bashrc")
    elif store_choice == "file":
        NSEC_FILE.write_text(nsec)
        NSEC_FILE.chmod(0o600)  # Read only for user
        console.print(f"[green]NSEC stored in {NSEC_FILE}[/green]")

    return nsec


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


async def _debug_nostr_state(wallet) -> None:
    """Debug Nostr relay state and proof storage."""
    from datetime import datetime
    import json

    console.print("\n[cyan]🔍 Nostr Relay Debugging[/cyan]")
    console.print("=" * 50)

    # 1. Show wallet configuration
    console.print("\n[yellow]📋 Wallet Configuration:[/yellow]")
    console.print(f"  Public Key: {wallet._get_pubkey()}")
    console.print(f"  Configured Relays: {len(wallet.relays)}")
    for i, relay in enumerate(wallet.relays):
        console.print(f"    {i + 1}. {relay}")

    # 2. Check relay connectivity
    console.print("\n[yellow]🌐 Relay Connectivity:[/yellow]")
    try:
        relay_connections = await wallet._get_relay_connections()
        console.print(f"  Connected Relays: {len(relay_connections)}")

        # Show relay pool status if using queued relays
        if wallet._use_queued_relays and wallet.relay_pool:
            console.print("  Using Relay Pool: ✅")
            console.print(f"  Pool Size: {len(wallet.relay_pool.relays)}")
            for i, relay in enumerate(wallet.relay_pool.relays):
                status = (
                    "🟢 Connected"
                    if hasattr(relay, "ws") and relay.ws and relay.ws.close_code is None
                    else "🔴 Disconnected"
                )
                console.print(f"    {i + 1}. {relay.url} - {status}")
        else:
            console.print("  Using Individual Relays: ✅")
            for i, relay in enumerate(relay_connections):
                status = (
                    "🟢 Connected"
                    if hasattr(relay, "ws") and relay.ws and relay.ws.close_code is None
                    else "🔴 Disconnected"
                )
                console.print(f"    {i + 1}. {relay.url} - {status}")

    except Exception as e:
        console.print(f"  ❌ Relay connection error: {e}")

    # 3. Fetch raw events from relays
    console.print("\n[yellow]📡 Raw Nostr Events:[/yellow]")
    try:
        # Get relay connections
        relays = await wallet._get_relay_connections()
        all_events = []

        for relay in relays:
            try:
                console.print(f"\n  Fetching from {relay.url}...")

                # Fetch wallet events using the correct API
                wallet_events = await relay.fetch_events(
                    [
                        {
                            "authors": [wallet._get_pubkey()],
                            "kinds": [17375],  # Wallet metadata
                            "limit": 5,
                        }
                    ]
                )

                # Fetch token events
                token_events = await relay.fetch_events(
                    [
                        {
                            "authors": [wallet._get_pubkey()],
                            "kinds": [7375],  # Token events
                            "limit": 20,
                        }
                    ]
                )

                # Fetch history events
                history_events = await relay.fetch_events(
                    [
                        {
                            "authors": [wallet._get_pubkey()],
                            "kinds": [7376],  # History events
                            "limit": 10,
                        }
                    ]
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
                console.print(f"    ❌ Error fetching from {relay.url}: {e}")

        # 4. Analyze events
        if all_events:
            console.print("\n[yellow]📊 Event Analysis:[/yellow]")

            # Group by kind
            events_by_kind: dict[str, list] = {}
            for event in all_events:
                kind = event.get("kind", "unknown")
                if kind not in events_by_kind:
                    events_by_kind[kind] = []
                events_by_kind[kind].append(event)

            for kind, events in events_by_kind.items():
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
                                    decrypted = wallet._nip44_decrypt(content)
                                    token_data = json.loads(decrypted)
                                    proofs = token_data.get("proofs", [])
                                    mint_url = token_data.get("mint", "unknown")
                                    total_amount = sum(
                                        p.get("amount", 0) for p in proofs
                                    )
                                    console.print(
                                        f"       → {len(proofs)} proofs, {total_amount} sats from {mint_url}"
                                    )
                                except Exception:
                                    console.print(
                                        f"       → Encrypted content ({len(content)} chars)"
                                    )
                        except Exception as e:
                            console.print(f"       → Parse error: {e}")
        else:
            console.print("  No events found on any relay")

        # 5. Check relay queue status
        if wallet._use_queued_relays and wallet.relay_pool:
            console.print("\n[yellow]📤 Relay Queue Status:[/yellow]")
            try:
                pending_proofs = wallet.relay_pool.get_pending_proofs()
                console.print(f"  Pending Proofs in Queue: {len(pending_proofs)}")

                if pending_proofs:
                    total_pending_sats = 0
                    for token_data in pending_proofs:
                        proofs = token_data.get("proofs", [])
                        mint_url = token_data.get("mint", "unknown")
                        amount = sum(p.get("amount", 0) for p in proofs)
                        total_pending_sats += amount
                        console.print(
                            f"    Mint {mint_url}: {len(proofs)} proofs, {amount} sats"
                        )

                    console.print(f"  Total Pending Value: {total_pending_sats} sats")
                    console.print(
                        "  ⚠️  These sats might be 'missing' until queue is processed!"
                    )

            except Exception as e:
                console.print(f"  ❌ Queue status error: {e}")

        # 6. Compare with local state
        console.print("\n[yellow]🔄 Local vs Relay Comparison:[/yellow]")
        try:
            local_state = await wallet.fetch_wallet_state(check_proofs=False)
            console.print(f"  Local Balance: {local_state.balance} sats")
            console.print(f"  Local Proofs: {len(local_state.proofs)}")

            # Show denomination breakdown
            local_denoms: dict[int, int] = {}
            for proof in local_state.proofs:
                amount = proof.get("amount", 0)
                local_denoms[amount] = local_denoms.get(amount, 0) + 1

            if local_denoms:
                console.print("  Local Denominations:")
                for denom in sorted(local_denoms.keys(), reverse=True):
                    count = local_denoms[denom]
                    console.print(f"    {denom} sats × {count} = {denom * count} sats")

        except Exception as e:
            console.print(f"  ❌ Local state error: {e}")

    except Exception as e:
        console.print(f"❌ Nostr debugging failed: {e}")

    console.print("\n" + "=" * 50)


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
) -> None:
    """Check wallet balance."""

    async def _balance() -> None:
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print("[blue]Checking balance...[/blue]")

            if nostr_debug:
                await _debug_nostr_state(wallet)

            if validate:
                balance_amount = await wallet.get_balance(check_proofs=True)
                console.print(
                    f"[green]✅ Validated Balance: {balance_amount} sats[/green]"
                )
            else:
                balance_amount = await wallet.get_balance(check_proofs=False)
                console.print(
                    f"[yellow]📊 Quick Balance: {balance_amount} sats[/yellow]"
                )
                console.print("[dim](not validated with mint)[/dim]")

            if details:
                state = await wallet.fetch_wallet_state(check_proofs=validate)

                # Create table for mint breakdown
                table = Table(title="Wallet Details")
                table.add_column("Mint", style="cyan")
                table.add_column("Balance", style="green")
                table.add_column("Proofs", style="blue")
                table.add_column("Denominations", style="magenta")

                # Group proofs by mint
                proofs_by_mint: dict[str, list] = {}
                for proof in state.proofs:
                    mint_url = proof.get("mint") or "unknown"
                    if mint_url not in proofs_by_mint:
                        proofs_by_mint[mint_url] = []
                    proofs_by_mint[mint_url].append(proof)

                for mint_url, proofs in proofs_by_mint.items():
                    mint_balance = sum(p["amount"] for p in proofs)
                    # Get denomination breakdown
                    denominations: dict[int, int] = {}
                    for proof in proofs:
                        amount = proof["amount"]
                        denominations[amount] = denominations.get(amount, 0) + 1

                    denom_str = ", ".join(
                        f"{amt}×{count}" for amt, count in sorted(denominations.items())
                    )

                    table.add_row(
                        mint_url[:30] + "..." if len(mint_url) > 33 else mint_url,
                        f"{mint_balance} sats",
                        str(len(proofs)),
                        denom_str,
                    )

                console.print(table)

    try:
        asyncio.run(_balance())
    except Exception as e:
        handle_wallet_error(e)
        raise typer.Exit(1)


@app.command()
def send(
    amount: Annotated[int, typer.Argument(help="Amount to send in sats")],
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
) -> None:
    """Send sats - create a Cashu token or send to Lightning address.

    Create a token:
        nuts send 1000

    Send to Lightning address:
        nuts send --to-lnurl user@getalby.com 1000
    """

    async def _send():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            if to_lnurl:
                # Send directly to Lightning address
                console.print(f"[blue]Sending {amount} sats to {to_lnurl}...[/blue]")

                # Check balance first
                balance = await wallet.get_balance()
                if balance <= amount:
                    console.print(
                        f"[red]Insufficient balance! Need >{amount}, have {balance}[/red]"
                    )
                    console.print(
                        "[dim]Lightning payments require fees (typically 1 sat)[/dim]"
                    )
                    return

                actual_paid = await wallet.send_to_lnurl(to_lnurl, amount)

                console.print("[green]✅ Successfully sent![/green]")
                console.print(f"Total paid (including fees): {actual_paid} sats")

                # Show remaining balance
                balance = await wallet.get_balance()
                console.print(f"Remaining balance: {balance} sats")

            else:
                # Create Cashu token
                console.print(f"[blue]Creating token for {amount} sats...[/blue]")

                # Check balance first
                balance = await wallet.get_balance()
                if balance < amount:
                    console.print(
                        f"[red]Insufficient balance! Need {amount}, have {balance}[/red]"
                    )
                    return

                token = await wallet.send(amount)

                console.print("\n[green]✅ Cashu Token Created:[/green]")
                # Display token without line wrapping for easy copying
                console.print(token, soft_wrap=False, no_wrap=True, overflow="ignore")

                # Display QR code unless disabled
                if not no_qr:
                    display_qr_code(token, f"Cashu Token ({amount} sats)")
                else:
                    console.print()

                # Show remaining balance
                balance = await wallet.get_balance()
                console.print(f"[dim]Remaining balance: {balance} sats[/dim]")

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
            async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
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
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
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
    amount: Annotated[int, typer.Argument(help="Amount to mint in sats")],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", "-t", help="Payment timeout in seconds")
    ] = 300,
    no_qr: Annotated[
        bool, typer.Option("--no-qr", help="Don't display QR code")
    ] = False,
) -> None:
    """Create Lightning invoice to mint new tokens."""

    async def _mint():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print(f"[blue]Creating invoice for {amount} sats...[/blue]")

            invoice, task = await wallet.mint_async(amount, timeout=timeout)

            # Display invoice for easy copying
            console.print("\n[yellow]⚡ Lightning Invoice:[/yellow]")
            # Display invoice without line wrapping for easy copying
            console.print(invoice, soft_wrap=False, no_wrap=True, overflow="ignore")

            # Display QR code unless disabled
            if not no_qr:
                display_qr_code(invoice, "Lightning Invoice QR Code")
            else:
                console.print()

            console.print("[blue]Waiting for payment...[/blue]")

            paid = await task

            if paid:
                console.print("[green]✅ Payment received! Tokens minted.[/green]")
                balance = await wallet.get_balance()
                console.print(f"New balance: {balance} sats")
            else:
                console.print(f"[red]❌ Payment timeout after {timeout} seconds[/red]")

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
    """Show wallet information."""

    async def _info():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print("[blue]Fetching wallet information...[/blue]")

            # Get wallet state
            state = await wallet.fetch_wallet_state(check_proofs=False)

            # Create info table
            table = Table(title="Wallet Information")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")

            # Basic info
            table.add_row("Public Key", wallet._get_pubkey())
            table.add_row("Currency", wallet.currency)
            table.add_row("Balance", f"{state.balance} sats")
            table.add_row("Total Proofs", str(len(state.proofs)))

            # Mint info
            table.add_row("Configured Mints", str(len(wallet.mint_urls)))
            for i, mint_url in enumerate(wallet.mint_urls):
                table.add_row(f"  Mint {i + 1}", mint_url)

            # Relay info
            table.add_row("Configured Relays", str(len(wallet.relays)))
            for i, relay_url in enumerate(wallet.relays):
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
    """Sixty Nuts - NIP-60 Cashu Wallet CLI."""
    pass


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
            async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet_obj:
                # Check if wallet exists
                exists, existing_event = await wallet_obj.check_wallet_event_exists()

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
                        if force:
                            console.print("🔄 Force creating new wallet event...")
                            created = await wallet_obj.initialize_wallet(force=True)
                        else:
                            console.print("🔄 Creating wallet event...")
                            created = await wallet_obj.initialize_wallet(force=False)

                        if created:
                            console.print(
                                "[green]✅ Wallet initialized successfully![/green]"
                            )
                            # Refresh wallet event info
                            (
                                exists,
                                existing_event,
                            ) = await wallet_obj.check_wallet_event_exists()
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
                        content = wallet_obj._nip44_decrypt(existing_event["content"])
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

                        console.print(f"  Relays: {len(wallet_obj.relays)}")
                        for i, relay in enumerate(wallet_obj.relays):
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
                if NSEC_FILE.exists():
                    nsec_existed = True
                    if not confirm:
                        console.print(
                            "\n[yellow]⚠️  Will delete local NSEC storage:[/yellow]"
                        )
                        console.print(f"   🗂️  NSEC file: {NSEC_FILE}")
                        if os.getenv(NSEC_ENV_VAR):
                            console.print(
                                f"   [dim]Note: Environment variable {NSEC_ENV_VAR} will remain set[/dim]"
                            )

                elif not any([clean_wallet, clean_history, clean_tokens]):
                    # Only NSEC requested but no file exists
                    console.print("[yellow]ℹ️ No stored NSEC file found[/yellow]")
                    if os.getenv(NSEC_ENV_VAR):
                        console.print(
                            f"[yellow]Environment variable {NSEC_ENV_VAR} is still set[/yellow]"
                        )
                        console.print("Unset it with: unset SIXTY_NUTS_NSEC")
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

                # Clear NSEC file
                if nsec_existed:
                    NSEC_FILE.unlink()
                    console.print(f"[green]🗑️ Cleared NSEC from {NSEC_FILE}[/green]")
                    if os.getenv(NSEC_ENV_VAR):
                        console.print(
                            f"[yellow]Environment variable {NSEC_ENV_VAR} is still set[/yellow]"
                        )
                        console.print("Unset it with: unset SIXTY_NUTS_NSEC")
                return

            # Get NSEC for wallet operations (this will prompt if needed and file was just deleted)
            wallet_nsec = get_nsec()
            async with Wallet(nsec=wallet_nsec, mint_urls=mint_urls) as wallet_obj:
                console.print("🔄 Scanning for events to erase...")

                # Check what exists
                wallet_exists = False
                history_count = 0
                token_count = 0
                current_balance = 0

                if clean_wallet:
                    exists, _ = await wallet_obj.check_wallet_event_exists()
                    wallet_exists = exists

                if clean_history:
                    history_entries = await wallet_obj.fetch_spending_history()
                    history_count = len(history_entries)

                if clean_tokens:
                    token_count = await wallet_obj.count_token_events()
                    # Get current balance to show user what they're about to lose
                    try:
                        current_balance = await wallet_obj.get_balance(
                            check_proofs=False
                        )
                    except Exception:
                        current_balance = 0

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
                    erase_summary.append(
                        f"💰 {token_count} token storage events [red](containing {current_balance} sats!)[/red]"
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
                            f"\n[red]💀 DANGER: You will lose {current_balance} sats stored on Nostr![/red]"
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
                        confirm_msg = f"\n[red]Type 'DELETE {current_balance} SATS' to confirm token deletion[/red]"
                        expected_response = f"DELETE {current_balance} SATS"
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
                    wallet_deleted = await wallet_obj.delete_all_wallet_events()
                    total_deleted += wallet_deleted
                    console.print(f"   ✅ Deleted {wallet_deleted} wallet event(s)")

                if clean_history and history_count > 0:
                    console.print("🗑️ Deleting transaction history events...")
                    history_deleted = await wallet_obj.clear_spending_history()
                    total_deleted += history_deleted
                    console.print(f"   ✅ Deleted {history_deleted} history event(s)")

                if clean_tokens and token_count > 0:
                    console.print(
                        f"🗑️ Deleting token storage events ({current_balance} sats)..."
                    )
                    tokens_deleted = await wallet_obj.clear_all_token_events()
                    total_deleted += tokens_deleted
                    console.print(
                        f"   💀 Deleted {tokens_deleted} token event(s) containing {current_balance} sats"
                    )

                if clean_nsec and nsec_existed:
                    console.print("🗑️ Deleting local NSEC storage...")
                    NSEC_FILE.unlink()
                    console.print(f"   ✅ Cleared NSEC from {NSEC_FILE}")
                    if os.getenv(NSEC_ENV_VAR):
                        console.print(
                            f"   [yellow]Environment variable {NSEC_ENV_VAR} is still set[/yellow]"
                        )
                        console.print("   Unset it with: unset SIXTY_NUTS_NSEC")

                # Summary
                console.print(
                    f"\n[green]🎉 Erase complete! Deleted {total_deleted} event(s) in optimized batches[/green]"
                )

                if clean_tokens:
                    console.print("\n[red]⚠️  Your Nostr balance is now 0 sats[/red]")
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
            async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
                console.print("🔄 Fetching spending history...")

                # Fetch history
                history_entries = await wallet.fetch_spending_history()

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
                    amount_display = f"{amount} sats"

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
            async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet_obj:
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

    async def _debug_wallet_config(wallet_obj):
        """Debug wallet configuration and keys."""
        console.print("\n[yellow]🗂️  Wallet Configuration[/yellow]")
        console.print(f"  Nostr Public Key: {wallet_obj._get_pubkey()}")
        console.print(
            f"  Wallet Private Key: {wallet_obj.wallet_privkey[:8]}...{wallet_obj.wallet_privkey[-8:]}"
        )
        console.print(f"  Currency: {wallet_obj.currency}")
        console.print(f"  Configured Mints: {len(wallet_obj.mint_urls)}")
        for i, mint_url in enumerate(wallet_obj.mint_urls):
            console.print(f"    {i + 1}. {mint_url}")

        # Check wallet events
        exists, current_event = await wallet_obj.check_wallet_event_exists()
        if exists and current_event:
            from datetime import datetime

            created_time = datetime.fromtimestamp(current_event["created_at"])
            console.print(
                f"  Current Wallet Event: {current_event['id'][:16]}... (created {created_time})"
            )

            # Check for multiple wallet events
            relays = await wallet_obj._get_relay_connections()
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

    async def _debug_nostr_relays(wallet_obj):
        """Debug Nostr relay connectivity and events."""
        console.print("\n[yellow]🌐 Nostr Relay Status[/yellow]")
        console.print(f"  Configured Relays: {len(wallet_obj.relays)}")
        for i, relay in enumerate(wallet_obj.relays):
            console.print(f"    {i + 1}. {relay}")

        # Check relay connectivity
        try:
            relay_connections = await wallet_obj._get_relay_connections()
            console.print(f"  Connected Relays: {len(relay_connections)}")

            # Show relay pool status if using queued relays
            if wallet_obj._use_queued_relays and wallet_obj.relay_pool:
                console.print("  Using Relay Pool: ✅")
                console.print(f"  Pool Size: {len(wallet_obj.relay_pool.relays)}")
                for i, relay in enumerate(wallet_obj.relay_pool.relays):
                    status = (
                        "🟢 Connected"
                        if hasattr(relay, "ws")
                        and relay.ws
                        and relay.ws.close_code is None
                        else "🔴 Disconnected"
                    )
                    console.print(f"    {i + 1}. {relay.url} - {status}")
            else:
                console.print("  Using Individual Relays: ✅")
                for i, relay in enumerate(relay_connections):
                    status = (
                        "🟢 Connected"
                        if hasattr(relay, "ws")
                        and relay.ws
                        and relay.ws.close_code is None
                        else "🔴 Disconnected"
                    )
                    console.print(f"    {i + 1}. {relay.url} - {status}")
        except Exception as e:
            console.print(f"  ❌ Relay connection error: {e}")

        # Event counts by relay
        relays = await wallet_obj._get_relay_connections()
        pubkey = wallet_obj._get_pubkey()

        console.print("\n  Event Counts by Relay:")
        for relay in relays:
            try:
                events = await relay.fetch_wallet_events(pubkey)
                wallet_events = [e for e in events if e["kind"] == 17375]
                token_events = [e for e in events if e["kind"] == 7375]
                history_events = [e for e in events if e["kind"] == 7376]

                total = len(wallet_events) + len(token_events) + len(history_events)
                console.print(
                    f"    {relay.url}: {total} events (W:{len(wallet_events)} T:{len(token_events)} H:{len(history_events)})"
                )
            except Exception as e:
                console.print(f"    {relay.url}: ❌ Error: {e}")

    async def _debug_balance_proofs(wallet_obj):
        """Debug balance calculation and proof validation."""
        console.print("\n[yellow]💰 Balance & Proof Validation[/yellow]")

        try:
            # Get balance without validation first (faster)
            state_unvalidated = await wallet_obj.fetch_wallet_state(check_proofs=False)
            console.print(
                f"  Raw Balance (unvalidated): {state_unvalidated.balance} sats"
            )
            console.print(f"  Raw Proof Count: {len(state_unvalidated.proofs)}")

            # Get balance with validation (slower but accurate)
            console.print("  Validating proofs with mints...")
            state_validated = await wallet_obj.fetch_wallet_state(check_proofs=True)
            console.print(f"  Validated Balance: {state_validated.balance} sats")
            console.print(f"  Valid Proof Count: {len(state_validated.proofs)}")

            # Show difference if any
            balance_diff = state_unvalidated.balance - state_validated.balance
            proof_diff = len(state_unvalidated.proofs) - len(state_validated.proofs)

            if balance_diff > 0 or proof_diff > 0:
                console.print("  [red]⚠️  Found spent/invalid proofs:[/red]")
                console.print(f"    Lost Balance: {balance_diff} sats")
                console.print(f"    Invalid Proofs: {proof_diff}")
            else:
                console.print("  [green]✅ All proofs valid[/green]")

            # Show proof breakdown by mint
            if state_validated.proofs:
                console.print("\n  Proof Breakdown by Mint:")
                proofs_by_mint = {}
                for proof in state_validated.proofs:
                    mint_url = proof.get("mint", "unknown")
                    if mint_url not in proofs_by_mint:
                        proofs_by_mint[mint_url] = []
                    proofs_by_mint[mint_url].append(proof)

                for mint_url, mint_proofs in proofs_by_mint.items():
                    mint_balance = sum(p["amount"] for p in mint_proofs)
                    denominations = {}
                    for proof in mint_proofs:
                        amount = proof["amount"]
                        denominations[amount] = denominations.get(amount, 0) + 1

                    denom_str = ", ".join(
                        f"{amount}×{count}"
                        for amount, count in sorted(denominations.items())
                    )
                    console.print(
                        f"    {mint_url}: {mint_balance} sats ({len(mint_proofs)} proofs: {denom_str})"
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

                # Check cache status
                is_cached, cached_state = wallet_obj._is_proof_state_cached(proof_id)
                cache_status = f"cached ({cached_state})" if is_cached else "not cached"

                console.print(
                    f"    {i + 1}. {proof['amount']} sats from {mint_url[:30]}..."
                )
                console.print(f"       ID: {proof['id'][:16]}...")
                console.print(f"       Secret: {proof['secret'][:16]}...")
                console.print(f"       Cache: {cache_status}")

        except Exception as e:
            console.print(f"  ❌ Proof state error: {e}")

    async def _debug_history_decryption(wallet_obj):
        """Debug history decryption issues."""
        import json

        console.print("\n[yellow]📊 History Decryption Analysis[/yellow]")

        try:
            # Get all wallet events to find different keys
            relays = await wallet_obj._get_relay_connections()
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
                    decrypted = wallet_obj._nip44_decrypt(event["content"])
                    wallet_data = json.loads(decrypted)

                    for item in wallet_data:
                        if item[0] == "privkey":
                            wallet_keys.add(item[1])
                            break
                except Exception:
                    continue

            console.print(f"  Unique Private Keys Found: {len(wallet_keys)}")
            console.print(
                f"  Current Key: {wallet_obj.wallet_privkey[:8]}...{wallet_obj.wallet_privkey[-8:]}"
            )

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
                        decrypted = wallet_obj._nip44_decrypt(event["content"])
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
                        console.print(
                            f"    {i + 1}. ✅ Success: {direction} {amount} sats"
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


if __name__ == "__main__":
    cli()
