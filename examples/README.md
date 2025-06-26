# Sixty Nuts Examples

This directory contains example scripts demonstrating the core features of the sixty_nuts library. Each example is focused on a specific use case and designed to be educational and easy to understand.

## Prerequisites

Before running the examples, install the dependencies:

```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

## Basic Wallet Operations

### mint_and_send.py

Shows the basic flow of minting tokens from Lightning and sending Cashu tokens.

```bash
python mint_and_send.py
```

### check_balance_and_proofs.py

Check wallet balance with detailed breakdown by mint and denomination.

```bash
python check_balance_and_proofs.py
```

### redeem_token.py

Simple token redemption into your wallet.

```bash
python redeem_token.py cashuAey...
```

### send_to_lightning_address.py

Send tokens to any Lightning Address (LNURL).

```bash
python send_to_lightning_address.py user@getalby.com 1000
```

## Payment Processing

### merchant_accept_token.py

Accept tokens from customers and automatically swap from untrusted mints.

```bash
python merchant_accept_token.py cashuAey...
```

### monitor_payments.py

Create Lightning invoices and monitor for payment in real-time.

```bash
python monitor_payments.py
```

### validate_token.py

Validate tokens before accepting them - essential for merchants.

```bash
python validate_token.py cashuAey...
```

## Token Management

### split_tokens.py

Split tokens into specific denominations for payments or privacy.

```bash
# Create multiple specific amounts
python split_tokens.py 100 50 25 10

# Create one exact amount
python split_tokens.py 137
```

### refresh_proofs.py

Refresh all proofs for privacy - swaps old proofs for new optimized ones.

```bash
python refresh_proofs.py
```

## Multi-Mint Operations

### multi_mint_operations.py

Work with multiple mints and check balances per mint.

```bash
python multi_mint_operations.py
```

## Advanced Features

### one_off_redeem.py

Redeem tokens without storing them in a persistent wallet.

```bash
# Redeem to temporary wallet
python one_off_redeem.py cashuAey...

# Redeem and forward to Lightning address
python one_off_redeem.py user@getalby.com cashuAey...
```

### clear_wallet.py

Empty your wallet by converting all tokens back to Lightning.

```bash
python clear_wallet.py
```

### recovery_tool.py

Demonstrates wallet recovery from Nostr relays using just your private key.

```bash
python recovery_tool.py
```

### queued_relay_demo.py

Shows how queued relays improve performance and reliability.

```bash
python queued_relay_demo.py
```

## Getting Started

1. **First time users**: Start with `mint_and_send.py` to understand the basic flow
2. **Merchants**: Check out `merchant_accept_token.py` and `validate_token.py`
3. **Privacy users**: Try `refresh_proofs.py` and `split_tokens.py`
4. **Advanced users**: Explore `multi_mint_operations.py` and `recovery_tool.py`

## Important Notes

- All examples use the same demo private key for consistency
- Replace the `nsec` with your own for production use
- Some examples require command-line arguments - run without args to see usage
- Examples demonstrate best practices and proper error handling

## Troubleshooting

### No Balance

If you see zero balance, you need to fund your wallet first:

```bash
python mint_and_send.py  # Creates Lightning invoice to fund wallet
```

### Validation Errors

If tokens fail validation:

```bash
python validate_token.py <your_token>  # Check token validity
```

### Missing Funds

If you're missing funds after operations:

```bash
python recovery_tool.py  # Shows recovery information
```

## Common Patterns

All examples follow these patterns:

- **Async/Await**: All operations use Python's async/await
- **Context Managers**: Use `async with Wallet(...)` for proper cleanup
- **Error Handling**: Proper exception handling with user-friendly messages
- **Type Hints**: Full type annotations for better IDE support
