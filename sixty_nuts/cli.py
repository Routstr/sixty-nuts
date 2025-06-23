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


def display_qr_code(
    data: str, title: str = "QR Code", *, animate_large: bool = False
) -> None:
    """Display QR code in terminal if qrcode library is available.

    Args:
        data: Data to encode in QR code
        title: Title for the QR code display
        animate_large: If True, animate large QR codes that don't fit in terminal
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

        elif animate_large:
            # QR code is too big, animate it in chunks
            console.print(
                f"[cyan]📱 {title} (Animated - QR too large for terminal)[/cyan]"
            )
            console.print("[dim]📱 Cycling through QR code sections...[/dim]")
            _animate_large_qr(matrix, max_display_width, max_display_height)

        else:
            # QR code is too big but animation not requested
            console.print(
                f"[yellow]⚠️ QR code too large for terminal ({qr_width}x{display_height})[/yellow]"
            )
            console.print(
                f"[dim]Terminal size: {max_display_width}x{max_display_height}[/dim]"
            )
            console.print("[dim]💡 Use --animate or try on mobile[/dim]")

    except Exception as e:
        console.print(f"[dim]⚠️ Could not generate QR code: {e}[/dim]")


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


def _animate_large_qr(
    matrix: list[list[bool]], max_width: int, max_height: int
) -> None:
    """Animate a large QR code by showing different sections."""
    qr_width = len(matrix[0])
    qr_height = len(matrix)

    # Calculate how many chunks we need
    chunks_x = (qr_width + max_width - 1) // max_width
    chunks_y = (qr_height + max_height * 2 - 1) // (
        max_height * 2
    )  # *2 because we use half-blocks

    total_chunks = chunks_x * chunks_y

    if total_chunks <= 1:
        # Only one chunk, display normally
        qr_text = _matrix_to_text(matrix)
        console.print(
            Panel(
                qr_text.rstrip(),
                title="[cyan]📱 QR Code[/cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
        return

    console.print(
        f"[dim]Showing {total_chunks} sections, cycling every 2 seconds...[/dim]"
    )
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        cycle_count = 0
        while cycle_count < 3:  # Cycle 3 times, then stop
            for chunk_y in range(chunks_y):
                for chunk_x in range(chunks_x):
                    # Clear previous display (simple approach)
                    console.clear()

                    # Calculate chunk boundaries
                    start_x = chunk_x * max_width
                    end_x = min(start_x + max_width, qr_width)
                    start_y = chunk_y * max_height * 2
                    end_y = min(start_y + max_height * 2, qr_height)

                    # Extract chunk from matrix
                    chunk_matrix = []
                    for y in range(start_y, end_y):
                        if y < len(matrix):
                            chunk_matrix.append(matrix[y][start_x:end_x])
                        else:
                            # Pad with False if we're beyond matrix bounds
                            chunk_matrix.append([False] * (end_x - start_x))

                    # Convert chunk to text
                    chunk_text = _matrix_to_text(chunk_matrix)

                    # Display chunk with position info
                    chunk_title = (
                        f"QR Section {chunk_y * chunks_x + chunk_x + 1}/{total_chunks}"
                    )
                    console.print(
                        Panel(
                            chunk_text.rstrip(),
                            title=f"[cyan]📱 {chunk_title}[/cyan]",
                            border_style="cyan",
                            padding=(0, 1),
                        )
                    )

                    # Show position indicator
                    progress = "█" * (chunk_y * chunks_x + chunk_x + 1) + "░" * (
                        total_chunks - chunk_y * chunks_x - chunk_x - 1
                    )
                    console.print(f"[dim]Position: {progress}[/dim]")

                    time.sleep(2.0)  # Wait 2 seconds before next chunk

            cycle_count += 1
            if cycle_count < 3:
                console.print(f"[dim]\n🔄 Cycle {cycle_count + 1}/3 starting...[/dim]")
                time.sleep(1.0)

    except KeyboardInterrupt:
        console.print("\n[dim]Animation stopped.[/dim]")

    console.print("\n[dim]📱 Scan any section with your wallet app[/dim]")


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
) -> None:
    """Check wallet balance."""

    async def _balance() -> None:
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print("[blue]Checking balance...[/blue]")

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
    animate: Annotated[
        bool,
        typer.Option("--animate", help="Animate large QR codes (cycling GIF-style)"),
    ] = False,
) -> None:
    """Create a Cashu token to send."""

    async def _send():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
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
                display_qr_code(
                    token, f"Cashu Token ({amount} sats)", animate_large=animate
                )
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
) -> None:
    """Redeem a Cashu token into wallet."""

    async def _redeem():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print("[blue]Redeeming token...[/blue]")

            # Check balance before
            balance_before = await wallet.get_balance()

            amount, unit = await wallet.redeem(token, auto_swap=auto_swap)

            console.print(f"[green]✅ Successfully redeemed {amount} {unit}![/green]")

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
def lnurl_pay(
    lnurl: Annotated[str, typer.Argument(help="LNURL or Lightning address")],
    amount: Annotated[int, typer.Argument(help="Amount to send in sats")],
    mint_urls: Annotated[
        Optional[list[str]], typer.Option("--mint", "-m", help="Mint URLs")
    ] = None,
) -> None:
    """Send to LNURL or Lightning address."""

    async def _lnurl_pay():
        nsec = get_nsec()
        async with Wallet(nsec=nsec, mint_urls=mint_urls) as wallet:
            console.print(f"[blue]Sending {amount} sats to {lnurl}...[/blue]")

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

            actual_paid = await wallet.send_to_lnurl(lnurl, amount)

            console.print("[green]✅ Successfully sent![/green]")
            console.print(f"Total paid (including fees): {actual_paid} sats")

            # Show remaining balance
            balance = await wallet.get_balance()
            console.print(f"Remaining balance: {balance} sats")

    try:
        asyncio.run(_lnurl_pay())
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
def redeem_to_ln(
    token: Annotated[str, typer.Argument(help="Cashu token to redeem")],
    lnurl: Annotated[str, typer.Argument(help="LNURL or Lightning address to send to")],
) -> None:
    """Redeem token and immediately send to Lightning address."""

    async def _redeem_to_ln():
        console.print(
            "[blue]Redeeming token and sending to Lightning address...[/blue]"
        )

        amount_sent = await redeem_to_lnurl(token, lnurl)

        console.print(
            f"[green]✅ Successfully sent {amount_sent} sats to {lnurl}![/green]"
        )

    try:
        asyncio.run(_redeem_to_ln())
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


@app.command()
def clear_nsec() -> None:
    """Clear stored NSEC from file."""
    if NSEC_FILE.exists():
        NSEC_FILE.unlink()
        console.print(f"[green]Cleared NSEC from {NSEC_FILE}[/green]")
    else:
        console.print("[yellow]No stored NSEC file found[/yellow]")

    if os.getenv(NSEC_ENV_VAR):
        console.print(
            f"[yellow]Environment variable {NSEC_ENV_VAR} is still set[/yellow]"
        )
        console.print("Unset it with: unset SIXTY_NUTS_NSEC")


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


def cli() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    cli()
