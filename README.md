# Sixty Nuts - A NIP-60 Cashu Wallet in Python

A lightweight, stateless Cashu wallet implementation following [NIP-60](https://github.com/nostr-protocol/nips/blob/master/60.md) specification for Nostr-based wallet state management.

## Features

- **NIP-60 Compliant**: Full implementation of the NIP-60 specification for Cashu wallet state management
- **NIP-44 Encryption**: Secure encryption using the NIP-44 v2 standard for all sensitive data
- **Stateless Design**: Wallet state stored on Nostr relays with automatic synchronization
- **Multi-Mint Support**: Seamlessly work with multiple Cashu mints with automatic token swapping
- **Modern Python**: Async/await implementation with full type hints (Python 3.11+)
- **LNURL Support**: Send to Lightning Addresses and other LNURL formats
- **CLI Interface**: Full-featured command-line interface for all wallet operations
- **Temporary Wallets**: Ephemeral wallets for one-time operations without key storage
- **Auto-Discovery**: Automatic relay and mint discovery with intelligent caching
- **QR Code Support**: Built-in QR code generation for invoices and tokens

## Installation

```bash
pip install sixty-nuts
```

For QR code support in the CLI:

```bash
pip install sixty-nuts[qr]
# or
pip install qrcode
```

## Quick Start

### CLI Usage (Recommended)

The easiest way to get started is with the CLI:

```bash
# Check status and initialize if needed
nuts status

# Check balance
nuts balance

# Create Lightning invoice to add funds
nuts mint 1000

# Send tokens
nuts send 100

# Redeem received token
nuts redeem cashuA...

# Send to Lightning Address
nuts send 500 --to-lnurl user@getalby.com

# Pay Lightning invoice
nuts pay lnbc...
```

### Python API Usage

```python
import asyncio
from sixty_nuts import Wallet

async def main():
    # Create wallet with automatic relay/mint discovery
    async with Wallet(nsec="your_nsec_private_key") as wallet:
        # Check balance
        balance = await wallet.get_balance()
        print(f"Balance: {balance} sats")
        
        # Create invoice and wait for payment
        invoice, task = await wallet.mint_async(1000)
        print(f"Pay: {invoice}")
        paid = await task
        
        if paid:
            # Send tokens
            token = await wallet.send(100)
            print(f"Token: {token}")

asyncio.run(main())
```

## Command Line Interface

The `nuts` CLI provides a complete interface for wallet operations:

### Basic Commands

#### `nuts status`

Check wallet initialization status and configuration:

```bash
# Check if wallet is initialized
nuts status

# Initialize wallet if needed
nuts status --init

# Force re-initialization
nuts status --force
```

#### `nuts balance`

Check your wallet balance:

```bash
# Quick balance check
nuts balance

# Detailed breakdown by mint
nuts balance --details

# Skip proof validation for speed
nuts balance --no-validate
```

#### `nuts mint <amount>`

Create Lightning invoice to add funds:

```bash
# Mint 1000 sats
nuts mint 1000

# With custom timeout
nuts mint 1000 --timeout 600

# Without QR code display
nuts mint 1000 --no-qr
```

#### `nuts send <amount>`

Send sats as Cashu token or to Lightning Address:

```bash
# Create Cashu token
nuts send 100

# Send directly to Lightning Address
nuts send 500 --to-lnurl user@getalby.com

# Send without QR code
nuts send 100 --no-qr
```

#### `nuts redeem <token>`

Redeem received Cashu tokens:

```bash
# Redeem token to wallet
nuts redeem cashuA...

# Redeem and forward to Lightning Address
nuts redeem cashuA... --to-lnurl user@getalby.com

# Disable auto-swap from untrusted mints
nuts redeem cashuA... --no-auto-swap
```

#### `nuts pay <invoice>`

Pay Lightning invoices:

```bash
# Pay BOLT11 invoice
nuts pay lnbc...
```

### Management Commands

#### `nuts info`

Show detailed wallet information:

```bash
nuts info
```

#### `nuts history`

View transaction history:

```bash
# Show recent transactions
nuts history

# Limit number of entries
nuts history --limit 10
```

#### `nuts relays`

Manage Nostr relay configuration:

```bash
# List configured relays
nuts relays --list

# Test relay connectivity
nuts relays --test

# Discover relays from profile
nuts relays --discover

# Interactive configuration
nuts relays --configure

# Clear relay cache
nuts relays --clear-cache
```

#### `nuts cleanup`

Clean up wallet state:

```bash
# Show what would be cleaned up
nuts cleanup --dry-run

# Clean up old/corrupted events
nuts cleanup

# Skip confirmation
nuts cleanup --yes
```

#### `nuts erase`

Delete wallet data (⚠️ DANGEROUS):

```bash
# Delete wallet configuration
nuts erase --wallet

# Delete transaction history
nuts erase --history

# Delete token storage (affects balance!)
nuts erase --tokens

# Clear locally stored NSEC
nuts erase --nsec

# Nuclear option - delete everything
nuts erase --all

# Skip confirmation
nuts erase --all --yes
```

#### `nuts debug`

Debug wallet issues:

```bash
# Debug Nostr connectivity
nuts debug --nostr

# Debug balance/proof issues
nuts debug --balance

# Debug proof state
nuts debug --proofs

# Debug wallet configuration
nuts debug --wallet

# Debug history decryption
nuts debug --history
```

### Global Options

Most commands support these options:

- `--mint, -m`: Specify mint URLs
- `--help`: Show command help
- `--yes, -y`: Skip confirmations (where applicable)

### Environment Configuration

The CLI automatically manages configuration through environment variables and `.env` files:

#### Required Configuration

- `NSEC`: Your Nostr private key (nsec1... or hex format)

#### Optional Configuration

- `CASHU_MINTS`: Comma-separated list of mint URLs
- `NOSTR_RELAYS`: Comma-separated list of relay URLs

#### Example `.env` file

```bash
NSEC="nsec1your_private_key_here"
CASHU_MINTS="https://mint.minibits.cash/Bitcoin,https://mint.cubabitcoin.org"
NOSTR_RELAYS="wss://relay.damus.io,wss://nostr.wine"
```

The CLI will prompt for missing configuration and automatically cache your choices.

## Python API

### Basic Wallet Setup

```python
import asyncio
from sixty_nuts import Wallet

async def main():
    # Create wallet with explicit configuration
    wallet = await Wallet.create(
        nsec="your_nsec_private_key",  # hex or nsec1... format
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        relays=["wss://relay.damus.io", "wss://nostr.wine"]
    )
    
    # Or use context manager for automatic cleanup
    async with Wallet(nsec="your_nsec_private_key") as wallet:
        # Wallet operations here
        pass

asyncio.run(main())
```

### Temporary Wallets

For one-time operations without storing keys:

```python
import asyncio
from sixty_nuts import TempWallet

async def main():
    # Create temporary wallet with auto-generated keys
    async with TempWallet() as wallet:
        # Use wallet normally - keys are never stored
        balance = await wallet.get_balance()
        print(f"Balance: {balance} sats")
        
        # Perfect for redeeming tokens to Lightning Address
        token = "cashuA..."
        amount, unit = await wallet.redeem(token)
        await wallet.send_to_lnurl("user@getalby.com", amount)

asyncio.run(main())
```

**TempWallet Use Cases:**

- One-time token redemption
- Privacy-focused operations
- Testing and development
- Receiving tokens without account setup

### Core Operations

#### Minting (Receiving via Lightning)

```python
async def mint_tokens(wallet: Wallet):
    # Create Lightning invoice
    invoice, payment_task = await wallet.mint_async(1000)
    
    print(f"Pay: {invoice}")
    
    # Wait for payment (5 minute timeout)
    paid = await payment_task
    
    if paid:
        balance = await wallet.get_balance()
        print(f"New balance: {balance} sats")
```

#### Sending Tokens

```python
async def send_tokens(wallet: Wallet):
    # Check balance
    balance = await wallet.get_balance()
    
    if balance >= 100:
        # Create Cashu token (V4 format by default)
        token = await wallet.send(100)
        print(f"Token: {token}")
        
        # Or use V3 format for compatibility
        token_v3 = await wallet.send(100, token_version=3)
```

#### Redeeming Tokens

```python
async def redeem_tokens(wallet: Wallet):
    token = "cashuA..."  # Token from someone else
    
    try:
        amount, unit = await wallet.redeem(token)
        print(f"Redeemed: {amount} {unit}")
        
        balance = await wallet.get_balance()
        print(f"New balance: {balance}")
    except WalletError as e:
        print(f"Failed: {e}")
```

#### Lightning Payments

```python
async def pay_invoice(wallet: Wallet):
    invoice = "lnbc..."
    
    try:
        await wallet.melt(invoice)
        print("Payment successful!")
    except WalletError as e:
        print(f"Payment failed: {e}")
```

#### LNURL/Lightning Address Support

```python
async def send_to_lightning_address(wallet: Wallet):
    # Send to Lightning Address
    amount_sent = await wallet.send_to_lnurl("user@getalby.com", 1000)
    print(f"Sent: {amount_sent} sats")
    
    # Works with various LNURL formats:
    # - user@domain.com (Lightning Address)
    # - LNURL1... (bech32 encoded)
    # - lightning:user@domain.com (with prefix)
    # - https://... (direct URL)
```

### Wallet State Management

```python
async def check_wallet_state(wallet: Wallet):
    # Fetch complete wallet state
    state = await wallet.fetch_wallet_state()
    
    print(f"Balance: {state.balance} sats")
    print(f"Proofs: {len(state.proofs)}")
    print(f"Mints: {list(state.mint_keysets.keys())}")
    
    # Show denomination breakdown
    denominations = {}
    for proof in state.proofs:
        amount = proof["amount"]
        denominations[amount] = denominations.get(amount, 0) + 1
    
    for amount, count in sorted(denominations.items()):
        print(f"  {amount} sat: {count} proof(s)")
```

### Complete Example

```python
import asyncio
from sixty_nuts import Wallet

async def complete_example():
    async with Wallet(nsec="your_nsec") as wallet:
        # Initialize wallet events if needed
        await wallet.initialize_wallet()
        
        # Check initial state
        balance = await wallet.get_balance()
        print(f"Starting balance: {balance} sats")
        
        # Mint tokens if balance is low
        if balance < 1000:
            invoice, task = await wallet.mint_async(1000)
            print(f"Pay: {invoice}")
            
            if await task:
                print("Payment received!")
        
        # Send some tokens
        token = await wallet.send(100)
        print(f"Created token: {token}")
        
        # Send to Lightning Address
        await wallet.send_to_lnurl("user@getalby.com", 200)
        print("Sent to Lightning Address!")
        
        # Final balance
        final_balance = await wallet.get_balance()
        print(f"Final balance: {final_balance} sats")

if __name__ == "__main__":
    asyncio.run(complete_example())
```

## Architecture

### NIP-60 Implementation

Sixty Nuts implements the complete NIP-60 specification:

- **Wallet Events** (kind 17375): Encrypted wallet metadata and configuration
- **Token Events** (kind 7375): Encrypted Cashu proof storage with rollover support
- **History Events** (kind 7376): Optional encrypted transaction history
- **Delete Events** (kind 5): Proper event deletion with relay compatibility

### Multi-Mint Strategy

- **Primary Mint**: Default mint for operations
- **Auto-Swapping**: Automatic token swapping from untrusted mints
- **Fee Optimization**: Intelligent proof selection to minimize transaction fees
- **Denomination Management**: Automatic proof splitting for optimal denominations

### Proof Management

- **State Validation**: Real-time proof validation with mint connectivity
- **Caching System**: Smart caching to avoid re-validating spent proofs
- **Backup & Recovery**: Automatic proof backup to Nostr relays with local fallback
- **Consolidation**: Automatic cleanup of fragmented wallet state

### Security Features

- **NIP-44 Encryption**: All sensitive data encrypted using NIP-44 v2
- **Key Separation**: Separate keys for Nostr identity and P2PK ecash operations  
- **Local Backup**: Automatic local proof backup before Nostr operations
- **State Validation**: Cryptographic proof validation before operations

## Development

### Project Structure

```
sixty_nuts/
├── __init__.py          # Package exports
├── wallet.py            # Main Wallet class implementation
├── temp.py              # TempWallet for ephemeral operations
├── mint.py              # Cashu mint API client
├── relay.py             # Nostr relay WebSocket client
├── events.py            # NIP-60 event management
├── crypto.py            # Cryptographic primitives (BDHKE, NIP-44)
├── lnurl.py             # LNURL protocol support
├── types.py             # Type definitions and errors
└── cli.py               # Command-line interface

tests/
├── unit/                # Unit tests (fast, no external deps)
├── integration/         # Integration tests (require Docker)
└── run_integration.py   # Integration test orchestration

examples/
├── basic_operations.py  # Basic wallet operations
├── multi_mint.py        # Multi-mint examples
├── lnurl_operations.py  # LNURL examples
└── one_off_redeem.py    # TempWallet examples
```

### Running Tests

#### Unit Tests

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ --cov=sixty_nuts --cov-report=html
```

#### Integration Tests

```bash
# Automated with Docker
python tests/run_integration.py

# Manual control
docker-compose up -d
RUN_INTEGRATION_TESTS=1 pytest tests/integration/ -v
docker-compose down -v
```

### Code Quality

```bash
# Type checking
mypy sixty_nuts/

# Linting  
ruff check sixty_nuts/

# Formatting
ruff format sixty_nuts/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

Please follow the existing code style and add comprehensive tests for new features.

## Implementation Status

### ✅ Completed Features

- [x] NIP-60 wallet state management
- [x] NIP-44 v2 encryption for all sensitive data
- [x] Multi-mint support with automatic swapping
- [x] Lightning invoice creation and payment
- [x] Cashu token sending and receiving (V3/V4 formats)
- [x] LNURL/Lightning Address support
- [x] Proof validation and state management
- [x] Automatic relay discovery and caching
- [x] Complete CLI interface
- [x] TempWallet for ephemeral operations
- [x] Automatic proof consolidation
- [x] Transaction history tracking
- [x] QR code generation
- [x] Comprehensive error handling

### 🚧 Work in Progress

- [ ] **P2PK Ecash Support** (NIP-61): Partially implemented
- [ ] **Quote Tracking**: Implement full NIP-60 quote tracking (kind 7374)
- [ ] **Multi-Mint Transactions**: Atomic operations across multiple mints
- [ ] **Advanced Coin Selection**: Privacy-optimized proof selection algorithms
- [ ] **Offline Operations**: Enhanced offline capability with delayed sync

## Troubleshooting

### Common Issues

#### "No mint URLs configured"

```bash
# Set mint URLs via environment
export CASHU_MINTS="https://mint.minibits.cash/Bitcoin"

# Or use CLI to select
nuts status --init
```

#### "Could not connect to relays"

```bash
# Test relay connectivity
nuts debug --nostr

# Configure relays manually
nuts relays --configure
```

#### "Insufficient balance"

```bash
# Check actual balance with validation
nuts balance --validate

# Clean up corrupted state
nuts cleanup
```

#### QR codes not displaying

```bash
# Install QR code support
pip install qrcode

# Disable QR codes
nuts mint 1000 --no-qr
```

### Debug Commands

The CLI provides extensive debugging capabilities:

```bash
# Comprehensive wallet debugging
nuts debug --wallet --nostr --balance --proofs

# Debug specific issues
nuts debug --history  # Transaction history issues
nuts debug --balance   # Balance calculation issues
nuts debug --proofs    # Proof validation issues
```

## Related Projects

- [Cashu Protocol](https://cashu.space) - Chaumian ecash protocol specification
- [NIP-60](https://github.com/nostr-protocol/nips/blob/master/60.md) - Cashu Wallet specification
- [NIP-44](https://github.com/nostr-protocol/nips/blob/master/44.md) - Encryption specification
- [Nostr Protocol](https://nostr.com) - Decentralized communication protocol

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/your-org/sixty-nuts/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/sixty-nuts/discussions)
- **Nostr**: Follow development updates via Nostr

---

**⚡ Start using Cashu with Nostr today!**

```bash
pip install sixty-nuts
nuts status --init
nuts mint 1000
```
