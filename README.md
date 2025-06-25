# Sixty Nuts - A NIP-60 Cashu Wallet in Python

A lightweight, stateless Cashu wallet implementation following [NIP-60](https://github.com/nostr-protocol/nips/blob/master/60.md) specification for Nostr-based wallet state management.

## Features

- **NIP-60 Compliant**: Full implementation of the NIP-60 specification
- **NIP-44 Encryption**: Secure encryption using the NIP-44 v2 standard
- **Stateless Design**: Wallet state stored on Nostr relays
- **Multi-Mint Support**: Can work with multiple Cashu mints
- **Async/Await**: Modern Python async implementation
- **LNURL Support**: Send to Lightning Addresses and other LNURL formats

## Installation

```bash
pip install sixty-nuts
```

## Usage

### Basic Setup

```python
import asyncio
from sixty_nuts import Wallet

async def main():
    # Create wallet with private key (hex or nsec format)
    wallet = await Wallet.create(
        nsec="your_nostr_private_key_hex",  # or "nsec1..." bech32 format
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        relays=["wss://relay.damus.io", "wss://nostr.wine"]
    )
    
    # Or use context manager for automatic cleanup
    async with Wallet(
        nsec="your_nostr_private_key_hex",
        mint_urls=["https://mint.minibits.cash/Bitcoin"]
    ) as wallet:
        # Wallet operations here
        pass

asyncio.run(main())
```

### Temporary Wallets (No NSEC Required)

If you need a wallet with ephemeral keys that are not stored anywhere, use `TempWallet`:

```python
import asyncio
from sixty_nuts import TempWallet

async def main():
    # Create a temporary wallet with auto-generated keys
    # No NSEC required - keys are generated randomly
    async with TempWallet(
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        currency="sat"
    ) as wallet:
        # Use the wallet normally
        state = await wallet.fetch_wallet_state()
        print(f"Balance: {state.balance} sats")
        
        # The private key is not stored anywhere
        # When the wallet is closed, the keys are lost forever
    
    # Alternative creation methods:
    temp_wallet = TempWallet()  # Uses default mint
    
    # Or with async factory
    temp_wallet2 = await TempWallet.create(
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        auto_init=False  # Skip relay connections
    )
    
    await temp_wallet.aclose()
    await temp_wallet2.aclose()

asyncio.run(main())
```

**Note**: TempWallet is useful for:

- One-time operations where you don't need to persist the wallet
- Testing and development
- Privacy-focused applications where keys should be ephemeral
- Scenarios where you want to receive/send tokens without storing keys

### Minting Tokens (Receiving via Lightning)

```python
async def mint_tokens(wallet: Wallet):
    # Create a Lightning invoice for 1000 sats
    invoice, payment_confirmation = await wallet.mint_async(1000)
    
    print(f"Pay this Lightning invoice: {invoice}")
    print("Waiting for payment...")
    
    # Wait for payment (with 5 minute timeout)
    paid = await payment_confirmation
    
    if paid:
        print("Payment received! Tokens minted.")
        # Check wallet balance
        state = await wallet.fetch_wallet_state()
        print(f"New balance: {state.balance} sats")
    else:
        print("Payment timed out")
```

### Sending Tokens

```python
async def send_tokens(wallet: Wallet):
    # Send 100 sats as a Cashu token
    amount = 100
    
    # Check balance first
    state = await wallet.fetch_wallet_state()
    print(f"Current balance: {state.balance} sats")
    
    if state.balance >= amount:
        # Create a Cashu token
        token = await wallet.send(amount)
        print(f"Send this token to recipient: {token}")
        
        # Check new balance
        new_state = await wallet.fetch_wallet_state()
        print(f"New balance: {new_state.balance} sats")
```

### Receiving Tokens

```python
async def receive_tokens(wallet: Wallet):
    # Redeem a received Cashu token
    token = "cashuA..."  # Token received from someone
    
    try:
        await wallet.redeem(token)
        print("Token redeemed successfully!")
        
        # Check new balance
        state = await wallet.fetch_wallet_state()
        print(f"New balance: {state.balance} sats")
    except Exception as e:
        print(f"Failed to redeem token: {e}")
```

### Paying Lightning Invoices (Melting)

```python
async def pay_invoice(wallet: Wallet):
    # Pay a Lightning invoice using your tokens
    invoice = "lnbc..."  # Lightning invoice to pay
    
    try:
        await wallet.melt(invoice)
        print("Invoice paid successfully!")
        
        # Check remaining balance
        state = await wallet.fetch_wallet_state()
        print(f"Remaining balance: {state.balance} sats")
    except Exception as e:
        print(f"Payment failed: {e}")
```

### Checking Wallet State

```python
async def check_wallet(wallet: Wallet):
    # Fetch current wallet state from Nostr relays
    state = await wallet.fetch_wallet_state()
    
    print(f"Balance: {state.balance} sats")
    print(f"Number of proofs: {len(state.proofs)}")
    print(f"Connected mints: {list(state.mint_keysets.keys())}")
    
    # Show proof denominations
    denominations = {}
    for proof in state.proofs:
        amount = proof["amount"]
        denominations[amount] = denominations.get(amount, 0) + 1
    
    print("Denominations:")
    for amount, count in sorted(denominations.items()):
        print(f"  {amount} sat: {count} proof(s)")
```

### Sending to Lightning Addresses (LNURL)

```python
async def send_to_lightning_address(wallet: Wallet):
    # Send to a Lightning Address (user@domain.com format)
    lnurl = "satoshi@bitcoin.org"
    amount = 500  # sats
    
    try:
        paid_amount = await wallet.send_to_lnurl(lnurl, amount)
        print(f"Successfully sent {paid_amount} sats to {lnurl}")
    except Exception as e:
        print(f"Failed to send: {e}")
    
    # You can also send to other LNURL formats:
    # - Bech32 encoded: "LNURL1DP68GURN8GHJ7..."
    # - With prefix: "lightning:user@domain.com"
    # - Direct URL: "https://lnurl.service.com/pay/..."
    
    # Custom fee parameters
    await wallet.send_to_lnurl(
        lnurl,
        amount=1000,
        fee_estimate=0.02,  # 2% fee estimate
        max_fee=50,         # Maximum 50 sats fee
    )
```

### Complete Example

```python
import asyncio
from sixty_nuts import Wallet

async def example_wallet_operations():
    # Initialize wallet
    async with Wallet(
        nsec="your_nostr_private_key_hex",
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        currency="sat"
    ) as wallet:
        # Check initial balance
        state = await wallet.fetch_wallet_state()
        print(f"Initial balance: {state.balance} sats")
        
        # Mint some tokens
        if state.balance < 1000:
            invoice, task = await wallet.mint_async(1000)
            print(f"Pay invoice to add funds: {invoice}")
            paid = await task
            if paid:
                print("Funded!")
        
        # Send tokens
        if state.balance >= 100:
            token = await wallet.send(100)
            print(f"Token to share: {token}")
        
        # Send to Lightning Address
        if state.balance >= 500:
            await wallet.send_to_lnurl("user@ln.tips", 500)
            print("Sent to Lightning Address!")
        
        # Final balance
        final_state = await wallet.fetch_wallet_state()
        print(f"Final balance: {final_state.balance} sats")

if __name__ == "__main__":
    asyncio.run(example_wallet_operations())
```

## Architecture

- `wallet.py` - Main wallet implementation
- `crypto.py` - Cryptographic primitives (BDHKE and NIP-44 v2 encryption)
- `mint.py` - Cashu mint API client
- `relay.py` - Nostr relay WebSocket client
- `lnurl.py` - LNURL protocol support for Lightning Address payments

## TODO

### Core Implementation

- [ ] **Proof-to-Event-ID Mapping**: Implement proper mapping between proofs and their containing event IDs (wallet.py:66)
  - Currently missing in `WalletState` dataclass
  - Required for accurate token event management and proper deletion when proofs are spent
  
- [ ] **Quote Tracking (NIP-60)**: Implement quote tracking as per NIP-60 specification (wallet.py:776)
  - Need to publish kind 7374 events for mint quotes
  - Track quote expiration and status
  
- [ ] **Minted Quote Tracking**: Properly track minted quotes to avoid double-minting (wallet.py:806)
  - Maintain state of which quotes have been successfully minted
  - Check existing token events for quote IDs in tags
  
- [ ] **Coin Selection Algorithm**: Implement better coin selection algorithm (wallet.py:956)
  - Current implementation is naive (first-fit)
  - Should optimize for privacy and minimize number of proofs used

### Security & Cryptography

- [ ] Implement proper BDHKE blinding for Cashu operations
- [ ] Improve proof tracking to correctly identify which proofs belong to which token events (wallet.py:1031)

### Features (todo)

- [ ] Support for P2PK ecash (NIP-61)
- [ ] Add comprehensive test suite
- [ ] Implement wallet recovery from relay state
- [ ] Add multi-mint transaction support

## License

MIT

## Testing

This project has comprehensive test coverage with both unit tests and integration tests.

### Running Unit Tests

Unit tests don't require external services and run quickly:

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run specific test file
pytest tests/unit/test_wallet.py -v

# Run with coverage
pytest tests/unit/ --cov=sixty_nuts --cov-report=html
```

### Running Integration Tests

Integration tests verify complete wallet functionality against real services. They require Docker to run fresh mint and relay instances.

#### Method 1: Automated Script (Recommended)

```bash
# Run the integration tests with docker compose
python tests/run_integration.py
```

This script will:

1. Start fresh Docker containers (Cashu mint + Nostr relay)
2. Wait for services to be ready
3. Run all integration tests
4. Clean up containers afterward

#### Method 2: Manual Control

```bash
# Start Docker services
docker-compose up -d

# Run integration tests (in another terminal)
RUN_INTEGRATION_TESTS=1 pytest tests/integration/ -v

# Clean up
docker-compose down -v
```

### Test Architecture

- **Unit Tests** (`tests/unit/`): Fast, isolated tests with no external dependencies
- **Integration Tests** (`tests/integration/`): End-to-end tests against real services
- **Environment Variable Gating**: Integration tests only run when `RUN_INTEGRATION_TESTS=1`
- **Fresh State**: Each integration test run uses clean Docker containers

### Test Categories

Integration tests cover:

- **Basic Operations**: Wallet creation, initialization, balance checking
- **Minting**: Lightning invoice creation and payment handling  
- **Transactions**: Sending and redeeming tokens with fee handling
- **Proof Management**: Validation, consolidation, and state management
- **Multi-mint**: Operations across different mint instances
- **Error Handling**: Edge cases and failure scenarios

### CI/CD Integration

```yaml
# Example GitHub Actions workflow
- name: Run Unit Tests
  run: pytest tests/unit/ -v

- name: Run Integration Tests  
  run: |
    docker-compose up -d
    RUN_INTEGRATION_TESTS=1 pytest tests/integration/ -v
    docker-compose down -v
```

## Development

### Project Structure

```
sixty_nuts/
├── __init__.py          # Package initialization
├── wallet.py            # Main wallet implementation
├── mint.py              # Cashu mint client
├── relay.py             # Nostr relay client  
├── crypto.py            # Cryptographic primitives
├── events.py            # NIP-60 event management
├── types.py             # Type definitions
└── cli.py               # Command-line interface

tests/
├── unit/                # Unit tests (fast, no external deps)
├── integration/         # Integration tests (require Docker)
└── conftest.py          # Pytest configuration

scripts/
└── run_integration_tests.py  # Integration test orchestration
```

### Adding Features

1. **Write unit tests first** for new functionality
2. **Add integration tests** for end-to-end flows
3. **Update type hints** for all new code
4. **Follow NIP-60 specification** for Nostr integration
5. **Maintain backward compatibility** for existing APIs

### Code Quality

```bash
# Type checking
mypy sixty_nuts/

# Linting  
ruff check sixty_nuts/

# Formatting
ruff format sixty_nuts/
```

## Architecture

### NIP-60 Implementation

- **Wallet Events**: Metadata stored in replaceable Nostr events (kind 17375)
- **Token Events**: Ecash proofs stored in Nostr events (kind 7375)
- **History Events**: Optional transaction history (kind 7376)
- **Event Encryption**: All sensitive data encrypted with NIP-44

### Multi-mint Strategy

- **Primary Mint**: Default mint for new operations
- **Auto-swapping**: Tokens from untrusted mints automatically swapped to primary
- **Fee Optimization**: Intelligent proof selection to minimize fees

### Proof Management

- **State Validation**: Proofs validated against mint before use
- **Denomination Optimization**: Automatic proof splitting for optimal denominations
- **Backup & Recovery**: All proofs backed up to Nostr relays

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [Cashu Protocol](https://cashu.space) - Chaumian ecash protocol
- [NIP-60](https://github.com/nostr-protocol/nips/blob/master/60.md) - Cashu Wallet specification
- [Nostr Protocol](https://nostr.com) - Decentralized social protocol
- [Routstr](https://github.com/routstr) - Decentralized llm routing protocol
